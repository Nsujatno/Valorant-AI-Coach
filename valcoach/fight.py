"""Compact fight investigation over the existing round and spatial cores."""

from dataclasses import asdict, dataclass

from .equipment import latest_equipment
from .query import alive, query_round
from .source import QueryError
from .spatial import SpatialSettings, query_spatial
from .utility import overlaps
from .resources import resource_state, resource_window, resource_evidence

FIGHT_VERSION = 6
MAX_EVENTS = 16
MAX_LINKS = 20
MAX_RESOURCE_EVENTS = 4


@dataclass(frozen=True)
class FightSettings:
    before_ms: int = 2000
    after_ms: int = 3000
    allow_candidate_map: bool = False
    smoke_radius_cm: float | None = None

    def validate(self):
        for name in ("before_ms", "after_ms"):
            value = getattr(self, name)
            if type(value) is not int or not 0 <= value <= 5000:
                raise QueryError("invalid_fight_settings", f"{name} must be 0-5000 ms.")
        SpatialSettings(smoke_radius_cm=self.smoke_radius_cm).validate()


def _pick(item, keys):
    return {key: item[key] for key in keys if key in item}


def _summary(fight, live_ms):
    result = _pick(fight, ("id", "kind", "player", "teammate", "opponent", "time_ms", "end_ms",
                          "actual_damage_dealt", "actual_damage_taken", "teammate_damage_dealt",
                          "teammate_damage_taken", "teammate_died_in_observed_exchange", "opponent_death"))
    result.update(round_time_ms=fight["time_ms"]-live_ms, round_end_ms=fight["end_ms"]-live_ms)
    if "selected_response" in fight:
        result["selected_response"] = _pick(fight["selected_response"], ("status", "actual_damage", "other_participation"))
        result["selected_response"]["damage_ids"] = fight["selected_response"]["damage_ids"][:MAX_LINKS]
        result["selected_response"]["damage_ids_omitted"] = max(0, len(fight["selected_response"]["damage_ids"])-MAX_LINKS)
    for key in ("damage_ids", "death_ids"):
        if key in fight:
            result[key] = fight[key][:MAX_LINKS]
            result[key+"_omitted"] = max(0, len(fight[key])-MAX_LINKS)
    return result


def _select(fights, round_, fight_id, at_ms):
    if fight_id is not None:
        match = next((f for f in fights if f["id"] == fight_id), None)
        if match is None:
            raise QueryError("unknown_fight", "Fight ID does not belong to this round/player; list fights first.")
        return match, match["time_ms"]
    if type(at_ms) is not int or not round_["buy_ms"] <= round_["live_ms"]+at_ms < round_["end_ms"]:
        raise QueryError("invalid_time_range", "--at-ms must be an integer inside the round, since live start.")
    moment = round_["live_ms"]+at_ms
    matches = [f for f in fights if f["time_ms"] <= moment <= f["end_ms"]]
    if not matches:
        distances = [(min(abs(moment-f["time_ms"]), abs(moment-f["end_ms"])), f) for f in fights]
        nearest = min((d for d, _ in distances), default=1001)
        matches = [f for d, f in distances if d == nearest and d <= 1000]
    if len(matches) != 1:
        code = "ambiguous_fight" if matches else "no_fight_near_time"
        hint = "; ".join(f["id"] for f in matches[:5]) or "List this round's fights and choose --id."
        raise QueryError(code, f"No unique damage group at that time (nearest tolerance 1000 ms). {hint}")
    return matches[0], moment


def _event(event):
    return _pick(event, ("id", "kind", "time_ms", "round_time_ms", "attacker", "victim", "killer", "player",
                         "actual_damage", "raw_damage", "weapon", "wall_penetration", "ability", "stage",
                         "phase", "alive", "post_round", "item", "item_class"))


def _utility(data):
    records = []
    for record in data["records"][:20]:
        item = _pick(record, ("id", "kind", "player", "owner", "label", "category", "start_ms", "end_ms",
                              "round_start_ms", "round_end_ms", "time_ms", "round_time_ms", "pair_status", "severity", "status"))
        position = record.get("position_observation")
        if position:
            item["position_cm"] = [round(n, 1) for n in position["position_cm"]]
            item["position_time_ms"] = position["time_ms"]
        records.append(item)
    return {"status": data["status"], "records": records, "total": data["total"],
            "omitted": None if data["total"] is None else data["total"]-len(records)}


def _resources(data):
    return {**data, "records": [_pick(r, ("id", "time_ms", "round_time_ms", "item", "ability",
                                          "metric", "value", "status")) for r in data["records"]]}


def _resource_state(state):
    """Keep counts/times/evidence; detail lookup carries repeated class/owner data."""
    weapon = state["weapon"]
    return {**state, "weapon": {k:v for k,v in weapon.items() if k != "ownership_sources"} if weapon else None,
            "abilities": [{k:v for k,v in a.items() if k != "ownership_sources" and (k != "item_class" or not a["ability"])}
                          for a in state["abilities"]]}


def _sample(snapshot, core_pawns, anchor):
    players = snapshot["players"] if anchor else [p for p in snapshot["players"] if p["pawn"] in core_pawns]
    observations = []
    for p in players:
        item = _pick(p, ("pawn", "status", "life_state", "position_cm", "sample_time_ms", "sample_age_ms", "source"))
        if item["position_cm"]:
            item["position_cm"] = [round(n, 1) for n in item["position_cm"]]
        item["view_deg"] = [round(p[k], 1) if p[k] is not None else None for k in ("yaw_deg", "pitch_deg")]
        item["status_effect_ids"] = [s["id"] for s in p["utility_status"]["effects"]]
        item["status_coverage"] = p["utility_status"]["status"]
        observations.append(item)
    relations = []
    for r in snapshot["relations"]:
        if not anchor and r["to"] not in core_pawns:
            continue
        item = _pick(r, ("to", "distance_m", "horizontal_offset_deg", "vertical_offset_deg"))
        item["sightline"] = _pick(r["static_sightline"], ("status", "reason", "blocker_ids"))
        item["walking"] = _pick(r["walking_access"], ("status", "reason", "length_m"))
        item["smokes"] = _pick(r["smoke_context"], ("status", "reason", "candidate_radius_cm"))
        relations.append(item)
    smokes = [_pick(s, ("id", "label", "position_cm", "sample_time_ms", "sample_age_ms"))
              for s in snapshot["smoke_observations"]["smokes"][:8]]
    angles = {**snapshot["entrance_context"], "candidates": [
        {**c, "targets": [t for t in c["targets"] if anchor or t["pawn"] in core_pawns]}
        for c in snapshot["entrance_context"]["candidates"]]}
    return {"time_ms": snapshot["time_ms"], "round_time_ms": snapshot["round_time_ms"],
            "scope": "all_players" if anchor else "fight_participants_and_selected",
            "players": observations, "relations_from_selected": relations,
            "entrance_context": angles, "smoke_centers": smokes,
            "smokes_omitted": snapshot["smoke_observations"]["omitted"]
                              +max(0, len(snapshot["smoke_observations"]["smokes"])-8)}


def _context(model, export, report, selected, fight, anchor, config):
    round_ = report["round"]
    start = max(round_["buy_ms"], min(anchor, fight["time_ms"])-config.before_ms)
    end = min(round_["end_ms"], max(anchor, fight["end_ms"])+config.after_ms+1, start+10000)
    # A late --at-ms anchor gets its own bounded window even for a long group.
    if anchor >= end:
        start = max(round_["buy_ms"], anchor-config.before_ms)
        end = min(round_["end_ms"], anchor+config.after_ms+1, start+10000)
    moments = sorted({max(start, anchor-1000), anchor, min(end-1, max(anchor+1, fight["end_ms"]))})
    offsets = [t-round_["live_ms"] for t in moments]
    spatial = query_spatial(model, export, round_["number"], selected, start-round_["live_ms"], end-round_["live_ms"],
                            SpatialSettings(limit=3, movement_max_age_ms=report["settings"]["movement_max_age_ms"],
                                            allow_candidate_map=config.allow_candidate_map,
                                            smoke_radius_cm=config.smoke_radius_cm), sample_times_ms=offsets)
    core = {selected["pawn"], fight.get("teammate"), fight["opponent"]}
    samples = [_sample(s, core, s["time_ms"] == anchor) for s in spatial["snapshots"]]
    event_map = {e["id"]: e for e in report["timeline"] if e["kind"] not in ("ownership", "teammate_fight")}
    for ref in fight["damage_ids"]:
        hit = report["evidence"].get(ref)
        if hit:
            event_map[ref] = {**hit, "round_time_ms": hit["time_ms"]-round_["live_ms"]}
    candidates = [e for e in event_map.values() if start <= e["time_ms"] < end]
    chosen = sorted(candidates, key=lambda e: (abs(e["time_ms"]-anchor), e["time_ms"], e["id"]))[:MAX_EVENTS]
    events = [_event(e) for e in sorted(chosen, key=lambda e: (e["time_ms"], 0 if e["kind"] == "damage" else 1, e["id"]))]
    geometry = spatial["map_model"]
    utility = _utility(spatial["utility"])
    summary = _summary(fight, round_["live_ms"])
    omitted_links = (sum(v for k, v in summary.items() if k.endswith("_omitted"))
                     +summary.get("selected_response", {}).get("damage_ids_omitted", 0))
    objective = next((e for e in reversed(report["timeline"]) if e["time_ms"] <= anchor
                      and e["kind"] in ("spikePlanted", "spikeDefused", "spikeExploded")), None)
    living = [p for p in model["players"] if alive(model, round_, p["pawn"], anchor)]
    equipment = latest_equipment(model, round_, selected["pawn"], anchor)
    resources = _resources(resource_window(model, round_, selected["pawn"], start, end, limit=MAX_RESOURCE_EVENTS))
    resource_snapshot = _resource_state(resource_state(model, round_, selected, anchor))
    response = {"window": {"start_ms": start, "end_ms": end, "round_start_ms": start-round_["live_ms"],
                            "round_end_ms": end-round_["live_ms"], "anchor_ms": anchor,
                            "scope": "start inclusive/end exclusive; global milliseconds; round times since live start"},
                "fight": summary, "events": events,
                "state_at_anchor": {"alive": {"selected_team": sum(p["team"] == selected["team"] for p in living),
                                                "opponents": sum(p["team"] != selected["team"] for p in living)},
                                    "last_recorded_objective": _event(objective) if objective else None,
                                    "equipment": equipment, "resources": resource_snapshot},
                "resource_observations": resources,
                "snapshots": samples, "utility": utility,
                "map_model": _pick(geometry, ("id", "revision", "validation")) if geometry else None,
                "coverage": {"events_total": len(candidates), "events_returned": len(events),
                    "event_selection": "nearest to anchor, then chronological",
                    "fight_outside_window": fight["time_ms"] < start or fight["end_ms"] >= end,
                    "fight_links_omitted": omitted_links,
                    "equipment_observations_omitted": equipment["omitted"],
                    "resource_observations_omitted": resources["omitted"],
                    "resource_abilities_omitted": resource_snapshot["abilities_omitted"],
                    "truncated": spatial["coverage"]["truncated"] or len(candidates)>len(events)
                                 or bool(utility["omitted"]) or bool(omitted_links) or bool(equipment["omitted"])
                                 or bool(resources["omitted"]) or bool(resource_snapshot["abilities_omitted"]) or any(s["smokes_omitted"] for s in samples),
                    "actual_visibility": None, "player_knowledge": None,
                    "limitations": ["Damage groups omit missed shots and exchanges without damage.",
                        "No recorded damage contribution does not establish no swing, shots or help.",
                        "Equipment observations do not establish remaining ammo, knives, ability charges or an active ultimate.",
                        "Separate resource observations expose counts/states; shot totals, reload completion and effective readiness remain unknown.",
                        "Replicated facing, proximity and a modeled route do not prove visibility, access or a missed trade.",
                        "Map estimates may be unverified; smoke opacity/height/radius and flash severity remain unknown.",
                        "Other players are sampled at the anchor only; earlier/later frames focus on this fight's participants."]}}
    return response


def query_fight(model, export, number, selected, fight_id=None, at_ms=None, evidence_id=None,
                settings=None, limit=20, offset=0, analysis_settings=None):
    config = settings or FightSettings()
    config.validate()
    if sum(v is not None for v in (fight_id, at_ms, evidence_id)) > 1:
        raise QueryError("invalid_fight_selector", "Use only one of --id, --at-ms or --evidence-id.")
    if type(limit) is not int or not 1 <= limit <= 40 or type(offset) is not int or offset < 0:
        raise QueryError("invalid_pagination", "Use limit 1-40 and a nonnegative offset.")
    report = query_round(model, export, number, selected, analysis_settings)
    base = {"schema_version": 1, "fight_version": FIGHT_VERSION, "match_id": model["match_id"],
            "revision": model["revision"], "replay_build": model["replay_build"], "round": number,
            "map_asset": model.get("map_asset"),
            "selected_player": selected["pawn"], "players": report["players"],
            "analysis_settings": report["settings"],
            "round_context": {"live_ms": report["round"]["live_ms"], "side": report["round"]["selected_side"]}}
    if evidence_id is not None:
        record = report["evidence"].get(evidence_id)
        if record is None:
            record = resource_evidence(model, report["round"], selected["pawn"], evidence_id)
        if record is None:
            record = next((r for r in model.get("checkpoint_state",{}).get("equipment",[])
                           if r["id"] == evidence_id and r["scope_round"] == number and r["player"] == selected["pawn"]),None)
        if record is None:
            record = next((r for r in model.get("gunfire",{}).get("observations",[]) if r["id"]==evidence_id
                           and r["player"]==selected["pawn"] and report["round"]["buy_ms"]<=r["time_ms"]<report["round"]["end_ms"]),None)
        if record is None:
            data = model.get("utility", {})
            for kind, items in (("smoke", data.get("smokes", [])), ("player_status", data.get("statuses", [])),
                                ("blind_update", data.get("blind_updates", []))):
                candidate = next((r for r in items if r["id"] == evidence_id), None)
                if candidate is None:
                    continue
                in_round = (report["round"]["buy_ms"] <= candidate["time_ms"] < report["round"]["end_ms"]
                            if kind == "blind_update" else overlaps(candidate, report["round"]["buy_ms"], report["round"]["end_ms"]))
                if in_round:
                    record = {"kind": kind, **{k: v for k, v in candidate.items() if k != "positions"}}
                break
        if record is None:
            raise QueryError("unknown_evidence", "Evidence ID was not found in this round/player query.")
        return {**base, "query": "evidence", "record": record}
    fights = sorted(report["player_fights"]+report["teammate_fights"], key=lambda f: (f["time_ms"], f["id"]))
    if fight_id is None and at_ms is None:
        page = fights[offset:offset+limit]
        return {**base, "query": "fight_index", "fights": [_summary(f, report["round"]["live_ms"]) for f in page],
                "pagination": {"total": len(fights), "returned": len(page), "offset": offset,
                               "next_offset": offset+len(page) if offset+len(page)<len(fights) else None}}
    fight, anchor = _select(fights, report["round"], fight_id, at_ms)
    result = _context(model, export, report, selected, fight, anchor, config)
    return {**base, "query": "fight", "settings": asdict(config), **result,
            "evidence_lookup": {"command": "fight", "round": number, "option": "--evidence-id",
                                "movement_sources": "Source row/packet/time are included in snapshots; use spatial for precise values."}}
