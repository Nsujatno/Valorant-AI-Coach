"""Source-backed item resources and state observations, separate from readiness.

Counts belong to item/component incarnations, not to whoever originally bought
the item. Ownership attributes events; a pickup can retain the same item's ammo.
No shot count, missed shot, reload completion or effective ability readiness is
inferred from these replicated fields.
"""

from bisect import bisect_right
from collections import defaultdict
import struct

from .equipment import latest_equipment, life_scope, observation_in_scope
from .source import QueryError, provenance, value


RESOURCE_VERSION = 2
AMMO_GROUP = "/Script/ShooterGame.AmmoComponent"
CHARGE_GROUPS = {"/Script/ShooterGame.EquipmentChargeComponent",
                 "/Script/ShooterGame.SignatureAbilityResourceComponent"}
STATE_GROUP = "/Script/ShooterGame.EquippableStateMachineComponent"
DAGGER_GROUP = "/Game/Characters/Wushu/S0/Ability_X/Comp_Wushu_X_DaggerResource.Comp_Wushu_X_DaggerResource_C_ClassNetCache"
COOLDOWN_GROUP = "/Game/Characters/Components/Comp_Ability_CooldownComponent.Comp_Ability_CooldownComponent_C"
LIMITATIONS = [
    "Last source observations, scoped to round/life/item; missing values are unknown.",
    "Ammo changes and firing states are not a complete shot log or proof of missed shots.",
    "Reload-state observations do not establish completion or an uninterrupted reload.",
    "Charge resources, temporary charges and signature charges are separate; effective readiness is unknown.",
    "Ultimate active/points and knife counts are observations, not a guarantee that an ability can be used.",
    "Checkpoint seeds retain their own times, source identities and explicit round scope; absent defaults stay unknown.",
]


class ItemIncarnations:
    """Keep GUID reuse and actor closes from joining different item incarnations."""

    def __init__(self, actors):
        self.scopes = defaultdict(list)
        for actor in sorted(actors, key=lambda a: (a["time_ms"], a.get("source_row", 0))):
            scopes = self.scopes[actor["actor_net_guid"]]
            if actor["event"] == "open":
                if scopes and scopes[-1]["closed_ms"] is None:
                    scopes[-1]["closed_ms"] = actor["time_ms"]
                scopes.append({"opened_ms": actor["time_ms"], "closed_ms": None,
                               "item_class": actor.get("class_path")})
            elif actor["event"] == "close" and scopes:
                scopes[-1]["closed_ms"] = actor["time_ms"]
        self.times = {item: [s["opened_ms"] for s in scopes] for item, scopes in self.scopes.items()}

    def at(self, item, moment):
        index = bisect_right(self.times.get(item, []), moment)-1
        if index < 0:
            return None
        scope = self.scopes[item][index]
        return scope if scope["closed_ms"] is None or moment < scope["closed_ms"] else None


def _ability(path):
    if "/Characters/Wushu/" not in (path or ""):
        return None
    return next((label for part, label in (("Ability_4", "Cloudburst"), ("Ability_Q", "Updraft"),
                                         ("Ability_E", "Tailwind"), ("Ability_X", "Blade Storm"))
                 if f"/{part}/" in path), None)


def _scalar(row, boolean=False, raw_int=False):
    observed = value(row)
    # These two exact Blueprint int parameters are not typed by vrfkit. Do not
    # apply this decoder to arbitrary raw fields, packed GUIDs or RPC envelopes.
    if observed is None and raw_int and row.get("bit_count") == 32 and len(row.get("raw_bits") or b"") == 4:
        observed = struct.unpack("<i", row["raw_bits"])[0]
    if boolean:
        return observed if type(observed) is bool else None
    return observed if type(observed) is int and observed >= 0 else None


def _metric(row, component, item_class):
    name, group = row["field_name"], row.get("group_path")
    if name == "AuthResourceAmount" and group == AMMO_GROUP:
        return {"MagazineAmmo": "magazine_ammo", "ReserveAmmo": "reserve_ammo"}.get(component)
    if group in CHARGE_GROUPS:
        return {"AuthResourceAmount": "charge_resource", "AuthSignatureChargeAmount": "signature_charge_resource",
                "CurrentTemporaryCharges": "temporary_charges", "MaxCharges": "max_charges"}.get(name)
    if group == COOLDOWN_GROUP:
        return {"CooldownActive": "cooldown_active", "CooldownSeconds": "cooldown_seconds"}.get(name)
    if _ability(item_class) == "Blade Storm":
        if name == "Multicast Update Daggers.DaggersRemaining" and group == DAGGER_GROUP:
            return "blade_storm_knives"
        if name == "Ammo" and group == "/Script/ShooterGame.BlueprintResourceVisualizationComponent":
            return "blade_storm_knives"
    if name == "CurrentState" and group == STATE_GROUP and component == "EquippableStateMachine":
        return "weapon_state" if "/Equippables/Guns/" in (item_class or "") else None
    return None


class _Ownership:
    def __init__(self, rows):
        self.rows = defaultdict(lambda: defaultdict(list))
        for row in rows:
            self.rows[(row["item"], row["opened_ms"])][row["time_ms"]].append(row)
        self.times = {key: sorted(times) for key, times in self.rows.items()}

    def at(self, item, scope_start, moment):
        key = (item, scope_start)
        times = self.times.get(key, [])
        index = bisect_right(times, moment)-1
        if index < 0:
            return {"owner": None, "sources": []}
        rows = self.rows[key][times[index]]
        owners = {r["owner"] for r in rows}
        return {"owner": next(iter(owners)) if len(owners) == 1 else None,
                "sources": [r["source"] for r in rows]}


def reconstruct_resources(rows, actors, guid_rows, players, *, table="fields"):
    items = ItemIncarnations(actors)
    guids = {g["net_guid"]: g for g in guid_rows}
    pawns = {p["pawn"] for p in players}
    states = {p["state_id"]: p["pawn"] for p in players}
    ownership = []
    for row in rows:
        scope = items.at(row["actor_net_guid"], row["time_ms"])
        if row["field_name"] == "Owner" and scope:
            ownership.append({"item": row["actor_net_guid"], "time_ms": row["time_ms"],
                              "owner": value(row), "opened_ms": scope["opened_ms"], "source": provenance(row,table)})
    owner_history = _Ownership(ownership)
    observations = []
    for row in rows:
        actor, moment = row["actor_net_guid"], row["time_ms"]
        scope = items.at(actor, moment)
        if not scope:
            continue
        component = guids.get(row.get("object_net_guid"), {})
        player_metric = {"NumUltimatePoints": "ultimate_points", "bUltimateActive": "ultimate_active"}.get(row["field_name"])
        if player_metric and actor in states and row.get("object_net_guid") is None:
            metric, pawn, item = player_metric, states[actor], None
            owner_state = {"owner": pawn, "sources": []}
        else:
            # Identically named components on another actor must never supply ammo.
            if component.get("outer_net_guid") != actor:
                continue
            metric = _metric(row, component.get("path"), scope["item_class"])
            if not metric:
                continue
            item = actor
            owner_state = owner_history.at(item, scope["opened_ms"], moment)
            owner = owner_state["owner"]
            pawn = owner if owner in pawns else None
        if metric == "weapon_state":
            state_guid = value(row)
            state = guids.get(state_guid, {})
            observed = state.get("path") if state.get("outer_net_guid") == actor else None
        elif metric == "cooldown_seconds":
            number = value(row)
            observed = number if type(number) in (int, float) and 0 <= number < float("inf") else None
        else:
            observed = _scalar(row, metric in {"ultimate_active", "cooldown_active"}, metric == "blade_storm_knives")
        source = provenance(row,table)
        observations.append({"id": f"{table}:{row['source_row']}:resource", "kind": "resource",
            "time_ms": moment, "source": source, "player": pawn, "item": item,
            "item_class": scope["item_class"], "ability": _ability(scope["item_class"]),
            "component": component.get("path"), "metric": metric, "value": observed,
            "ownership_sources": owner_state["sources"],
            "status": "observed" if observed is not None else "unknown_value",
            "opened_ms": scope["opened_ms"], "closed_ms": scope["closed_ms"],
            "decoding": "exact_blueprint_int32_le" if metric == "blade_storm_knives" and value(row) is None else "typed_export"})
    return {"version": RESOURCE_VERSION, "observations": sorted(observations, key=lambda r: (r["time_ms"], r["source"]["row"])),
            "ownership": ownership}


def _last(records, metric, moment, minimum, round_):
    candidates = [r for r in records if r["metric"] == metric and observation_in_scope(r,round_,minimum,moment)
                  and (r["closed_ms"] is None or moment < r["closed_ms"])]
    if not candidates:
        return {"status": "unknown", "value": None, "reason": "no_observation_in_scope", "evidence_ids": []}
    latest = max(r["time_ms"] for r in candidates)
    records = [r for r in candidates if r["time_ms"] == latest]
    values = {r["value"] for r in records}
    observed = next(iter(values)) if len(values) == 1 else None
    return {"status": "conflicting_same_time_observations" if len(values) > 1 else
                       "observed" if observed is not None else "unknown_value", "value": observed,
            "observed_time_ms": latest, "observation_age_ms": moment-latest,
            "evidence_ids": [r["id"] for r in records[:8]], "evidence_ids_omitted": max(0, len(records)-8)}


def resource_state(model, round_, selected, moment):
    """Resources at a time; counts do not imply the player can act on them."""
    pawn = selected["pawn"]
    living, minimum = life_scope(model, round_, pawn, moment)
    data = model.get("resources", {})
    observations = data.get("observations", [])
    owners = _Ownership(data.get("ownership", []))
    player_records = [r for r in observations if r["item"] is None and r["player"] == pawn]
    equipment = latest_equipment(model, round_, pawn, moment)
    result = {"status": "observations_loaded" if data else "not_loaded", "life_state": "alive" if living else "dead",
              "time_ms": moment, "round_time_ms": moment-round_["live_ms"],
              "weapon": None, "weapon_status": equipment["status"], "abilities": [], "abilities_omitted": 0,
              "ultimate_points": _last(player_records, "ultimate_points", moment, minimum, round_),
              "ultimate_active": _last(player_records, "ultimate_active", moment, minimum, round_),
              "effective_readiness": None, "shots_fired": None, "missed_shots": None}
    if not living:
        return result
    if equipment["status"] == "equipped_observation":
        result["weapon_status"] = "no_resource_observations_in_scope"
        equip = equipment["observations"][0]
        records = [r for r in observations if r["item"] == equip["item"] and observation_in_scope(r,round_,minimum,moment)
                   and (r["closed_ms"] is None or moment < r["closed_ms"])]
        if records:
            scope = max(r["opened_ms"] for r in records)
            records = [r for r in records if r["opened_ms"] == scope]
            owner = owners.at(equip["item"], scope, moment)
            result["weapon_status"] = "ownership_unknown_or_not_selected"
            if owner["owner"] == pawn:
                result["weapon_status"] = "observed_item_resources"
                result["weapon"] = {"item": equip["item"], "weapon": equip.get("weapon"),
                    "ownership_sources": owner["sources"],
                    "magazine": _last(records, "magazine_ammo", moment, minimum, round_),
                    "reserve": _last(records, "reserve_ammo", moment, minimum, round_),
                    "state": _last(records, "weapon_state", moment, minimum, round_)}
    by_item = defaultdict(list)
    for record in observations:
        if record["item"] is not None and "/Characters/" in (record["item_class"] or ""):
            if observation_in_scope(record,round_,minimum,moment) and (record["closed_ms"] is None or moment < record["closed_ms"]):
                by_item[(record["item"], record["opened_ms"])].append(record)
    activations = [r["time_ms"] for r in player_records if r["metric"] == "ultimate_active" and r["value"] is True
                   and "scope_round" not in r and minimum <= r["time_ms"] <= moment]
    for (item, scope), records in sorted(by_item.items()):
        owner = owners.at(item, scope, moment)
        if owner["owner"] != pawn:
            continue
        item_minimum = max(minimum, max(activations, default=minimum)) if records[0]["ability"] == "Blade Storm" else minimum
        result["abilities"].append({"item": item, "ability": records[0]["ability"], "item_class": records[0]["item_class"],
            "ownership_sources": owner["sources"],
            "resources": {metric: _last(records, metric, moment, item_minimum, round_) for metric in sorted({r["metric"] for r in records})}})
    result["abilities_omitted"] = max(0, len(result["abilities"])-16)
    result["abilities"] = result["abilities"][:16]
    return result


def resource_evidence(model, round_, pawn, evidence_id):
    """Resolve also an item's pre-pickup counts used by the player's snapshot."""
    data = model.get("resources", {})
    record = next((r for r in data.get("observations", []) if r["id"] == evidence_id), None)
    if not record or not observation_in_scope(record,round_,round_["buy_ms"],round_["end_ms"]-1):
        return None
    if record["player"] == pawn:
        return record
    # Holding an item does not rewrite who owned it when its ammo was observed.
    # Retrieval preserves the original observation's player and source.
    owners = _Ownership(data.get("ownership", []))
    for row in data.get("ownership", []):
        if row["item"] == record["item"] and row["opened_ms"] == record["opened_ms"]:
            if round_["buy_ms"] <= row["time_ms"] < round_["end_ms"] and owners.at(row["item"],row["opened_ms"],row["time_ms"])["owner"] == pawn:
                return record
    return None


def resource_window(model, round_, pawn, start, end, limit=200, offset=0):
    records = [{**r, "round_time_ms": r["time_ms"]-round_["live_ms"]}
               for r in model.get("resources", {}).get("observations", [])
               if r["player"] == pawn and start <= r["time_ms"] < end
               and ("scope_round" not in r or r["scope_round"] == round_["number"])]
    page = records[offset:offset+limit]
    return {"status": "observations_loaded" if "resources" in model else "not_loaded", "records": page,
            "total": len(records), "returned": len(page), "offset": offset,
            "omitted": len(records)-len(page),
            "next_offset": offset+len(page) if offset+len(page) < len(records) else None}


def query_resources(model, number, selected, at_ms, before_ms=2000, after_ms=3000, limit=40, offset=0):
    round_ = next((r for r in model["rounds"] if r["number"] == number), None)
    if round_ is None:
        raise QueryError("unknown_round", f"Round {number} does not exist in this export.")
    if type(at_ms) is not int or not round_["buy_ms"] <= round_["live_ms"]+at_ms < round_["end_ms"]:
        raise QueryError("invalid_time_range", "--at-ms must be inside the round, since live start.")
    if any(type(v) is not int or not 0 <= v <= 5000 for v in (before_ms, after_ms)):
        raise QueryError("invalid_resource_settings", "Before/after must each be 0-5000 ms.")
    if type(limit) is not int or not 1 <= limit <= 200 or type(offset) is not int or offset < 0:
        raise QueryError("invalid_pagination", "Use limit 1-200 and a nonnegative offset.")
    moment = round_["live_ms"]+at_ms
    start, end = max(round_["buy_ms"], moment-before_ms), min(round_["end_ms"], moment+after_ms+1)
    return {"schema_version": 1, "resource_version": RESOURCE_VERSION, "query": "resources",
            "match_id": model["match_id"], "revision": model["revision"], "replay_build": model["replay_build"],
            "round": number, "selected_player": selected["pawn"],
            "round_context": {key:round_[key] for key in ("buy_ms","live_ms","end_ms")},
            "window": {"start_ms": start, "end_ms": end, "end_exclusive": True},
            "state": resource_state(model, round_, selected, moment),
            "observations": resource_window(model, round_, selected["pawn"], start, end, limit, offset),
            "checkpoint_coverage": model.get("checkpoint_state", {}).get("coverage", {"status": "not_loaded"}),
            "limitations": LIMITATIONS}
