"""Tagged firing effects, including activity outside damage-based fights.

One source invocation is one observation, not necessarily one bullet: shotgun
pellets and alternate attacks can carry several projectiles. Nearby damage is
bounded temporal context, never a projectile-to-hit or miss assignment.
"""

from collections import Counter, defaultdict
import json
import math
import struct

from .resources import ItemIncarnations
from .source import QueryError, provenance


GUNFIRE_VERSION = 1
EFFECT_GROUP = "/Script/ShooterGame.ReplayEffectComponent_ClassNetCache"
FUNCTIONS = {"ReplayPlayContinuousEffectAtLocation", "ReplayPlayOneShotEffectAtLocation"}
ARRAYS = ("FloatValues", "ObjectValues", "VectorValues")
LIMITATIONS = [
    "One firing-effect observation is not necessarily one bullet; projectile counts and sequence indices remain separate.",
    "Unrecorded or undecodable effects remain a coverage gap; observation totals are not a guaranteed complete shot count.",
    "Nearby damage can overlap several effects; no nearby recorded damage does not prove a miss or identify a target.",
    "Effect origin and attack vectors are source observations, not crosshair pixels, visibility or player knowledge.",
    "Other firing sources include ability weapons and automatic turret attacks; attribution does not prove a player pulled the trigger.",
]


def _tags(manifest):
    choices = defaultdict(set)
    for group in manifest.get("net_field_export_groups", []):
        if group.get("path") == "NetworkGameplayTagNodeIndex":
            for field in group.get("fields", []):
                if type(field.get("handle")) is int and isinstance(field.get("name"), str):
                    choices[field["handle"]].add(field["name"])
    return {index:next(iter(names)) for index,names in choices.items() if len(names)==1}


def _invocations(rows):
    current = {}
    result = []
    for row in sorted(rows,key=lambda r:r["source_row"]):
        if row.get("group_path") != EFFECT_GROUP:
            continue
        function, _, member = (row.get("field_name") or "").partition(".")
        if function not in FUNCTIONS:
            continue
        key = (row["packet_id"],row["actor_net_guid"],row.get("object_net_guid"),function)
        # EffectContainer is the first observed parameter of each invocation.
        # Keep adjacent same-packet calls instead of overwriting their arrays.
        if member == "EffectContainer" or key not in current:
            current[key] = {"anchor":row,"function":function,"members":defaultdict(list)}
            result.append(current[key])
        current[key]["members"][member].append(row)
    return result


def _arrays(invocation, tags):
    fields = defaultdict(list)
    issues = []
    for name in ARRAYS:
        for row in invocation["members"].get(name, []):
            text = row.get("value_str")
            try:
                array = json.loads(text) if text and len(text) <= 65536 else None
            except (ValueError,TypeError):
                array = None
            if not isinstance(array,list) or len(array)>64:
                issues.append("invalid_or_untyped_"+name)
                continue
            for entry in array:
                if not isinstance(entry,dict) or type(entry.get("tag")) is not int or "value" not in entry:
                    issues.append("invalid_tag_value_pair")
                    continue
                tag = tags.get(entry["tag"])
                if tag and tag.startswith("FiringState."):
                    fields[tag].append(entry["value"])
    return fields, sorted(set(issues))


def _field(fields,name):
    values = fields.get("FiringState."+name,[])
    if not values:
        return None, "missing"
    # A repeated tag is ambiguous even when an upstream decoder overwrote it
    # in another representation. Equal corroborating values are admissible.
    if any(v != values[0] for v in values[1:]):
        return None, "conflicting_values"
    return values[0], "observed"


def _number(fields,name,integer=True):
    number,status = _field(fields,name)
    if status != "observed":
        return None,status
    valid = type(number) in (int,float) and math.isfinite(number)
    if integer:
        valid = valid and number >= 0 and int(number)==number
    return (int(number) if integer else number,"observed") if valid else (None,"invalid_value")


def _item(state, moment, guids, incarnations):
    visited, path = set(), []
    current = state
    for _ in range(16):
        if type(current) is not int or current in visited or current == 0:
            break
        visited.add(current)
        node = guids.get(current,{})
        if node.get("path"):
            path.append(node["path"])
        scope = incarnations.at(current,moment)
        if scope and scope.get("item_class"):
            cls = scope["item_class"]
            if "/Equippables/" in cls or "/Characters/" in cls:
                return current,cls,path
        current = node.get("outer_net_guid")
    return None,None,path


def _origin(invocation):
    rows = invocation["members"].get("248",[])
    if len(rows)!=1:
        return None
    row=rows[0]
    raw=row.get("raw_bits")
    if row.get("bit_count") != 192 or not raw or len(raw)!=24:
        return None
    xyz=struct.unpack("<3d",raw)
    return list(xyz) if all(math.isfinite(v) for v in xyz) else None


def reconstruct_gunfire(rows, manifest, actors, guid_rows, players):
    tags = _tags(manifest)
    guids = {g["net_guid"]:g for g in guid_rows}
    incarnations = ItemIncarnations(actors)
    identities = {p["state_id"]:p["pawn"] for p in players}
    records, counts = [],Counter()
    for invocation in _invocations(rows):
        counts["effect_invocations_inspected"] += 1
        fields, issues = _arrays(invocation,tags)
        for issue in issues:counts[issue] += 1
        if not fields:
            continue
        anchor=invocation["anchor"]
        counts["firing_effect_observations"] += 1
        state,state_status = _number(fields,"FiringState")
        shooter,shooter_status = _number(fields,"FiringPlayerState")
        item,cls,chain = _item(state,anchor["time_ms"],guids,incarnations)
        values,statuses = {},{"firing_state":state_status,"firing_player_state":shooter_status}
        for field,key,integer in [("AmmoRemaining","ammo_remaining",True),("NumProjectiles","num_projectiles",True),
                                  ("BurstShotNumber","burst_shot_number",True),("RandomSeed","random_seed_observation",False),
                                  ("TracerOption","tracer_option",True),("YawSwitch","yaw_switch",True)]:
            values[key],statuses[key] = _number(fields,field,integer)
        vectors=[]
        for index in range(1,16):
            vector,status = _field(fields,f"AttackVector.{index}")
            valid = isinstance(vector,dict) and all(type(vector.get(k)) in (int,float) and math.isfinite(vector[k]) for k in ("x","y","z"))
            if status == "observed" and valid:
                vectors.append({"index":index,"direction":[vector[k] for k in ("x","y","z")]})
            elif status != "missing":
                issues.append(f"invalid_or_conflicting_attack_vector_{index}")
        if item is None:counts["unresolved_items"] += 1
        if shooter not in identities:counts["unresolved_shooters"] += 1
        sources = [provenance(r) for rows_ in invocation["members"].values() for r in rows_]
        if anchor["field_name"].split(".",1)[-1] != "EffectContainer":
            issues.append("missing_invocation_start_parameter")
        records.append({"id":f"fields:{anchor['source_row']}:gunfire","kind":"gunfire","time_ms":anchor["time_ms"],
            "source":provenance(anchor),"component_sources":sources,"function":invocation["function"],
            "player":identities.get(shooter),"firing_player_state":shooter,"firing_state":state,
            "item":item,"item_class":cls,"item_kind":"unknown" if cls is None else "gun" if "/Equippables/Guns/" in cls else "other_firing_source",
            "weapon":None,"state_outer_path":chain,"origin_cm":_origin(invocation),"attack_vectors":vectors,
            **values,"field_status":statuses,"issues":sorted(set(issues)),"hit_or_miss":None})
    return {"version":GUNFIRE_VERSION,"observations":records,"coverage":{
        "tag_names_available":bool(tags),**dict(counts),"scope":"live tagged replay effects only; no checkpoint actions"}}


def gunfire_window(model,round_,pawn,start,end,limit=20,offset=0,damage_window_ms=100):
    records = [r for r in model.get("gunfire",{}).get("observations",[]) if r["player"]==pawn and start<=r["time_ms"]<end]
    records.sort(key=lambda r:(r["time_ms"],r["source"]["row"]))
    page=[]
    for record in records[offset:offset+limit]:
        nearby=[d for d in model.get("damage",[]) if d["attacker"]==pawn and record["item"] is not None
                and d.get("weapon_actor")==record["item"] and abs(d["time_ms"]-record["time_ms"])<=damage_window_ms]
        page.append({**record,"round_time_ms":record["time_ms"]-round_["live_ms"],
            "phase":"buy" if record["time_ms"]<round_["live_ms"] else "post_round" if record["time_ms"]>round_.get("result_time_ms",round_["end_ms"]) else "live",
            "nearby_damage":{"status":"known_item" if record["item"] is not None else "unknown_item",
                             "window_ms":damage_window_ms,"relation":"absolute source-time difference; same shooter and item; not hit attribution",
                             "damage_ids":[d["id"] for d in nearby[:16]],"omitted":max(0,len(nearby)-16)}})
    return {"records":page,"total":len(records),"returned":len(page),"offset":offset,
            "omitted":len(records)-len(page),"next_offset":offset+len(page) if offset+len(page)<len(records) else None}


def query_gunfire(model,number,selected,start_ms=None,end_ms=None,limit=20,offset=0,damage_window_ms=100):
    round_=next((r for r in model["rounds"] if r["number"]==number),None)
    if round_ is None:raise QueryError("unknown_round",f"Round {number} does not exist in this export.")
    start=0 if start_ms is None else start_ms
    end=round_["end_ms"]-round_["live_ms"] if end_ms is None else end_ms
    if any(type(v) is not int for v in (start,end)) or not round_["buy_ms"]<=round_["live_ms"]+start<round_["live_ms"]+end<=round_["end_ms"]:
        raise QueryError("invalid_time_range","Use an increasing window inside the round, in milliseconds since live start.")
    if type(limit) is not int or not 1<=limit<=100 or type(offset) is not int or offset<0:
        raise QueryError("invalid_pagination","Use limit 1-100 and a nonnegative offset.")
    if type(damage_window_ms) is not int or not 0<=damage_window_ms<=1000:
        raise QueryError("invalid_gunfire_settings","Damage context window must be 0-1000 ms.")
    return {"schema_version":1,"gunfire_version":GUNFIRE_VERSION,"query":"gunfire","match_id":model["match_id"],
            "revision":model["revision"],"replay_build":model["replay_build"],"round":number,"selected_player":selected["pawn"],
            "window":{"start_ms":round_["live_ms"]+start,"end_ms":round_["live_ms"]+end,"end_exclusive":True},
            "observations":gunfire_window(model,round_,selected["pawn"],round_["live_ms"]+start,round_["live_ms"]+end,limit,offset,damage_window_ms),
            "source_coverage":model.get("gunfire",{}).get("coverage",{}),"limitations":LIMITATIONS}
