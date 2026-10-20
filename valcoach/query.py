"""Round queries and explicit, configurable fight/trade heuristics."""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
import math

from .normalize import fact
from .source import Export, QueryError, provenance
from .utility import fight_utility_refs, utility_window
from .resources import resource_window

DEFAULTS = {"engagement_gap_ms": 3000, "trade_window_ms": 5000,
            "response_before_ms": 3000, "response_after_ms": 5000,
            "movement_max_age_ms": 250, "dash_speed_cm_s": 900,
            "dash_max_sample_gap_ms": 100, "death_replication_tolerance_ms": 100,
            "heuristic_version": 1}


def damage_total(hits: list[dict]) -> float | None:
    if any(d["actual_damage"] is None for d in hits):
        return None
    return round(sum(d["actual_damage"] for d in hits), 4)


def select_player(model: dict, selector: str) -> dict:
    matches = [p for p in model["players"] if selector.casefold() in
               {p["id"].casefold(), str(p["state_id"]), str(p["pawn"]), p["agent"].casefold()}]
    if len(matches) != 1:
        raise QueryError("ambiguous_player" if matches else "unknown_player",
                         "Select a unique subject/state/pawn ID from the players command; agent names can be ambiguous.")
    return matches[0]


def player_labels(model: dict, selected: dict) -> dict[str, str]:
    labels = {}
    for player in model["players"]:
        relation = "teammate " if player["team"] == selected["team"] else "enemy "
        agent = player["agent"] if player["agent"] != "Unknown" else f"agent {player.get('agent_internal') or 'unknown'}"
        labels[str(player["pawn"])] = "you" if player["pawn"] == selected["pawn"] else relation + agent
    return labels


def alive(model: dict, round_: dict, pawn: int, time_ms: int, before: bool = False) -> bool:
    state = True
    for event in model["timeline"]:
        if event["time_ms"] < round_["live_ms"]:
            continue
        if event["time_ms"] > time_ms or (before and event["time_ms"] == time_ms):
            break
        if event["kind"] == "death" and event["victim"] == pawn:
            state = False
        elif event["kind"] == "resurrection" and event["player"] == pawn:
            state = True
    return state


class Positions:
    def __init__(self, rows: list[dict], max_age_ms: int):
        self.max_age_ms = max_age_ms
        self.rows: dict[int, list] = defaultdict(list)
        for row in sorted(rows, key=lambda r: (r["time_ms"], r["source_row"])):
            self.rows[row["character_net_guid"]].append(row)
        self.times = {pawn: [r["time_ms"] for r in samples] for pawn, samples in self.rows.items()}

    def at(self, pawn: int, time_ms: int) -> dict | None:
        index = bisect_right(self.times.get(pawn, []), time_ms) - 1
        if index < 0:
            return None
        row = self.rows[pawn][index]
        if time_ms - row["time_ms"] > self.max_age_ms:
            return None
        return row

    def distance(self, first: int, second: int, time_ms: int) -> dict:
        a, b = self.at(first, time_ms), self.at(second, time_ms)
        if a is None or b is None:
            return {"distance_m": None, "reason": "missing_or_stale_movement", "max_age_ms": self.max_age_ms}
        distance = math.sqrt(sum((a[f"pos_{axis}"] - b[f"pos_{axis}"]) ** 2 for axis in "xyz")) / 100
        return {"distance_m": round(distance, 3), "sample_ages_ms": [time_ms-a["time_ms"], time_ms-b["time_ms"]],
                "sources": [provenance(a, "movement"), provenance(b, "movement")],
                "method": "last_observed_samples_no_interpolation", "max_age_ms": self.max_age_ms}


def trade_outcomes(model: dict, round_: dict, settings: dict) -> list[dict]:
    events = [e for e in model["timeline"] if round_["live_ms"] <= e["time_ms"] < round_["end_ms"]
              and e["time_ms"] <= round_.get("result_time_ms", round_["end_ms"])]
    teams = {p["pawn"]: p["team"] for p in model["players"]}
    results = []
    for index, death in enumerate(events):
        if death["kind"] != "death" or teams.get(death["killer"]) == teams.get(death["victim"]):
            continue
        response = None
        stopped_by = None
        for later in events[index + 1:]:
            if later["time_ms"] - death["time_ms"] > settings["trade_window_ms"]:
                break
            if later["kind"] == "resurrection" and later["player"] in (death["killer"], death["victim"]):
                stopped_by = later["id"]
                break
            if later["kind"] == "death" and later["victim"] == death["killer"]:
                if teams.get(later["killer"]) == teams.get(death["victim"]) and later["killer"] != death["victim"]:
                    response = later
                break
        results.append({"id": f"trade:{death['id']}", "death_id": death["id"],
                        "time_ms": death["time_ms"], "victim": death["victim"], "opponent": death["killer"],
                        "completed": response is not None, "response_death_id": response["id"] if response else None,
                        "responder": response["killer"] if response else None,
                        "delay_ms": response["time_ms"] - death["time_ms"] if response else None,
                        "window_ms": settings["trade_window_ms"], "stopped_by_resurrection": stopped_by,
                        "interpretation": "observed_sequence_only; opportunity_unknown"})
    return results


def group_fights(damage: list[dict], model: dict, round_: dict, settings: dict) -> list[list[dict]]:
    groups: dict[tuple, list[list]] = defaultdict(list)
    life = [e for e in model["timeline"] if e["kind"] in ("death", "resurrection")]
    for hit in sorted(damage, key=lambda d: (d["combat_time_ms"], d["id"])):
        pair = tuple(sorted((hit["attacker"], hit["victim"])))
        bucket = groups[pair]
        last = bucket[-1][-1] if bucket else None
        life_break = last is not None and any(
            last["combat_time_ms"] <= e["time_ms"] < hit["combat_time_ms"] and
            (e.get("victim") if e["kind"] == "death" else e.get("player")) in pair and
            (e["kind"] == "resurrection" or hit["combat_time_ms"]-e["time_ms"] > settings["death_replication_tolerance_ms"])
            for e in life)
        if last is None or life_break or hit["combat_time_ms"] - last["combat_time_ms"] > settings["engagement_gap_ms"]:
            bucket.append([])
        bucket[-1].append(hit)
    return sorted((g for bucket in groups.values() for g in bucket), key=lambda g: g[0]["combat_time_ms"])


def teammate_fights(model: dict, round_: dict, selected: dict, positions: Positions,
                    settings: dict, damage: list[dict], trades: list[dict]) -> list[dict]:
    players = {p["pawn"]: p for p in model["players"]}
    selected_pawn = selected["pawn"]
    all_pair_damage = [d for d in damage if d["attacker"] in players and d["victim"] in players
                       and players[d["attacker"]]["team"] != players[d["victim"]]["team"]]
    summaries = []
    for group in group_fights(all_pair_damage, model, round_, settings):
        pair = {group[0]["attacker"], group[0]["victim"]}
        teammate = next((p for p in pair if p != selected_pawn and players[p]["team"] == selected["team"]), None)
        if teammate is None:
            continue
        opponent = next(p for p in pair if p != teammate)
        visible = [d for d in group if alive(model, round_, selected_pawn, d["combat_time_ms"], before=True)]
        if not visible:
            continue
        start, end = visible[0]["combat_time_ms"], visible[-1]["combat_time_ms"]
        # A trailing projectile can land after the selected player's death; clip here.
        responses = [d for d in damage if d["attacker"] == selected_pawn and d["victim"] == opponent
                     and start-settings["response_before_ms"] <= d["combat_time_ms"] <= end+settings["response_after_ms"]
                     and alive(model, round_, selected_pawn, d["combat_time_ms"], before=True)
                     and not any(e["kind"] == "resurrection" and e["player"] == opponent
                                 and min(start, d["combat_time_ms"]) < e["time_ms"] <= max(end, d["combat_time_ms"])
                                 for e in model["timeline"])]
        damage_ids = {d["id"] for d in visible}
        linked_deaths = {e["victim"]: e for e in model["timeline"] if e["kind"] == "death"
                         and e["damage_id"] in damage_ids}
        death = linked_deaths.get(teammate)
        opponent_death = linked_deaths.get(opponent)
        outcome = next((t for t in trades if death and t["death_id"] == death["id"]), None)
        summaries.append({"id": f"fight:{visible[0]['id']}:{teammate}", "kind": "teammate_fight",
                          "time_ms": start, "end_ms": end, "teammate": teammate, "opponent": opponent,
                          "damage_ids": [d["id"] for d in visible],
                          "teammate_damage_dealt": damage_total([d for d in visible if d["attacker"] == teammate]),
                          "teammate_damage_taken": damage_total([d for d in visible if d["victim"] == teammate]),
                          "teammate_died_in_observed_exchange": bool(death),
                          "opponent_death": {key: opponent_death.get(key) for key in
                                             ("id", "time_ms", "killer", "victim", "weapon", "damage_id")}
                                            if opponent_death else None,
                          "survival_scope": "observed damage group only, clipped to selected player's live intervals",
                          "selected_response": {"status": "recorded_damage_contribution" if responses else "no_recorded_damage_contribution",
                                                "damage_ids": [d["id"] for d in responses],
                                                "actual_damage": damage_total(responses),
                                                "first_delay_from_fight_start_ms": responses[0]["combat_time_ms"]-start if responses else None,
                                                "window_before_ms": settings["response_before_ms"],
                                                "window_after_ms": settings["response_after_ms"],
                                                "other_participation": "unknown"},
                          "selected_distance_at_start": positions.distance(selected_pawn, teammate, start),
                          "trade": outcome, "review_lead": "teammate_fight_context; support_opportunity_unknown"})
    return summaries


def jett_abilities(model: dict, round_: dict, selected: dict, positions: Positions, settings: dict) -> list[dict]:
    if selected["agent"] != "Jett":
        return []
    abilities = []
    for cast in model["casts"]:
        if cast["Player"] != selected["id"] or cast["Round"] + 1 != round_["number"]:
            continue
        slot = cast["Slot"]
        if slot not in {3, 4, 5, 9}:
            continue
        phase = cast["RoundPhase"]
        origin = {3: round_["buy_ms"], 4: round_["live_ms"]}.get(phase)
        time_ms = origin + round(cast["CastTime"] * 1000) if origin is not None else cast["first_observed_ms"]
        abilities.append(fact("ability", time_ms, cast["source"], player=selected["pawn"],
                              ability={3: "Cloudburst", 4: "Updraft", 5: "Tailwind", 9: "Blade Storm"}[slot],
                              stage="preparation_record" if slot == 5 else "cast_record",
                              phase={3: "buy", 4: "live", 5: "post"}.get(phase, "unknown"),
                              timing_method="phase_relative_cast_time" if origin is not None else "first_observed_only",
                              first_observed_ms=cast["first_observed_ms"], effects="not_reconstructed_in_first_slice"))
    for equip in model["equipment"]:
        if equip["player"] != selected["pawn"] or not round_["live_ms"] <= equip["time_ms"] < round_["end_ms"]:
            continue
        if "Ability_Wushu_E_Dash" not in (equip["item_class"] or ""):
            continue
        fast = [r for r in positions.rows.get(selected["pawn"], [])
                if equip["time_ms"]-60 <= r["time_ms"] <= equip["time_ms"]+800
                and math.hypot(r["vel_x"], r["vel_y"]) > settings["dash_speed_cm_s"]
                and alive(model, round_, selected["pawn"], r["time_ms"])]
        contiguous = []
        for row in fast:
            if contiguous and row["time_ms"]-contiguous[-1]["time_ms"] > settings["dash_max_sample_gap_ms"]:
                break
            contiguous.append(row)
        corroborated = len(contiguous) >= 2
        event = fact("ability", equip["time_ms"], equip["source"], player=selected["pawn"], ability="Tailwind",
                     stage="activation_corroborated" if corroborated else "dash_equip_unconfirmed",
                     phase="live", method="dash_equippable_plus_horizontal_speed" if corroborated else "dash_equippable_only",
                     movement_start_ms=contiguous[0]["time_ms"] if corroborated else None,
                     movement_end_ms=contiguous[-1]["time_ms"] if corroborated else None,
                     peak_speed_m_s=round(max(math.hypot(r["vel_x"], r["vel_y"]) for r in contiguous)/100, 3) if corroborated else None,
                     movement_sources=[provenance(r, "movement") for r in (contiguous[0], contiguous[-1])] if corroborated else [],
                     limitation="activation heuristic validated on reference cases; velocity alone is not a dash")
        abilities.append(event)
    return sorted(abilities, key=lambda e: (e["time_ms"], e["id"]))


def player_fights(model: dict, round_: dict, selected: dict, settings: dict,
                  damage: list[dict], deaths: list[dict]) -> list[dict]:
    """Pairwise damage groups, using the same grouping rule as teammate fights."""
    opponents = {p["pawn"] for p in model["players"] if p["team"] != selected["team"]}
    pawn = selected["pawn"]
    hits = [d for d in damage if (d["attacker"] == pawn and d["victim"] in opponents)
            or (d["victim"] == pawn and d["attacker"] in opponents)]
    result = []
    for group in group_fights(hits, model, round_, settings):
        opponent = next(p for p in (group[0]["attacker"], group[0]["victim"]) if p != pawn)
        ids = {d["id"] for d in group}
        linked = [e for e in deaths if e["damage_id"] in ids]
        result.append({"id": f"player_fight:{group[0]['id']}:{pawn}", "kind": "player_fight",
                       "time_ms": group[0]["combat_time_ms"], "end_ms": group[-1]["combat_time_ms"],
                       "round_time_ms": group[0]["combat_time_ms"] - round_["live_ms"],
                       "player": pawn, "opponent": opponent, "damage_ids": [d["id"] for d in group],
                       "actual_damage_dealt": damage_total([d for d in group if d["attacker"] == pawn]),
                       "actual_damage_taken": damage_total([d for d in group if d["victim"] == pawn]),
                       "death_ids": [e["id"] for e in linked],
                       "method": "pairwise_damage_group; not a complete tactical engagement"})
    return result


def query_round(model: dict, export: Export, number: int, selected: dict, settings: dict | None = None) -> dict:
    config = {**DEFAULTS, **(settings or {})}
    for name, setting in config.items():
        if name.endswith("_ms") and (not isinstance(setting, int) or setting < 0):
            raise QueryError("invalid_settings", f"{name} must be a nonnegative integer.")
    round_ = next((r for r in model["rounds"] if r["number"] == number), None)
    if round_ is None:
        raise QueryError("unknown_round", f"Round {number} does not exist in this export.")
    players = {p["pawn"]: p for p in model["players"]}
    movement = export.movement(max(0, round_["buy_ms"]-config["movement_max_age_ms"]), round_["end_ms"], list(players))
    positions = Positions(movement, config["movement_max_age_ms"])
    timeline = [dict(e) for e in model["timeline"] if round_["live_ms"] <= e["time_ms"] < round_["end_ms"]]
    lethal_times = {e["damage_id"]: e["time_ms"] for e in timeline if e["kind"] == "death" and e["damage_id"]}
    damage = [{**d, "combat_time_ms": lethal_times.get(d["id"], d["time_ms"])} for d in model["damage"]
              if round_["live_ms"] <= d["time_ms"] < round_["end_ms"] and d["victim"] in players]
    for hit in damage:
        hit["observed_time_ms"] = hit["time_ms"]
        hit["time_ms"] = hit["combat_time_ms"]
    selected_damage = [d for d in damage if selected["pawn"] in (d["attacker"], d["victim"])]
    trades = trade_outcomes(model, round_, config)
    team_fights = teammate_fights(model, round_, selected, positions, config, damage, trades)
    abilities = jett_abilities(model, round_, selected, positions, config)
    touched = {o["item"] for o in model["ownership"] if o["owner"] == selected["pawn"]
               and o["time_ms"] < round_["end_ms"] and any(
                   i["item"] == o["item"] and i["player"] == selected["pawn"]
                   and round_["buy_ms"] <= i["time_ms"] < round_["end_ms"] for i in model["equipment"])}
    ownership = [dict(o) for o in model["ownership"] if o["item"] in touched and round_["buy_ms"] <= o["time_ms"] < round_["end_ms"]]
    equipment = [dict(e) for e in model.get("equipment", []) if e["player"] == selected["pawn"]
                 and round_["buy_ms"] <= e["time_ms"] < round_["end_ms"]]
    timeline.extend(selected_damage + abilities + team_fights + ownership + equipment)
    priority = {"damage": 0, "death": 1, "resurrection": 2}
    timeline.sort(key=lambda e: (e.get("combat_time_ms", e["time_ms"]), priority.get(e["kind"], 3), e["id"]))
    alive_pawns = set(players)
    for event in timeline:
        if event["kind"] == "death":
            alive_pawns.discard(event["victim"])
        elif event["kind"] == "resurrection":
            alive_pawns.add(event["player"])
        event["round_time_ms"] = event["time_ms"] - round_["live_ms"]
        event["post_round"] = event["time_ms"] > round_.get("result_time_ms", round_["end_ms"])
        event["alive"] = {"selected_team": sum(players[p]["team"] == selected["team"] for p in alive_pawns),
                          "opponents": sum(players[p]["team"] != selected["team"] for p in alive_pawns)}
    roles = [r for r in model["team_roles"] if r["team"] == selected["team"] and r["time_ms"] <= round_["live_ms"]]
    deaths = [e for e in timeline if e["kind"] == "death"]
    own_fights = player_fights(model, round_, selected, config, damage, deaths)
    utility = utility_window(model, round_, round_["buy_ms"], round_["end_ms"])
    utility_evidence = {r["id"]: r for r in utility["records"]}
    for fight in team_fights + own_fights:
        fight["utility_context"] = fight_utility_refs(model, fight, selected["pawn"])
        # Resolve referenced intervals even when the broad round list is capped.
        refs = set(fight["utility_context"]["status_ids"] + fight["utility_context"]["smoke_ids"])
        for records in (model.get("utility", {}).get("statuses", []), model.get("utility", {}).get("smokes", [])):
            for record in records:
                if record["id"] in refs and record["id"] not in utility_evidence:
                    utility_evidence[record["id"]] = {k: v for k, v in record.items() if k != "positions"}
    evidence = {e["id"]: e for e in damage + timeline + own_fights}
    evidence.update(utility_evidence)
    resources = resource_window(model, round_, selected["pawn"], round_["buy_ms"], round_["end_ms"])
    return {"schema_version": 1, "round_version": 4, "match_id": model["match_id"], "revision": model["revision"],
            "query": "round", "settings": config, "round": {**round_, "selected_side": roles[-1]["role"] if roles else None},
            "selected_player": selected, "players": player_labels(model, selected), "timeline": timeline, "teammate_fights": team_fights,
            "player_fights": own_fights,
            "utility": utility,
            "resources": resources,
            "trades": [t for t in trades if players.get(t["victim"], {}).get("team") == selected["team"]],
            "evidence": evidence,
            "coverage": {"death_events": len(deaths), "deaths_matched_to_damage": sum(bool(e["damage_id"]) for e in deaths),
                         "damage_events": len(damage), "teammate_fights": len(team_fights),
                         "equipment_observations": len(equipment), "ammunition_support": "component_observations",
                         "resource_observations": resources["total"], "resource_observations_omitted": resources["omitted"],
                         "ability_support": "Jett cast records and corroborated Tailwind only" if selected["agent"] == "Jett" else "unsupported_agent",
                         "parser_quality": model["source_quality"], "truncated": bool(resources["omitted"]),
                         "limitations": ["Export observations do not establish complete replay semantics.",
                                         "Damage-only fights omit missed shots and fights without damage.",
                                         "No recorded damage contribution does not mean no participation.",
                                         "Distances do not establish visibility, knowledge, access, or trade opportunity.",
                                         "Resources are source observations; full shot logs, reload completion and effective ability readiness are unknown.",
                                         "Other agents' detailed casts and utility effect scoring are not reconstructed.",
                                         "Player display names and all-agent utility mapping are not required.",
                                         "Trade and response windows are configurable heuristics, not tactical verdicts."]}}
