"""Shared Markdown and value formatting for readable query results."""

from collections.abc import Iterable


def cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def amount(number: float | None) -> str:
    return "unknown" if number is None else f"{number:g}"


def seconds(time_ms: int) -> str:
    return f"{time_ms/1000:+.3f}s"


def player(names: dict, pawn: int | None) -> str:
    return names.get(str(pawn), "unresolved/non-player owner" if pawn else "unknown")


def table(headers: list[str], rows: Iterable[Iterable[object]]) -> list[str]:
    return ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |",
            *("| " + " | ".join(cell(value) for value in row) + " |" for row in rows)]


def death_description(event: dict, names: dict, selected_pawn: int) -> str:
    victim = player(names, event["victim"])
    if event["killer"] == event["victim"]:
        return f"{victim}: self-attributed death — {event['weapon'] or 'unknown cause'}"
    verb = "kill" if event["killer"] == selected_pawn else "kills"
    return f"{player(names, event['killer'])} {verb} {victim} — {event['weapon'] or 'unknown weapon'}"


def fight_kill_description(death: dict | None, names: dict, live_ms: int) -> str:
    if death is None:
        return "no opponent kill linked to this damage group"
    return (f"{player(names, death['killer'])} kills {player(names, death['victim'])} "
            f"with {death['weapon'] or 'unknown weapon'} at {seconds(death['time_ms']-live_ms)}")


def coverage_section(result: dict) -> list[str]:
    coverage = result["coverage"]
    return ["## Coverage and limits", "",
            f"Death events matched to lethal damage: {coverage['deaths_matched_to_damage']}/{coverage['death_events']}.", "",
            *(f"- {limit}" for limit in coverage["limitations"])]
