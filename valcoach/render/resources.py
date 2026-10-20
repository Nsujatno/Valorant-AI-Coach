"""Readable resource observations; interpretation stays outside rendering."""

from .common import seconds, table


def state_lines(state):
    weapon = state.get("weapon")
    lines = [f"Life state: {state['life_state']}. Effective ability/weapon readiness: unknown."]
    if weapon:
        lines.append(f"Equipped {weapon.get('weapon') or 'item'}: magazine {weapon['magazine']['value']}, "
                     f"reserve {weapon['reserve']['value']}; last weapon state {weapon['state']['value']}.")
    else:
        lines.append("Equipped weapon resources: unknown.")
    lines.append(f"Ultimate points: {state['ultimate_points']['value']}; active flag: {state['ultimate_active']['value']}.")
    for ability in state["abilities"]:
        counts = ", ".join(f"{name}: {observation['value']}" for name, observation in ability["resources"].items())
        lines.append(f"{ability['ability'] or ability['item_class']}: {counts}.")
    return lines


def observation_section(data):
    rows = [(seconds(r["round_time_ms"]), r.get("ability") or r["item"], r["metric"],
             r["value"], r["status"], r["id"]) for r in data["records"]]
    return ["## Resource observations", "", *table(["Time", "Item/ability", "Resource/state", "Value", "Status", "Evidence"], rows),
            "", f"Returned {data['returned']}/{data['total']}; next offset: {data['next_offset']}."]


def render_resources(result):
    lines = [f"# Resource inspection — round {result['round']}", "",
             f"Snapshot at {seconds(result['state']['round_time_ms'])} since live start.", "",
             *state_lines(result["state"]), "", *observation_section(result["observations"]), "",
             *("- "+item for item in result["limitations"])]
    return "\n".join(lines)+"\n"
