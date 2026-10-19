"""Full-match aggregation of the same evidence returned by round queries."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .query import DEFAULTS, player_labels, query_round
from .source import Export, QueryError

EVENT_KINDS = ("kill", "death", "player_fight", "teammate_fight", "trade", "ability")
OVERVIEW_VERSION = 2


@dataclass
class RoundOverview:
    summary: dict
    review_events: list[dict]
    response_hits: list[dict]


def _sum_known(values) -> float | None:
    values = list(values)
    return None if any(v is None for v in values) else round(sum(values), 4)


def _damage_summary(hits: list[dict]) -> dict:
    return {"hits": len(hits), "actual": _sum_known(d["actual_damage"] for d in hits),
            "raw": _sum_known(d["raw_damage"] for d in hits),
            "unknown_actual_hits": sum(d["actual_damage"] is None for d in hits),
            "unknown_raw_hits": sum(d["raw_damage"] is None for d in hits)}


def _ability_counts(events: list[dict]) -> list[dict]:
    counts = Counter((e["ability"], e["stage"], e["phase"]) for e in events)
    return [{"ability": a, "stage": s, "phase": p, "count": n}
            for (a, s, p), n in sorted(counts.items())]


def _review_event(report: dict, event: dict, kind: str, details: dict,
                  ids: list[str] | None = None) -> dict:
    return {"id": event["id"], "kind": kind, "round": report["round"]["number"],
            "time_ms": event["time_ms"],
            "round_time_ms": event["time_ms"] - report["round"]["live_ms"],
            "post_round": event["time_ms"] > report["round"].get("result_time_ms", report["round"]["end_ms"]),
            "details": details,
            "evidence": {"query": "round", "round": report["round"]["number"],
                         "ids": ids if ids is not None else [event["id"]]}}


def summarize_round(report: dict, opponents: set[int]) -> RoundOverview:
    """Return a compact round, review references, and unique response hits."""
    pawn = report["selected_player"]["pawn"]
    round_ = report["round"]
    deaths = [e for e in report["timeline"] if e["kind"] == "death" and e["victim"] == pawn]
    kills = [e for e in report["timeline"] if e["kind"] == "death"
             and e["killer"] == pawn and e["victim"] in opponents]
    damage = [e for e in report["evidence"].values() if e["kind"] == "damage"]
    outgoing = [e for e in damage if e["attacker"] == pawn and e["victim"] in opponents]
    incoming = [e for e in damage if e["victim"] == pawn and e["attacker"] in opponents]
    abilities = [e for e in report["timeline"] if e["kind"] == "ability"]
    fights = report["teammate_fights"]
    response_ids = {ref for f in fights for ref in f["selected_response"]["damage_ids"]}
    responses = [report["evidence"][ref] for ref in sorted(response_ids)]
    trades = report["trades"]
    completed = [t for t in trades if t["completed"]]
    selected_death_trades = [t for t in trades if t["victim"] == pawn]
    side = round_["selected_side"]
    winner_role = round_["result"].get("WinningTeamRole")
    outcome = ("win" if side == winner_role else "loss") if (
        side in {"attacker", "defender"} and winner_role in {"attacker", "defender"}) else "unknown"
    summary = {"number": round_["number"], "buy_ms": round_["buy_ms"], "live_ms": round_["live_ms"],
               "end_ms": round_["end_ms"], "result_time_ms": round_.get("result_time_ms"),
               "side": side, "outcome": outcome, "result": round_["result"],
               "result_sources": round_.get("result_sources", {}),
               "opponent_kills": len(kills), "death_events": len(deaths),
               "self_attributed_deaths": sum(e["killer"] == pawn for e in deaths),
               "post_round_deaths": sum(e["post_round"] for e in deaths),
               "damage_dealt": _damage_summary(outgoing), "damage_taken": _damage_summary(incoming),
               "player_damage_groups": len(report["player_fights"]),
               "teammate_fights": {"total": len(fights),
                   "without_teammate_death_in_group": sum(not f["teammate_died_in_observed_exchange"] for f in fights),
                   "with_teammate_kill": sum(bool(f.get("opponent_death")) for f in fights),
                   "with_recorded_response": sum(bool(f["selected_response"]["damage_ids"]) for f in fights),
                   "without_recorded_response": sum(not f["selected_response"]["damage_ids"] for f in fights),
                   "unique_response_damage": _damage_summary(responses)},
               "trades": {"eligible_team_deaths": len(trades), "completed_team_trades": len(completed),
                          "by_selected": sum(t["responder"] == pawn for t in completed),
                          "selected_deaths_eligible": len(selected_death_trades),
                          "selected_deaths_traded": sum(t["completed"] for t in selected_death_trades)},
               "jett_abilities": _ability_counts(abilities) if report["selected_player"]["agent"] == "Jett" else None,
               "follow_up": {"query": "round", "round": round_["number"],
                             "player": report["selected_player"]["id"]}}
    return RoundOverview(summary, _round_events(report, kills, deaths, selected_death_trades, completed), responses)


def _round_events(report: dict, kills: list[dict], deaths: list[dict],
                  selected_death_trades: list[dict], completed: list[dict]) -> list[dict]:
    pawn = report["selected_player"]["pawn"]
    records = []
    for event in kills + deaths:
        is_death = event["victim"] == pawn
        trade = next((t for t in selected_death_trades if t["death_id"] == event["id"]), None)
        status = ("completed" if trade["completed"] else "not_observed_within_window") if trade else "outside_trade_scope"
        details = {"killer": event["killer"], "victim": event["victim"], "weapon": event["weapon"],
                   "alive_after_event": event["alive"], "damage_id": event["damage_id"]}
        if is_death:
            details.update({"self_attributed": event["killer"] == pawn, "trade_status": status,
                            "trade_id": trade["id"] if trade else None})
        records.append(_review_event(report, event, "death" if is_death else "kill", details))
    for fight in report["player_fights"]:
        records.append(_review_event(report, fight, "player_fight", {
            key: fight[key] for key in ("opponent", "end_ms", "actual_damage_dealt", "actual_damage_taken", "death_ids")}))
    for fight in report["teammate_fights"]:
        kill = fight.get("opponent_death")
        records.append(_review_event(report, fight, "teammate_fight", {
            "teammate": fight["teammate"], "opponent": fight["opponent"], "end_ms": fight["end_ms"],
            "teammate_died_in_observed_exchange": fight["teammate_died_in_observed_exchange"],
            "opponent_death": kill,
            "recorded_response_damage": fight["selected_response"]["actual_damage"],
            "response_status": fight["selected_response"]["status"], "other_participation": "unknown"},
            [fight["id"], kill["id"]] if kill else [fight["id"]]))
    for trade in completed:
        records.append(_review_event(report, trade, "trade", {
            key: trade[key] for key in ("victim", "opponent", "responder", "delay_ms", "window_ms")},
            [trade["death_id"], trade["response_death_id"]]))
    for event in report["timeline"]:
        if event["kind"] != "ability":
            continue
        records.append(_review_event(report, event, "ability", {
            key: event[key] for key in ("ability", "stage", "phase")}))
    return records


def _totals(rounds: list[dict], responses: list[dict]) -> dict:
    counts = ("opponent_kills", "death_events", "self_attributed_deaths", "post_round_deaths", "player_damage_groups")
    totals = {key: sum(r[key] for r in rounds) for key in counts}
    for direction in ("damage_dealt", "damage_taken"):
        totals[direction] = {key: (_sum_known(r[direction][key] for r in rounds)
                                  if key in {"actual", "raw"} else sum(r[direction][key] for r in rounds))
                             for key in ("hits", "actual", "raw", "unknown_actual_hits", "unknown_raw_hits")}
    totals["teammate_fights"] = {key: sum(r["teammate_fights"][key] for r in rounds)
                                 for key in ("total", "without_teammate_death_in_group", "with_teammate_kill",
                                             "with_recorded_response", "without_recorded_response")}
    totals["teammate_fights"]["unique_response_damage"] = _damage_summary(responses)
    totals["trades"] = {key: sum(r["trades"][key] for r in rounds)
                        for key in ("eligible_team_deaths", "completed_team_trades", "by_selected",
                                    "selected_deaths_eligible", "selected_deaths_traded")}
    return totals


def _round_index(round_: dict) -> dict:
    """Keep detailed source rows and hit lists in the retrievable round query."""
    result = {key: round_[key] for key in (
        "number", "live_ms", "side", "outcome", "opponent_kills", "death_events",
        "self_attributed_deaths", "post_round_deaths", "player_damage_groups", "trades")}
    result["result"] = round_["result"].get("RoundResult")
    for key in ("damage_dealt", "damage_taken"):
        result[key] = {name: round_[key][name] for name in ("actual", "raw")}
    result["teammate_fights"] = {key: val for key, val in round_["teammate_fights"].items()
                                  if key != "unique_response_damage"}
    abilities = Counter()
    for item in round_["jett_abilities"] or []:
        abilities[f"{item['ability']}:{item['stage']}"] += item["count"]
    result["jett_abilities"] = dict(sorted(abilities.items())) if round_["jett_abilities"] is not None else None
    return result


def _quality_summary(quality: dict) -> dict:
    keys = ("malformed_packets", "content_block_framing_failures", "field_stream_failures",
            "rpc_stream_failures", "unresolved_rpc_payloads_preserved")
    return {"content_blocks_lost": quality.get("content_blocks_lost"), "partial_rows": quality.get("partial_rows"),
            "net": {key: quality.get("net", {}).get(key) for key in keys},
            "detail_query": "round.coverage.parser_quality"}


@dataclass
class MatchOverview:
    rounds: list[dict]
    review_events: list[dict]
    response_hits: list[dict]
    coverage: dict
    limitations: list[str]


@dataclass
class ReviewPage:
    events: list[dict]
    index: dict
    pagination: dict


def _collect_rounds(model: dict, export: Export, selected: dict, config: dict) -> MatchOverview:
    opponents = {p["pawn"] for p in model["players"] if p["team"] != selected["team"]}
    rounds, records, responses = [], [], {}
    coverage = {"death_events": 0, "deaths_matched_to_damage": 0, "damage_events": 0}
    limitations = []
    for round_ in sorted(model["rounds"], key=lambda r: r["number"]):
        report = query_round(model, export, round_["number"], selected, config)
        data = summarize_round(report, opponents)
        rounds.append(data.summary)
        records.extend(data.review_events)
        responses.update({hit["id"]: hit for hit in data.response_hits})
        for key in coverage:
            coverage[key] += report["coverage"][key]
        limitations = report["coverage"]["limitations"]
    records.sort(key=lambda e: (e["round"], e["time_ms"], e["kind"], e["id"]))
    return MatchOverview(rounds, records, list(responses.values()), coverage, limitations)


def _review_page(events: list[dict], event_kind: str | None, limit: int, offset: int) -> ReviewPage:
    index = {kind: {"total": sum(e["kind"] == kind for e in events),
                    "rounds": sorted({e["round"] for e in events if e["kind"] == kind})} for kind in EVENT_KINDS}
    matching = [e for e in events if event_kind is None or e["kind"] == event_kind]
    page = matching[offset:offset + limit]
    pagination = {"event_kind": event_kind, "offset": offset, "limit": limit,
                  "total": len(matching), "returned": len(page),
                  "next_offset": offset + len(page) if offset + len(page) < len(matching) else None}
    return ReviewPage(page, index, pagination)


def _scoreboard(model: dict, selected: dict, totals: dict) -> dict:
    observations = model.get("scoreboard", {}).get(str(selected["pawn"]), {})
    values = {key: observations.get(key, {}).get("value") for key in ("kills", "deaths", "assists")}
    differences = {key: None if values[key] is None else totals[metric] - values[key]
                   for key, metric in (("kills", "opponent_kills"), ("deaths", "death_events"))}
    return {**values, "method": "latest_observed_replicated_aggregate",
            "observations": observations, "event_count_difference": differences}


def _ability_totals(rounds: list[dict], selected: dict) -> dict:
    if selected["agent"] != "Jett":
        return {"status": "unsupported_agent", "counts": None}
    counts = Counter()
    for round_ in rounds:
        for item in round_["jett_abilities"]:
            counts[(item["ability"], item["stage"], item["phase"])] += item["count"]
    return {"status": "supported_record_types_only", "counts": [
        {"ability": a, "stage": s, "phase": p, "count": n} for (a, s, p), n in sorted(counts.items())]}


def _teams(model: dict, selected: dict) -> list[dict]:
    fields = ("id", "state_id", "pawn", "agent", "agent_internal")
    return [{"id": team, "selected": team == selected["team"],
             "players": [{key: p.get(key) for key in fields} for p in model["players"] if p["team"] == team]}
            for team in sorted({p["team"] for p in model["players"]})]


def _coverage(data: MatchOverview, page: ReviewPage, model: dict) -> dict:
    return {**data.coverage, "rounds_total": len(data.rounds), "rounds_returned": len(data.rounds),
            "round_totals_complete": True, "truncated": len(page.events) < page.pagination["total"],
            "parser_quality": _quality_summary(model["source_quality"]),
            "limitations": data.limitations + [
                "Recorded scoreboard counters are latest observations; missing counters remain unknown.",
                "Raw death events can differ from scoreboard deaths; post-round deaths are retained.",
                "Zero supported ability records does not establish unused or available utility.",
                "Player fights are pairwise damage groups, not complete tactical engagements.",
                "Only review-event detail is paginated; totals and the round index include every observed round.",
                "Review events are investigation references, not ranked mistakes or established habits."]}


def query_overview(model: dict, export: Export, selected: dict, settings: dict | None = None,
                   limit: int = 20, offset: int = 0, event_kind: str | None = None) -> dict:
    """Complete round totals, with a bounded page of retrievable review events."""
    if not isinstance(limit, int) or not 1 <= limit <= 100 or not isinstance(offset, int) or offset < 0:
        raise QueryError("invalid_pagination", "Use --limit between 1 and 100 and --offset >= 0.")
    if event_kind is not None and event_kind not in EVENT_KINDS:
        raise QueryError("invalid_event_kind", f"Choose an event kind from {', '.join(EVENT_KINDS)}.")
    config = {**DEFAULTS, **(settings or {})}
    data = _collect_rounds(model, export, selected, config)
    page = _review_page(data.review_events, event_kind, limit, offset)
    totals = _totals(data.rounds, data.response_hits)
    score = {"wins": sum(r["outcome"] == "win" for r in data.rounds),
             "losses": sum(r["outcome"] == "loss" for r in data.rounds),
             "unknown": sum(r["outcome"] == "unknown" for r in data.rounds), "scope": "observed_rounds"}
    return {"schema_version": 1, "overview_version": OVERVIEW_VERSION, "query": "overview",
            "match_id": model["match_id"], "revision": model["revision"], "replay_build": model["replay_build"],
            "map_asset": model.get("map_asset"), "selected_player": selected, "players": player_labels(model, selected),
            "teams": _teams(model, selected), "settings": config, "score": score,
            "scoreboard": _scoreboard(model, selected, totals), "totals": totals,
            "rounds": [_round_index(r) for r in data.rounds], "jett_abilities": _ability_totals(data.rounds, selected),
            "review_index": page.index, "review_events": page.events, "pagination": page.pagination,
            "aggregation": {"damage_scope": "player-versus-opponent damage from live start to next buy, including post-round",
                "damage_method": "unique source damage IDs; raw and actual remain separate",
                "response_method": "union of selected-response damage IDs, counted once across overlapping fight windows",
                "round_kd": "observed opponent kills and death events; not reconstructed per-round scoreboard",
                "round_ability_keys": "ability:stage; phase counts combined in round index, separate in match totals",
                "result_method": "compare selected team role with winning role for each round"},
            "evidence_retrieval": {"query": "round", "player": selected["id"],
                "round_argument": "round number from the index or review event", "ids_location": "evidence",
                "result_sources_location": "round.result_sources",
                "settings": "reuse this overview's settings for identical grouping and trades"},
            "coverage": _coverage(data, page, model)}
