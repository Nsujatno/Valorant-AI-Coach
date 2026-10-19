"""Readable utility evidence shared by round and spatial documents."""

from .common import player, seconds, table


def utility_section(result):
    data = result.get("utility")
    if not data or data["status"] == "not_loaded":
        return []
    rows = []
    for record in data["records"]:
        if record["kind"] == "blind_update":
            rows.append((seconds(record["round_time_ms"]), "unknown", player(result["players"], record["player"]),
                         "Raw blind-array update; strength/duration undecoded", record["id"]))
            continue
        subject = record.get("player", record.get("owner"))
        label = record.get("label", record.get("category", "unknown"))
        rows.append((seconds(record["round_start_ms"]),
                     seconds(record["round_end_ms"]) if record["round_end_ms"] is not None else "unknown",
                     player(result["players"], subject), label + " — " + record["pair_status"], record["id"]))
    return ["## Utility observations", "",
            "Smoke rows show actor lifetime, not confirmed opaque duration. Status rows show visual-effect intervals; "
            "severity and actual impairment remain unknown. An empty list does not establish no utility.", "",
            *table(["Start", "End", "Player/owner", "Observation", "Evidence"], rows), "",
            f"Returned {data['returned']}/{data['total']} records; truncated: {data['truncated']}."]
