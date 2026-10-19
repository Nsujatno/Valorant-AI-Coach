"""Small readable investigation views; tactical interpretation belongs to the host."""

import json

from .common import amount, player, seconds, table


def render_fight(result):
    names = result["players"]
    title = f"# Fight investigation — round {result['round']}"
    if result["query"] == "evidence":
        return title + "\n\n```json\n" + json.dumps(result["record"], indent=2, ensure_ascii=False) + "\n```\n"
    if result["query"] == "fight_index":
        rows = [(seconds(f["round_time_ms"]), f["kind"], player(names, f.get("teammate", f.get("player"))),
                 player(names, f["opponent"]), f["id"]) for f in result["fights"]]
        return "\n".join([title, "", *table(["Start", "Kind", "Player", "Opponent", "ID"], rows), "",
                          f"Page: {result['pagination']}"]) + "\n"
    fight, window = result["fight"], result["window"]
    lines = [title, "", f"Evidence: `{fight['id']}`. Window {seconds(window['round_start_ms'])} to "
             f"{seconds(window['round_end_ms'])}, end exclusive.", "",
             f"Selected side: {result['round_context']['side'] or 'unknown'}; "
             f"alive at anchor: {result['state_at_anchor']['alive']}.", ""]
    equipment = result["state_at_anchor"]["equipment"]
    items = "; ".join(e.get("weapon") or e.get("item_class") or "no item / unknown" for e in equipment["observations"])
    lines += [f"Equipment observation at anchor: {equipment['status']}; {items or 'unknown'}. "
              "Remaining ammo and ability charges are unknown.", ""]
    rows = []
    for event in result["events"]:
        subject = event.get("attacker", event.get("killer", event.get("player")))
        detail = event.get("weapon", event.get("ability", "")) or "unknown"
        if event["kind"] == "damage":
            detail += f"; {amount(event.get('actual_damage'))} actual damage"
        rows.append((seconds(event["round_time_ms"]), event["kind"], player(names, subject),
                     player(names, event.get("victim")), detail, event["id"]))
    lines += table(["Time", "Kind", "Player", "Target", "Detail", "Evidence"], rows)
    lines += ["", "## Fight facts", "", "```json", json.dumps(fight, indent=2), "```", "",
              "## Spatial samples", ""]
    positions = [(seconds(s["round_time_ms"]), player(names, p["pawn"]), p["status"],
                  p["position_cm"], p["view_deg"], p["sample_age_ms"]) for s in result["snapshots"] for p in s["players"]]
    lines += table(["Time", "Player", "State", "XYZ cm", "Yaw/pitch degrees", "Age ms"], positions)
    angle_rows = [(seconds(s["round_time_ms"]), c["label"], c["facing_side"], c["status"],
                   "; ".join(player(names,t["pawn"])+": "+t["side"] for t in c["targets"]))
                  for s in result["snapshots"] for c in s["entrance_context"]["candidates"]]
    if angle_rows:
        lines += ["", "## Entrance-facing estimates", "", "Left/right is from the observer; estimates do not establish visibility or intent.", "",
                  *table(["Time", "Entrance", "Facing side", "Estimate", "Players beyond entrance"], angle_rows)]
    lines += ["", "## Utility observations", "", "```json", json.dumps(result["utility"], indent=2), "```", "",
              "## Coverage", "", f"Truncated: {result['coverage']['truncated']}; "
              f"events {result['coverage']['events_returned']}/{result['coverage']['events_total']}.", "",
              *("- "+item for item in result["coverage"]["limitations"])]
    return "\n".join(lines) + "\n"
