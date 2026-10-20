"""Validate resource snapshot seeds without turning snapshots into live events."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from .resources import ItemIncarnations, reconstruct_resources
from .source import provenance, value


GAME_STATE = "/Game/GameModes/Bomb/BombGameState.BombGameState_C"
PLAYER_STATE = "/Game/GameModes/Bomb/BombPlayerState.BombPlayerState_C"


@dataclass
class CheckpointState:
    resources: dict = field(default_factory=lambda: {"observations": [], "ownership": []})
    equipment: list = field(default_factory=list)
    coverage: dict = field(default_factory=lambda: {"status": "not_exported"})


def _single(rows, name, actor=None):
    candidates = [r for r in rows if r["field_name"] == name and (actor is None or r["actor_net_guid"] == actor)]
    values = {value(r) for r in candidates}
    return candidates[0] if len(values) == 1 and None not in values else None


def _round_scope(rows, checkpoint_actors, rounds):
    states = {a["actor_net_guid"] for a in checkpoint_actors if a.get("class_path") == GAME_STATE}
    markers = [r for r in rows if r["actor_net_guid"] in states and r.get("object_net_guid") is None
               and r.get("group_path") == GAME_STATE and r["field_name"] == "RoundNumber"]
    marker = _single(markers,"RoundNumber")
    number = value(marker) if marker else None
    if type(number) is not int or number < 0:
        return None, "missing_or_conflicting_round_marker"
    round_ = next((r for r in rounds if r["number"] == number+1),None)
    if round_ is None:
        return None, "unknown_round_marker"
    moment = rows[0]["time_ms"]
    previous = next((r for r in rounds if r["number"] == number),None)
    if moment >= round_["end_ms"] or (previous and moment < previous.get("result_time_ms",previous["live_ms"])):
        return None, "round_marker_time_mismatch"
    return (round_,provenance(marker,"checkpoint_fields")), None


def _players_in_snapshot(rows, players):
    matched = []
    for player in players:
        state_rows = [r for r in rows if r["actor_net_guid"] == player["state_id"]
                      and r.get("group_path") == PLAYER_STATE and r.get("object_net_guid") is None]
        subject = _single(state_rows,"Subject")
        pawn = _single(state_rows,"SpawnedCharacter")
        if subject and pawn and value(subject) == player["id"] and value(pawn) == player["pawn"]:
            matched.append(player)
    return matched


def reconstruct_checkpoints(tables, players, rounds, live_actors):
    result = CheckpointState()
    if tables is None:
        return result
    quality = tables.get("quality",{})
    issues = {name:quality[name] for name in ("checkpoint_partial_rows","checkpoint_partial_bits",
              "checkpoint_actor_rows_dropped","checkpoint_frame_non_finite_times") if quality.get(name)}
    issues.update({name:quality.get("net",{})[name] for name in ("malformed_packets","partial_errors",
                   "unfinished_partials","field_stream_failures","content_block_framing_failures")
                   if quality.get("net",{}).get(name)})
    if issues:
        result.coverage = {"status":"rejected_source_quality","issues":issues}
        return result
    groups = {name: defaultdict(list) for name in ("fields","actors","net_guids")}
    for name, grouped in groups.items():
        for row in tables[name]:
            grouped[row["checkpoint_index"]].append(row)
    live_items = ItemIncarnations(live_actors)
    rejected = Counter()
    snapshots, accepted = [], 0
    for index, rows in sorted(groups["fields"].items()):
        ids = {r["checkpoint_id"] for name in groups for r in groups[name].get(index,[])}
        times = {r["time_ms"] for name in ("fields","actors") for r in groups[name].get(index,[])}
        if len(ids) != 1 or len(times) != 1:
            rejected["inconsistent_checkpoint_identity_or_time"] += 1
            continue
        checkpoint_actors = groups["actors"].get(index,[])
        scoped, reason = _round_scope(rows,checkpoint_actors,rounds)
        if scoped is None:
            rejected[reason] += 1
            continue
        round_, marker_source = scoped
        moment = rows[0]["time_ms"]
        # A checkpoint actor-open is a snapshot assertion, not a new spawn.
        # Admit only an already observed, still-live incarnation of that class.
        valid_actors = set()
        for actor in checkpoint_actors:
            scope = live_items.at(actor["actor_net_guid"],moment)
            if actor["event"] == "open" and scope and scope["item_class"] == actor.get("class_path"):
                valid_actors.add(actor["actor_net_guid"])
        known_players = _players_in_snapshot(rows,players)
        player_states = {p["state_id"] for p in known_players}
        all_player_states = {p["state_id"] for p in players}
        eligible = [r for r in rows if r["actor_net_guid"] in valid_actors
                    and (r["actor_net_guid"] not in all_player_states or r["actor_net_guid"] in player_states)]
        resources = reconstruct_resources(eligible,live_actors,groups["net_guids"].get(index,[]),known_players,table="checkpoint_fields")
        for record in resources["observations"]:
            record["scope_round"] = round_["number"]
            record["round_marker_source"] = marker_source
        result.resources["observations"].extend(resources["observations"])
        result.resources["ownership"].extend(resources["ownership"])
        guids = {r["net_guid"]: r for r in groups["net_guids"].get(index,[])}
        pawns = {p["pawn"] for p in known_players}
        for row in eligible:
            if row["actor_net_guid"] not in pawns or row["field_name"] not in {"CurrentEquippable","NewCurrentEquippable"}:
                continue
            component = guids.get(row.get("object_net_guid"),{})
            if row.get("group_path") != "/Script/ShooterGame.AresInventory" or component.get("outer_net_guid") != row["actor_net_guid"]:
                continue
            item = value(row)
            item_scope = live_items.at(item,moment)
            if item not in (None,0) and (not item_scope or item not in valid_actors):
                continue
            result.equipment.append({"id":f"checkpoint_fields:{row['source_row']}:equipped", "kind":"equipped",
                "time_ms":moment, "scope_round":round_["number"], "player":row["actor_net_guid"],
                "item":item, "item_class":item_scope["item_class"] if item_scope else None,
                "weapon":None, "source":provenance(row,"checkpoint_fields")})
        accepted += 1
        snapshots.append({"index":index,"id":next(iter(ids)),"time_ms":moment,"round":round_["number"],
                          "matched_players":len(known_players),"resource_observations":len(resources["observations"])})
    result.coverage = {"status":"loaded", "snapshots_total":len(groups["fields"]),"snapshots_accepted":accepted,
        "rejections":dict(rejected),"resource_observations":len(result.resources["observations"]),
        "equipment_observations":len(result.equipment),"snapshots":snapshots,
        "semantics":"Explicit saved state; no inferred defaults, casts, refills or round-start timestamps."}
    return result
