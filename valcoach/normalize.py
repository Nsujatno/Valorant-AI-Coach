"""Versioned facts reconstructed from Parquet, never from the earlier reports."""

from __future__ import annotations

from collections import defaultdict
from itertools import groupby
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from .source import Export, QueryError, packed_guid, provenance, value
from .effects import reconstruct_utility

SCHEMA_VERSION = 3
AGENTS = {"Wushu": "Jett", "Rift": "Astra", "BountyHunter": "Fade", "Clay": "Raze",
          "Gumshoe": "Cypher", "Deadeye": "Chamber", "Vampire": "Reyna", "Smonk": "Clove", "Hunter": "Sova",
          "Phoenix": "Phoenix", "Killjoy": "Killjoy",
          "Iris": "Miks"}  # User-confirmed in the 13.08 round-one playback review (2026-10-18).
WEAPONS = {"CompactPistol": "Bandit", "BasePistol": "Classic", "LugerPistol": "Ghost",
           "RevolverPistol": "Sheriff", "AssaultRifle_AK": "Vandal", "AssaultRifle_ACR": "Phantom",
           "AssaultRifle_Burst": "Bulldog", "SubMachineGun_MP5": "Spectre", "MP5": "Spectre",
           "Vector": "Stinger", "DMR": "Guardian", "PumpShotgun": "Bucky", "AutomaticShotgun": "Judge",
           "Gun_Deadeye_Q_Pistol": "Headhunter", "Gun_Giantslayer": "Tour de Force",
           "Wushu_Dagger": "Blade Storm", "Wushu_X_Dagger": "Blade Storm",
           "Hunter_LaserMulti": "Hunter's Fury", "Hunter_X_LaserMulti": "Hunter's Fury",
           "Clay_ClusterGrenade": "Paint Shells", "Clay_4_ClusterGrenade": "Paint Shells",
           "Clay_4_Projectile_Secondary": "Paint Shells"}


def name_for_class(path: str | None) -> str | None:
    if not path:
        return None
    for internal, display in WEAPONS.items():
        if internal in path:
            return display
    return None


def fact(kind: str, time_ms: int, source: dict, **kwargs) -> dict:
    return {"id": f"{source['table']}:{source['row']}:{kind}", "kind": kind,
            "time_ms": time_ms, "source": source, **kwargs}


def reconstruct_casts(rows: list[dict]) -> list[dict]:
    """Apply complete packet deltas before identifying a cast; indices can reorder.

    Effects are deliberately not forward-filled or scored in this slice.
    """
    slots: dict = {}
    casts: dict = {}
    pattern = re.compile(r"AbilityCastsThisRound\[(\d+)\]\.([^._]+)_")
    relevant = [r for r in rows if (r.get("field_name") or "").startswith("AbilityCastsThisRound[")]
    for _, packet in groupby(relevant, lambda r: (r["packet_id"], r["actor_net_guid"], r["object_net_guid"])):
        changed: dict = {}
        for row in packet:
            match = pattern.match(row["field_name"])
            if not match or match[2] not in {"Player", "Slot", "Round", "RoundPhase", "CastTime"}:
                continue
            key = (row["actor_net_guid"], row["object_net_guid"], int(match[1]))
            changed.setdefault(key, []).append(row)
        for key, updates in changed.items():
            state = slots.setdefault(key, {})
            for row in updates:
                member = pattern.match(row["field_name"])[2]
                if value(row) is not None:
                    state[member] = value(row)
            required = ("Player", "Slot", "Round", "RoundPhase", "CastTime")
            if not all(k in state for k in required):
                continue
            identity = tuple(state[k] for k in required)
            if identity not in casts:
                casts[identity] = {k: state[k] for k in required}
                casts[identity].update({"source": provenance(updates[0]),
                                        "first_observed_ms": updates[0]["time_ms"]})
    return list(casts.values())


def reconstruct_damage(rows: list[dict], classes: dict[int, str], guid_paths: dict[int, str]) -> list[dict]:
    invocations: list[dict] = []
    current: dict = {}
    for row in rows:
        field = row.get("field_name") or ""
        if not field.startswith("MulticastNotifyDamage") or "." not in field:
            continue
        prefix, member = field.split(".", 1)
        key = (row["packet_id"], row["actor_net_guid"], row["object_net_guid"], prefix)
        if member == "DamageTaken":
            invocation = {"row": row, "values": {}}
            invocations.append(invocation)
            current[key] = invocation
        if key in current:
            current[key]["values"][member] = value(row)
    damage = []
    for invocation in invocations:
        row, values = invocation["row"], invocation["values"]
        weapon_actor = values.get("EquippableUsed")
        raw_class = classes.get(weapon_actor)
        method = "equippable_actor"
        weapon = name_for_class(raw_class)
        if weapon is None:
            for fallback in (classes.get(values.get("DamageCauser")), guid_paths.get(values.get("DamageType"))):
                weapon = name_for_class(fallback)
                if weapon:
                    raw_class, method = fallback, "damage_causer_or_type"
                    break
        sections = []
        for member, component in values.items():
            match = re.fullmatch(r"LifeChangeEvents\[(\d+)\]\.ChangedComponent", member)
            if match:
                stem = f"LifeChangeEvents[{match[1]}]."
                sections.append({"component_id": component,
                                 "result": values.get(stem + "LifeResult"),
                                 "delta": values.get(stem + "DeltaLife")})
        damage.append(fact("damage", row["time_ms"], provenance(row),
                           attacker=values.get("EventInstigatorPawn"), victim=row["actor_net_guid"],
                           actual_damage=values.get("DamageTaken"), raw_damage=values.get("DamageDealt"),
                           lethal=values.get("bDamageKilledTarget"), weapon=weapon,
                           weapon_actor=weapon_actor, weapon_class=raw_class,
                           attribution=method if weapon else "unknown", bone=values.get("DamagedBone"),
                           wall_penetration=values.get("bIsWallPenetration"), life_sections=sections))
    return damage


def reconstruct_scoreboard(rows: list[dict], players: list[dict]) -> dict:
    """Latest replicated aggregate per player; missing counters remain unknown."""
    states = {p["state_id"]: str(p["pawn"]) for p in players}
    fields = {"AggregateKills": "kills", "AggregateDeaths": "deaths", "AggregateAssists": "assists"}
    result = {pawn: {} for pawn in states.values()}
    for row in sorted(rows, key=lambda r: (r["time_ms"], r["source_row"])):
        if row["actor_net_guid"] not in states or row["field_name"] not in fields:
            continue
        number = value(row)
        valid = isinstance(number, int) and not isinstance(number, bool) and number >= 0
        result[states[row["actor_net_guid"]]][fields[row["field_name"]]] = {
            "value": number if valid else None, "observed_time_ms": row["time_ms"],
            "source": provenance(row), "status": "observed" if valid else "unknown_value"}
    return result


def normalize(export: Export) -> dict:
    rows = export.fields()
    actors = export.rows("actors")
    events = export.rows("events")
    classes = {a["actor_net_guid"]: a["class_path"] for a in actors if a["event"] == "open"}
    guids = {g["net_guid"]: g["path"] for g in export.rows("net_guids")}
    by_name: dict[str, list] = defaultdict(list)
    for row in rows:
        by_name[row["field_name"]].append(row)
    players = {}
    for row in by_name["Subject"]:
        if value(row):
            players[row["actor_net_guid"]] = {"id": value(row), "state_id": row["actor_net_guid"],
                                            "source": provenance(row)}
    for row in by_name["AssignedTeamState"]:
        if row["actor_net_guid"] in players:
            players[row["actor_net_guid"]]["team"] = value(row) or packed_guid(row["raw_bits"])
    for row in by_name["SpawnedCharacter"]:
        if row["actor_net_guid"] in players and value(row):
            player = players[row["actor_net_guid"]]
            if player.get("pawn") not in (None, value(row)):
                raise QueryError("unsupported_identity_change", "Pawn identity changes require interval mapping in a later normalizer.")
            player["pawn"] = value(row)
    for player in players.values():
        path = classes.get(player.get("pawn"), "")
        player["agent_class"] = path or None
        internal = re.search(r"/Characters/([^/]+)/", path)
        player["agent_internal"] = internal[1] if internal else None
        player["agent"] = next((display for internal, display in AGENTS.items()
                                if f"/Characters/{internal}/" in path), "Unknown")
        if not player.get("pawn") or not player.get("team"):
            raise QueryError("incomplete_identity", "Cannot establish player pawn and team.")
    if not players:
        raise QueryError("incomplete_identity", "No player identities found.")
    player_list = sorted(players.values(), key=lambda p: p["state_id"])
    pawns = {p["pawn"] for p in player_list}
    # TeamRole is a byte enum in this export, distinct from packed actor GUIDs.
    team_roles = []
    for row in by_name["TeamRole"]:
        if row["actor_net_guid"] in {p["team"] for p in player_list}:
            role = value(row)
            if role is None and row["raw_bits"]:
                role = row["raw_bits"][0]
            team_roles.append({"team": row["actor_net_guid"], "time_ms": row["time_ms"],
                               "role": {1: "attacker", 2: "defender"}.get(role)})
    round_starts = sorted([e for e in events if e["group"] == "roundStarted"], key=lambda e: e["time1"])
    live = sorted(set(r["time_ms"] for r in by_name["ClientBuyPhaseEnd"]))
    rounds = []
    for i, start in enumerate(round_starts):
        end = round_starts[i + 1]["time1"] if i + 1 < len(round_starts) else export.manifest["duration_ms"] + 1
        candidates = [t for t in live if start["time1"] <= t < end]
        if len(candidates) != 1:
            raise QueryError("round_boundary_conflict", f"Round {i + 1}: expected exactly one live-start marker.")
        rounds.append({"number": int(start["word0"]) + 1, "buy_ms": start["time1"],
                       "live_ms": candidates[0], "end_ms": end, "result": {},
                       "source": provenance(start, "events"), "result_sources": {}})
    for row in rows:
        match = re.fullmatch(r"RoundResults\[(\d+)\]\.(\w+)", row.get("field_name") or "")
        if match and int(match[1]) < len(rounds):
            target = rounds[int(match[1])]
            target["result"][match[2]] = value(row)
            target["result_sources"][match[2]] = provenance(row)
            target["result_time_ms"] = row["time_ms"]
    damage = reconstruct_damage(rows, classes, guids)
    timeline = []
    for event in events:
        kind = event["group"]
        if kind == "characterDeath":
            lethal = [d for d in damage if d["lethal"] and d["attacker"] == event["word0"]
                      and d["victim"] == event["word1"] and abs(d["time_ms"] - event["time1"]) <= 100]
            linked = min(lethal, key=lambda d: abs(d["time_ms"] - event["time1"])) if lethal else None
            timeline.append(fact("death", event["time1"], provenance(event, "events"),
                                 killer=event["word0"], victim=event["word1"],
                                 damage_id=linked["id"] if linked else None,
                                 weapon=linked["weapon"] if linked else None))
        elif kind in {"spikePlanted", "spikeDefused", "spikeExploded"}:
            field = {"spikePlanted": "BombPlantedRPC.BombPlanter",
                     "spikeDefused": "BombDefusedRPC.DefusingCharacter"}.get(kind)
            matches = [r for r in by_name.get(field, []) if abs(r["time_ms"] - event["time1"]) <= 100]
            match = min(matches, key=lambda r: abs(r["time_ms"] - event["time1"])) if matches else None
            timeline.append(fact(kind, event["time1"], provenance(event, "events"),
                                 player=value(match) if match else None,
                                 attribution_source=provenance(match) if match else None))
    for row in by_name["CurrentDefuser"]:
        if value(row) in pawns:
            timeline.append(fact("defuse_started", row["time_ms"], provenance(row), player=value(row)))
    for row in by_name["MulticastReceivePlayerResurrectEvent.ResurrectedPlayer"]:
        if value(row) in players:
            timeline.append(fact("resurrection", row["time_ms"], provenance(row), player=players[value(row)]["pawn"]))
    ownership, equipment = [], []
    for row in rows:
        name = row["field_name"]
        if name == "Owner" and name_for_class(classes.get(row["actor_net_guid"])):
            ownership.append(fact("ownership", row["time_ms"], provenance(row), item=row["actor_net_guid"],
                                  owner=value(row), weapon=name_for_class(classes.get(row["actor_net_guid"]))))
        elif name in {"CurrentEquippable", "NewCurrentEquippable"} and row["actor_net_guid"] in pawns:
            equipment.append(fact("equipped", row["time_ms"], provenance(row), player=row["actor_net_guid"],
                                  item=value(row), item_class=classes.get(value(row)),
                                  weapon=name_for_class(classes.get(value(row)))))
    return {"schema_version": SCHEMA_VERSION, "replay_build": export.manifest["replay_build"],
            "map_asset": (export.manifest.get("level_names_and_times") or [{}])[0].get("name"),
            "players": player_list, "scoreboard": reconstruct_scoreboard(rows, player_list),
            "rounds": rounds, "team_roles": team_roles,
            "damage": damage, "timeline": sorted(timeline, key=lambda e: (e["time_ms"], e["id"])),
            "casts": reconstruct_casts(rows), "equipment": equipment, "ownership": ownership,
            "source_quality": export.manifest.get("quality", {}),
            "utility": reconstruct_utility(export, actors, player_list, rounds, timeline, guids)}


def load_match(export: Export, cache_dir: Path | None = None) -> tuple[dict, bool]:
    code_hash = hashlib.sha256(b"".join(Path(__file__).with_name(name).read_bytes()
                                      for name in ("normalize.py", "source.py", "effects.py"))).hexdigest()
    revision = hashlib.sha256(f"{export.fingerprint}:{code_hash}".encode()).hexdigest()
    cache_dir = cache_dir or Path.cwd() / ".valcoach-cache"
    cache_file = cache_dir / f"{revision}.json"
    if cache_file.is_file():
        try:
            model = json.loads(cache_file.read_text(encoding="utf-8"))
            if model.get("revision") == revision and model.get("schema_version") == SCHEMA_VERSION:
                return model, True
        except (OSError, ValueError):
            pass
    model = normalize(export)
    model["revision"] = revision
    model["match_id"] = export.fingerprint[:16]
    cache_dir.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=cache_dir, suffix=".tmp", delete=False) as stream:
            temp_name = stream.name
            json.dump(model, stream, ensure_ascii=False, allow_nan=False)
        os.replace(temp_name, cache_file)
    finally:
        if temp_name and Path(temp_name).exists():
            Path(temp_name).unlink()
    return model, False
