"""Readable views generated from exactly the same facts as the JSON query."""

from __future__ import annotations


def render_round(result: dict) -> str:
    names = result["players"]

    def label(pawn):
        return names.get(str(pawn), "unresolved/non-player owner" if pawn else "unknown")

    def escape(text):
        return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")

    def ms(time):
        return f"{(time-result['round']['live_ms'])/1000:+.3f}s"

    def amount(number):
        return "unknown" if number is None else f"{number:.2f}"

    lines = [f"# Round {result['round']['number']} — {result['selected_player']['agent']}", "",
             f"Side: {result['round']['selected_side'] or 'unknown'}. "
             f"Live starts at replay {result['round']['live_ms']/1000:.3f}s. "
             f"Result: {result['round']['result'].get('RoundResult', 'unknown')}.", "",
             "Times below are seconds since live start; negative times are buy phase. "
             "Lethal damage is aligned to its matched death event; JSON preserves its original observed timestamp.", "",
             f"Match `{result['match_id']}` · revision `{result['revision'][:16]}`.", "",
             "## Timeline", "", "| Time | Event | Alive: us/them | Evidence |",
             "| --- | --- | --- | --- |"]
    for event in result["timeline"]:
        kind = event["kind"]
        if kind == "damage":
            text = (f"{label(event['attacker'])} → {label(event['victim'])}: "
                    f"{amount(event['actual_damage'])} actual damage, {amount(event['raw_damage'])} raw; "
                    f"{event['weapon'] or 'unknown weapon'}")
            if event.get("wall_penetration"):
                text += "; wall penetration"
        elif kind == "death":
            if event["killer"] == event["victim"]:
                text = f"{label(event['victim'])}: self-attributed death — {event['weapon'] or 'unknown cause'}"
            else:
                verb = "kill" if event["killer"] == result["selected_player"]["pawn"] else "kills"
                text = f"{label(event['killer'])} {verb} {label(event['victim'])} — {event['weapon'] or 'unknown weapon'}"
        elif kind in {"spikePlanted", "spikeDefused", "defuse_started", "spikeExploded", "resurrection"}:
            text = f"{kind}: {label(event.get('player'))}"
        elif kind == "ability":
            text = f"{event['ability']}: {event['stage']}"
            if event.get("movement_start_ms") is not None:
                text += f"; movement {ms(event['movement_start_ms'])}–{ms(event['movement_end_ms'])}, peak {event['peak_speed_m_s']:.2f} m/s"
        elif kind == "teammate_fight":
            distance = event["selected_distance_at_start"]["distance_m"]
            text = (f"Fight: {label(event['teammate'])} vs {label(event['opponent'])}; "
                    f"teammate dealt {amount(event['teammate_damage_dealt'])}, took {amount(event['teammate_damage_taken'])}; "
                    f"{'died' if event['teammate_died_in_observed_exchange'] else 'no death in observed exchange'}; "
                    f"your response: {event['selected_response']['status'].replace('_', ' ')}")
            text += f"; distance {distance:.2f} m" if distance is not None else "; distance unknown"
        elif kind == "ownership":
            text = f"{event['weapon']} item {event['item']}: owner becomes {label(event['owner'])}"
        else:
            text = kind
        if event.get("post_round"):
            text += " [post-round]"
        counts = event["alive"]
        lines.append(f"| {ms(event['time_ms'])} | {escape(text)} | {counts['selected_team']}/{counts['opponents']} | `{event['id']}` |")
    lines += ["", "## Teammate fight context", "",
              "Every damage-based teammate fight while you were alive is included, with no distance cutoff. "
              "Response windows can overlap fights; contribution totals must not be summed across summaries. "
              "Survival refers to the observed exchange, not the whole round.", ""]
    for fight in result["teammate_fights"]:
        response = fight["selected_response"]
        lines.append(f"- {ms(fight['time_ms'])}–{ms(fight['end_ms'])}: {label(fight['teammate'])} vs "
                     f"{label(fight['opponent'])}. Your recorded damage: {amount(response['actual_damage'])}; "
                     f"response references: {', '.join(response['damage_ids']) or 'none'}. "
                     "Other participation and actual support opportunity: unknown.")
    lines += ["", "## Observed trades", "",
              f"A completed trade here means the killer died to a teammate within {result['settings']['trade_window_ms']/1000:g}s, "
              "without an intervening relevant resurrection. This is an event sequence, not a judgment about what was possible.", ""]
    for trade in result["trades"]:
        text = f"{label(trade['victim'])} died at {ms(trade['time_ms'])} to {label(trade['opponent'])}. "
        text += (f"{label(trade['responder'])} killed that opponent {trade['delay_ms']/1000:.3f}s later."
                 if trade["completed"] else "No completed trade observed within the configured window.")
        lines.append(f"- {text} Evidence: `{trade['death_id']}`" +
                     (f", `{trade['response_death_id']}`." if trade["response_death_id"] else "."))
    lines += ["", "## Coverage and limits", "",
              f"Death events matched to lethal damage: {result['coverage']['deaths_matched_to_damage']}/{result['coverage']['death_events']}.", ""]
    lines += [f"- {limit}" for limit in result["coverage"]["limitations"]]
    return "\n".join(lines) + "\n"
