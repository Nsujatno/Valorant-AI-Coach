"""Round-report sections, derived solely from the round query response."""

from .common import amount, cell, coverage_section, death_description, fight_kill_description, player, seconds, table
from .utility import utility_section


def _header(result: dict) -> list[str]:
    round_ = result["round"]
    return [f"# Round {round_['number']} — {cell(result['selected_player']['agent'])}", "",
            f"Side: {round_['selected_side'] or 'unknown'}. Live starts at replay {round_['live_ms']/1000:.3f}s. "
            f"Result: {round_['result'].get('RoundResult', 'unknown')}.", "",
            "Times below are seconds since live start; negative times are buy phase. "
            "Lethal damage is aligned to its matched death event; JSON preserves its original observed timestamp.", "",
            f"Match `{result['match_id']}` · revision `{result['revision'][:16]}`."]


def _fight_description(event: dict, names: dict, live_ms: int) -> str:
    distance = event["selected_distance_at_start"]["distance_m"]
    text = (f"Fight: {player(names, event['teammate'])} vs {player(names, event['opponent'])}; "
            f"teammate dealt {amount(event['teammate_damage_dealt'])}, took {amount(event['teammate_damage_taken'])}; "
            f"{'died' if event['teammate_died_in_observed_exchange'] else 'no death in observed exchange'}; "
            f"{fight_kill_description(event.get('opponent_death'), names, live_ms)}; "
            f"your response: {event['selected_response']['status'].replace('_', ' ')}")
    return text + (f"; distance {distance:.2f} m" if distance is not None else "; distance unknown")


def _ability_description(event: dict, live_ms: int) -> str:
    text = f"{event['ability']}: {event['stage']}"
    if event.get("movement_start_ms") is not None:
        text += (f"; movement {seconds(event['movement_start_ms']-live_ms)}–"
                 f"{seconds(event['movement_end_ms']-live_ms)}, peak {event['peak_speed_m_s']:.2f} m/s")
    return text


def _event_description(event: dict, result: dict) -> str:
    kind, names = event["kind"], result["players"]
    if kind == "damage":
        text = (f"{player(names, event['attacker'])} → {player(names, event['victim'])}: "
                f"{amount(event['actual_damage'])} actual damage, {amount(event['raw_damage'])} raw; "
                f"{event['weapon'] or 'unknown weapon'}")
        return text + ("; wall penetration" if event.get("wall_penetration") else "")
    if kind == "death":
        return death_description(event, names, result["selected_player"]["pawn"])
    if kind == "ability":
        return _ability_description(event, result["round"]["live_ms"])
    if kind == "teammate_fight":
        return _fight_description(event, names, result["round"]["live_ms"])
    if kind == "ownership":
        return f"{event['weapon']} item {event['item']}: owner becomes {player(names, event['owner'])}"
    if kind == "equipped":
        return f"Equip observation: {event.get('weapon') or event.get('item_class') or 'no item / unknown'}; remaining ammo/charges unknown"
    if kind in {"spikePlanted", "spikeDefused", "defuse_started", "spikeExploded", "resurrection"}:
        return f"{kind}: {player(names, event.get('player'))}"
    return kind


def _timeline_section(result: dict) -> list[str]:
    rows = []
    for event in result["timeline"]:
        text = _event_description(event, result) + (" [post-round]" if event.get("post_round") else "")
        counts = event["alive"]
        rows.append((seconds(event["round_time_ms"]), text,
                     f"{counts['selected_team']}/{counts['opponents']}", f"`{event['id']}`"))
    return ["## Timeline", "", *table(["Time", "Event", "Alive: us/them", "Evidence"], rows)]


def _fight_section(result: dict) -> list[str]:
    lines = ["## Teammate fight context", "",
             "Every damage-based teammate fight while you were alive is included, with no distance cutoff. "
             "Response windows can overlap fights; contribution totals must not be summed across summaries. "
             "Survival refers to the observed exchange, not the whole round.", ""]
    names, live_ms = result["players"], result["round"]["live_ms"]
    for fight in result["teammate_fights"]:
        response = fight["selected_response"]
        lines.append(f"- {seconds(fight['time_ms']-live_ms)}–{seconds(fight['end_ms']-live_ms)}: "
                     f"{player(names, fight['teammate'])} vs {player(names, fight['opponent'])}. "
                     f"Outcome: {fight_kill_description(fight.get('opponent_death'), names, live_ms)}. "
                     f"Your recorded damage: {amount(response['actual_damage'])}; "
                     f"response references: {', '.join(response['damage_ids']) or 'none'}. "
                     "Other participation and actual support opportunity: unknown.")
    return lines


def _trade_section(result: dict) -> list[str]:
    lines = ["## Observed trades", "",
             f"A completed trade here means the killer died to a teammate within {result['settings']['trade_window_ms']/1000:g}s, "
             "without an intervening relevant resurrection. This is an event sequence, not a judgment about what was possible.", ""]
    names, live_ms = result["players"], result["round"]["live_ms"]
    for trade in result["trades"]:
        text = f"{player(names, trade['victim'])} died at {seconds(trade['time_ms']-live_ms)} to {player(names, trade['opponent'])}. "
        text += (f"{player(names, trade['responder'])} killed that opponent {trade['delay_ms']/1000:.3f}s later."
                 if trade["completed"] else "No completed trade observed within the configured window.")
        lines.append(f"- {text} Evidence: `{trade['death_id']}`" +
                     (f", `{trade['response_death_id']}`." if trade["response_death_id"] else "."))
    return lines


def render_round(result: dict) -> str:
    sections = (_header, _timeline_section, _fight_section, _trade_section, utility_section, coverage_section)
    return "\n\n".join("\n".join(section(result)) for section in sections) + "\n"
