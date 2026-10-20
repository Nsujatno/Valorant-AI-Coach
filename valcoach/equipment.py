"""Observed equipment identity; never infer remaining ammo or ability charges."""


def life_scope(model: dict, round_: dict, pawn: int, moment: int) -> tuple[bool, int]:
    """Alive flag and earliest admissible observation in this round/life."""
    life_start = round_["buy_ms"]
    living = True
    for event in model["timeline"]:
        if not round_["live_ms"] <= event["time_ms"] <= moment:
            continue
        if event["kind"] == "death" and event["victim"] == pawn:
            living, life_start = False, event["time_ms"]+1
        elif event["kind"] == "resurrection" and event["player"] == pawn:
            living, life_start = True, event["time_ms"]
    return living, life_start


def observation_in_scope(record: dict, round_: dict, minimum: int, moment: int) -> bool:
    """A boundary snapshot keeps its real clock and explicit round membership."""
    if record["time_ms"] > moment:
        return False
    if "scope_round" not in record:
        return minimum <= record["time_ms"]
    if record["scope_round"] != round_["number"]:
        return False
    return minimum <= record["time_ms"] or minimum == round_["buy_ms"]


def latest_equipment(model: dict, round_: dict, pawn: int, moment: int) -> dict:
    result = {"status": "unknown", "observations": [], "omitted": 0,
              "ammunition_remaining": None, "ability_charges_remaining": None,
              "method": "last recorded equip in current round/life; separate resources query for counts"}
    living, life_start = life_scope(model, round_, pawn, moment)
    if not living:
        return {**result, "reason": "player_dead"}
    candidates = model.get("equipment", []) + model.get("checkpoint_state", {}).get("equipment", [])
    records = [e for e in candidates if e["player"] == pawn and observation_in_scope(e,round_,life_start,moment)]
    if not records:
        return {**result, "reason": "no_equip_observation_in_current_round_and_life"}
    latest = max(e["time_ms"] for e in records)
    observations = sorted((e for e in records if e["time_ms"] == latest), key=lambda e: e["id"])
    identities = {(e["item"], e.get("item_class")) for e in observations}
    status = ("conflicting_same_time_observations" if len(identities) > 1 else
              "no_item_observed" if observations[0]["item"] in (None, 0) else "equipped_observation")
    return {**result, "status": status, "observed_time_ms": latest,
            "round_observed_time_ms": latest-round_["live_ms"], "observation_age_ms": moment-latest,
            "observations": observations[:8], "omitted": max(0, len(observations)-8)}
