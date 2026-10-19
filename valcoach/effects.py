"""Source-backed smoke lifecycles and player visual-effect observations.

Actor lifetimes are not opaque-smoke intervals. Visual effects are evidence of
status application, not authoritative gameplay flags or flash-strength values.
"""

from collections import defaultdict
import json
import math

from .source import provenance, value

UTILITY_VERSION = 1
SMOKE_CLASSES = {
    "GameObject_Wushu_4_SmokeZone_C": "Jett Cloudburst",
    "GameObject_Smonk_NewSmoke_C": "Clove smoke",
    "GameObject_Smonk_NewSmoke_PDS_C": "Clove post-death smoke",
    "GameObject_Iris_E_Smoke_C": "Miks smoke",
}
STATUS_CLASSES = {
    "FXC_Phoenix_E_Blinded_Production_C": "blind_visual",
    "FXC_Wushu_4_SmokeNearsight_C": "smoke_nearsight_visual",
    "FXC_Global_ConcussedWavy_Prototype_C": "concussion_visual",
    "FXC_Vampire_4_NearsightAOE_Nearsight_C": "nearsight_visual",
    "FXC_Wraith_Q_NearsightMissile_Nearsight_C": "nearsight_visual",
    "FXC_Deadeye_4_Trap_Slowed_C": "slow_visual",
    "FXC_Aggrobot_X_DetainDebuff_C": "detain_visual",
    "FXC_Grenadier_Player_Suppressed_C": "suppression_visual",
}


def class_leaf(path):
    return (path or "").rsplit("/", 1)[-1].rsplit(".", 1)[-1]


def finite_position(values):
    return (len(values) == 3 and all(isinstance(n, (int, float)) and not isinstance(n, bool)
                                   and math.isfinite(n) for n in values))


def smoke_instances(actors):
    """A dormant actor remains alive; GUID reuse cannot silently extend it."""
    records, active = [], {}
    for row in sorted(actors, key=lambda r: (r["time_ms"], r["source_row"])):
        actor = row["actor_net_guid"]
        if row["event"] == "open":
            if actor in active:
                active.pop(actor)["scope_end_ms"] = row["time_ms"]
            leaf = class_leaf(row.get("class_path"))
            if leaf not in SMOKE_CLASSES:
                continue
            position = [row.get("spawn_" + axis) for axis in "xyz"]
            record = {"id": f"smoke:actors:{row['source_row']}", "actor": actor,
                      "label": SMOKE_CLASSES[leaf], "class_path": row["class_path"],
                      "start_ms": row["time_ms"], "end_ms": None, "scope_end_ms": None,
                      "start_source": provenance(row, "actors"), "end_source": None,
                      "owner": None, "owner_source": None, "owner_time_ms": None, "radius_cm": None,
                      "opaque_start_ms": None, "opaque_end_ms": None,
                      "positions": [{"time_ms": row["time_ms"], "position_cm": position,
                                     "source": provenance(row, "actors"), "method": "actor_spawn"}]
                                   if finite_position(position) else [],
                      "position_updates_unresolved": 0}
            records.append(record)
            active[actor] = record
        elif row["event"] == "close" and actor in active:
            record = active.pop(actor)
            record["end_ms"], record["end_source"] = row["time_ms"], provenance(row, "actors")
    return records


def _smoke_fields(smokes, rows, pawns):
    by_actor = defaultdict(list)
    for smoke in smokes:
        by_actor[smoke["actor"]].append(smoke)
    for row in rows:
        for smoke in by_actor.get(row["actor_net_guid"], []):
            ends = [t for t in (smoke["end_ms"], smoke["scope_end_ms"]) if t is not None]
            end = min(ends) if ends else None
            if row["time_ms"] < smoke["start_ms"] or (end is not None and row["time_ms"] >= end):
                continue
            if row["field_name"] == "Instigator":
                owner = value(row)
                smoke["owner"] = owner if owner in pawns else None
                smoke["owner_source"] = provenance(row)
                smoke["owner_time_ms"] = row["time_ms"]
            elif row["field_name"] == "ReplicatedMovement":
                try:
                    data = json.loads(row["value_str"])
                    point = [data["location"][axis] for axis in "xyz"]
                    if not finite_position(point):
                        raise ValueError("Nonfinite position")
                except (TypeError, ValueError, KeyError):
                    smoke["position_updates_unresolved"] += 1
                    continue
                smoke["positions"].append({"time_ms": row["time_ms"], "position_cm": point,
                                           "source": provenance(row), "method": "replicated_movement_world_cm"})
    for smoke in smokes:
        smoke["positions"].sort(key=lambda p: (p["time_ms"], p["source"]["row"]))


def effect_calls(rows):
    """Split repeated RPC invocations even when they share a packet and actor."""
    pending, result = None, []
    for row in sorted(rows, key=lambda r: r["source_row"]):
        name = row.get("field_name") or ""
        if not name.startswith(("MulticastPlayContinuousEffect.", "MulticastStopContinuousEffect.")):
            continue
        function, member = name.split(".", 1)
        key = (row["packet_id"], row["actor_net_guid"], row["object_net_guid"], function)
        if pending is None or pending["key"] != key or member in pending["values"]:
            pending = {"key": key, "time_ms": row["time_ms"], "actor": row["actor_net_guid"],
                       "component": row["object_net_guid"], "function": function,
                       "values": {}, "sources": []}
            result.append(pending)
        pending["values"][member] = value(row)
        pending["sources"].append(provenance(row))
    return result


def _effect_key(call):
    values = call["values"]
    required = ("EffectID", "SourceID", "bLocalEffect", "bTransient")
    if any(values.get(k) is None for k in required):
        return None
    return (call["actor"], call["component"], *(values[k] for k in required))


def status_intervals(calls, guids, boundaries):
    result, active = [], {}
    coverage = {"unmapped_effect_starts": 0, "unmatched_stops": 0, "incomplete_effect_keys": 0}
    for call in calls:
        key = _effect_key(call)
        if key is None:
            coverage["incomplete_effect_keys"] += 1
        if call["function"] == "MulticastStopContinuousEffect":
            record = active.pop(key, None) if key is not None else None
            if record is None:
                coverage["unmatched_stops"] += 1
            else:
                record["end_ms"] = call["time_ms"]
                record["end_sources"] = call["sources"]
            continue
        if key in active:
            previous = active.pop(key)
            previous["scope_end_ms"] = min(t for t in (previous["scope_end_ms"], call["time_ms"]) if t is not None)
        path = guids.get(call["values"].get("EffectContainer"))
        category = STATUS_CLASSES.get(class_leaf(path))
        if category is None:
            coverage["unmapped_effect_starts"] += 1
            continue
        bound = min((t for t in boundaries.get(call["actor"], []) if t > call["time_ms"]), default=None)
        record = {"id": f"status:fields:{call['sources'][0]['row']}", "player": call["actor"],
                  "component": call["component"], "category": category, "effect_path": path,
                  "effect_id": call["values"].get("EffectID"), "source_id": call["values"].get("SourceID"),
                  "start_ms": call["time_ms"], "end_ms": None, "scope_end_ms": bound,
                  "start_sources": call["sources"], "end_sources": [], "severity": None,
                  "interpretation": "visual_effect_observations; gameplay_state_not_authoritative"}
        result.append(record)
        if key is not None:
            active[key] = record
    coverage["unpaired_supported_starts"] = sum(r["end_ms"] is None for r in result)
    return result, coverage


def _boundaries(players, rounds, timeline, actors):
    result = defaultdict(list)
    for pawn in players:
        result[pawn].extend(r["end_ms"] for r in rounds)
    for event in timeline:
        if event["kind"] in ("death", "resurrection"):
            result[event.get("victim", event.get("player"))].append(event["time_ms"])
    for actor in actors:
        if actor["actor_net_guid"] in players and actor["event"] in ("open", "close"):
            result[actor["actor_net_guid"]].append(actor["time_ms"])
    return result


def reconstruct_utility(export, actors, players, rounds, timeline, guids):
    pawns = {p["pawn"] for p in players}
    smokes = smoke_instances(actors)
    for smoke in smokes:
        bounds = [r["end_ms"] for r in rounds if r["buy_ms"] <= smoke["start_ms"] < r["end_ms"]]
        if smoke["scope_end_ms"] is not None:
            bounds.append(smoke["scope_end_ms"])
        smoke["scope_end_ms"] = min(bounds) if bounds else None
    rows = export.utility_fields(sorted(pawns), sorted({s["actor"] for s in smokes}))
    _smoke_fields(smokes, rows, pawns)
    statuses, coverage = status_intervals(effect_calls(rows), guids, _boundaries(pawns, rounds, timeline, actors))
    blinds = [{"id": f"blind:fields:{r['source_row']}", "player": r["actor_net_guid"],
               "time_ms": r["time_ms"], "source": provenance(r),
               "status": "undecoded_blind_array_update", "severity": None, "duration_ms": None}
              for r in rows if r["field_name"] == "ActiveBlinds"]
    coverage.update({"smoke_instances": len(smokes), "status_intervals": len(statuses),
                     "raw_blind_updates": len(blinds), "supported_classes_only": True})
    return {"version": UTILITY_VERSION, "smokes": smokes, "statuses": statuses,
            "blind_updates": blinds, "coverage": coverage}
