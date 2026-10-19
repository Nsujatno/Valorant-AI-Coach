"""Match overview sections; no reconstruction or tactical judgments."""

from .common import amount, cell, coverage_section, death_description, fight_kill_description, player, seconds, table


def _header(result: dict) -> list[str]:
    scoreboard, score, totals = result["scoreboard"], result["score"], result["totals"]
    return [f"# Match overview — {cell(result['selected_player']['agent'])}", "",
            f"Observed round score: **{score['wins']}–{score['losses']}**, with {score['unknown']} unknown results. "
            f"Rounds: {len(result['rounds'])}.", "",
            f"Latest recorded scoreboard K/D/A: **{amount(scoreboard['kills'])}/{amount(scoreboard['deaths'])}/{amount(scoreboard['assists'])}**.", "",
            f"Observed opponent kills: {totals['opponent_kills']}; death events: {totals['death_events']} "
            f"({totals['self_attributed_deaths']} self-attributed; {totals['post_round_deaths']} post-round). "
            "Event counts and scoreboard counters are kept separate.", "",
            f"Opponent damage dealt: {amount(totals['damage_dealt']['actual'])} actual / {amount(totals['damage_dealt']['raw'])} raw; "
            f"taken: {amount(totals['damage_taken']['actual'])} actual / {amount(totals['damage_taken']['raw'])} raw. Includes post-round damage.", "",
            f"Pairwise player damage groups: {totals['player_damage_groups']}. Completed trades by you: {totals['trades']['by_selected']}; "
            f"your deaths traded: {totals['trades']['selected_deaths_traded']}/{totals['trades']['selected_deaths_eligible']} eligible death events.", "",
            f"Match `{result['match_id']}` · revision `{result['revision'][:16]}` · overview v{result['overview_version']}."]


def _round_section(result: dict) -> list[str]:
    rows = []
    for round_ in result["rounds"]:
        fights, trades, abilities = round_["teammate_fights"], round_["trades"], round_["jett_abilities"]
        prep_dash = "unknown" if abilities is None else "/".join(str(abilities.get("Tailwind:" + stage, 0))
            for stage in ("preparation_record", "activation_corroborated"))
        rows.append((round_["number"], round_["side"] or "unknown", round_["outcome"],
                     f"{round_['opponent_kills']}/{round_['death_events']}",
                     f"{amount(round_['damage_dealt']['actual'])}/{amount(round_['damage_taken']['actual'])}",
                     round_["player_damage_groups"], f"{fights['total']}/{fights['with_teammate_kill']}/{fights['with_recorded_response']}",
                     f"{trades['by_selected']}/{trades['selected_deaths_traded']}", prep_dash))
    return ["## Round index", "",
            "K/D below are observed opponent kills/death events. Damage is actual opponent damage. "
            "Prep/dash counts are recorded Tailwind preparations/corroborated activations, not availability or success scores.", "",
            *table(["Round", "Side", "Result", "K/D events", "Damage dealt/taken", "Player groups",
                    "Teammate groups / linked kills / responses", "Trades by you / deaths traded", "Prep/dash"], rows)]


def _contribution_section(result: dict) -> list[str]:
    fights = result["totals"]["teammate_fights"]
    return ["## Teammate contributions", "",
            f"{fights['total']} observed teammate damage groups while you were alive; "
            f"{fights['without_teammate_death_in_group']} without a teammate death in the observed group. "
            f"{fights['with_teammate_kill']} groups include a linked teammate kill. "
            f"{fights['with_recorded_response']} have a recorded damage response, "
            f"{fights['without_recorded_response']} have no recorded damage response.", "",
            f"Your response damage counted once across overlapping windows: {amount(fights['unique_response_damage']['actual'])} actual "
            f"from {fights['unique_response_damage']['hits']} unique hits. Other participation and support opportunity remain unknown."]


def _ability_section(result: dict) -> list[str]:
    counts = result["jett_abilities"]["counts"]
    if counts is None:
        return ["## Jett ability records", "", "Ability reconstruction is unsupported for this selected agent."]
    rows = ((a["ability"], a["stage"], a["phase"], a["count"]) for a in counts)
    return ["## Jett ability records", "", *table(["Ability", "Stage", "Phase", "Records"], rows)]


def _event_description(event: dict, result: dict) -> str:
    details, kind, names = event["details"], event["kind"], result["players"]
    if kind in {"kill", "death"}:
        text = death_description(details, names, result["selected_player"]["pawn"])
        return text + (f"; trade: {details['trade_status']}" if kind == "death" else "")
    if kind == "trade":
        return (f"{player(names, details['responder'])} trades {player(names, details['victim'])}'s death to "
                f"{player(names, details['opponent'])}; {details['delay_ms']} ms")
    if kind == "player_fight":
        return (f"Damage group vs {player(names, details['opponent'])}: dealt {amount(details['actual_damage_dealt'])}, "
                f"took {amount(details['actual_damage_taken'])}")
    if kind == "teammate_fight":
        live_ms = event["time_ms"] - event["round_time_ms"]
        return (f"{player(names, details['teammate'])} vs {player(names, details['opponent'])}; "
                f"{'teammate died' if details['teammate_died_in_observed_exchange'] else 'no teammate death in group'}; "
                    f"{fight_kill_description(details.get('opponent_death'), names, live_ms)}; "
                f"your response: {details['response_status']}, {amount(details['recorded_response_damage'])} actual")
    return f"{details['ability']}: {details['stage']} ({details['phase']})"


def _event_section(result: dict) -> list[str]:
    rows = []
    for event in result["review_events"]:
        text = _event_description(event, result) + (" [post-round]" if event["post_round"] else "")
        refs = ", ".join(f"`{ref}`" for ref in event["evidence"]["ids"])
        rows.append((event["round"], seconds(event["round_time_ms"]), text, refs))
    page = result["pagination"]
    lines = ["## Events to investigate", "",
             "These references include successful and unsuccessful outcomes. They are not tactical verdicts. "
             "Times are seconds since that round's live start; negative times are buy phase.", "",
             *table(["Round", "Time", "Event", "Evidence IDs"], rows), "",
             f"Review events returned: {page['returned']} of {page['total']} matching events; offset {page['offset']}. "
             "Round totals above always cover the full match."]
    if page["next_offset"] is not None:
        lines.append(f"Next page: repeat overview with `--offset {page['next_offset']}` and the same filters/settings.")
    return lines + ["", "Inspect an event's evidence IDs in the JSON evidence from its round:", "", "```powershell",
                    f"python -m valcoach round <round> --export <same-export> --player {result['selected_player']['state_id']} --json", "```"]


def render_overview(result: dict) -> str:
    sections = (_header, _round_section, _contribution_section, _ability_section, _event_section, coverage_section)
    return "\n\n".join("\n".join(section(result)) for section in sections) + "\n"
