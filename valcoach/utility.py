"""Bounded utility windows and candidate smoke intersections for shared queries."""

import math

from .effects import UTILITY_VERSION
from .geometry import point_segment_distance
from .source import QueryError

MAX_UTILITY_RECORDS = 200
MAX_SNAPSHOT_SMOKES = 32
MAX_PLAYER_STATUSES = 16


def interval_end(record):
    ends = [record[k] for k in ("end_ms", "scope_end_ms") if record.get(k) is not None]
    return min(ends) if ends else None


def overlaps(record, start, end):
    stop = interval_end(record)
    return record["start_ms"] < end and (stop is None or stop > start)


def _window_record(record, live_ms, sample_at):
    result = {k: v for k, v in record.items() if k != "positions"}
    result["round_start_ms"] = record["start_ms"] - live_ms
    result["round_end_ms"] = record["end_ms"] - live_ms if record.get("end_ms") is not None else None
    result["pair_status"] = "matched_start_stop" if record.get("end_ms") is not None else "end_unobserved"
    if "positions" in record:
        result["position_observation_count"] = len(record["positions"])
        samples = [p for p in record["positions"] if p["time_ms"] <= max(sample_at, record["start_ms"])]
        result["position_observation"] = samples[-1] if samples else None
    return result


def utility_window(model, round_, start, end, pawns=None):
    data = model.get("utility")
    if data is None:
        return {"version": UTILITY_VERSION, "status": "not_loaded", "records": [],
                "total": None, "returned": 0, "truncated": False}
    records = []
    for kind, items in (("smoke", data["smokes"]), ("player_status", data["statuses"])):
        for item in items:
            if kind == "player_status" and pawns is not None and item["player"] not in pawns:
                continue
            if overlaps(item, start, end):
                records.append({"kind": kind, **_window_record(item, round_["live_ms"], start)})
    for item in data["blind_updates"]:
        if start <= item["time_ms"] < end and (pawns is None or item["player"] in pawns):
            records.append({"kind": "blind_update", **item,
                            "round_time_ms": item["time_ms"] - round_["live_ms"]})
    records.sort(key=lambda r: (r.get("start_ms", r.get("time_ms")), r["id"]))
    return {"version": data["version"], "status": "partial_supported_observations",
            "records": records[:MAX_UTILITY_RECORDS], "total": len(records),
            "returned": min(len(records), MAX_UTILITY_RECORDS), "limit": MAX_UTILITY_RECORDS,
            "truncated": len(records) > MAX_UTILITY_RECORDS, "source_coverage": data["coverage"],
            "limitations": ["Smoke actor lifetime does not establish opaque activation/fade timing.",
                "Smoke radius/height are unknown unless a candidate radius is explicitly configured.",
                "Status intervals are visual-effect observations, not authoritative gameplay flags or impairment severity.",
                "Missing stops remain unknown and are bounded by life/round/actor transitions.",
                "Blind array updates remain undecoded; an update is not a flash hit or duration.",
                "Only explicitly mapped effect/zone classes are supported; no observation does not establish no utility."]}


def player_status_at(model, pawn, moment):
    data = model.get("utility")
    if data is None:
        return {"status": "not_loaded", "effects": [], "omitted": 0}
    items = [{"id": r["id"], "category": r["category"], "effect_path": r["effect_path"],
              "interval_evidence": "paired_visual_effect" if r["end_ms"] is not None else "possibly_active_end_unobserved",
              "start_ms": r["start_ms"], "end_ms": r["end_ms"], "severity": None}
             for r in data["statuses"] if r["player"] == pawn and overlaps(r, moment, moment + 1)]
    return {"status": "supported_effect_observations" if items else "no_supported_status_observation",
            "effects": items[:MAX_PLAYER_STATUSES], "omitted": max(0, len(items) - MAX_PLAYER_STATUSES),
            "actual_impairment": None}


def smoke_snapshot(model, moment):
    data = model.get("utility")
    if data is None:
        return {"status": "not_loaded", "smokes": [], "omitted": 0}
    smokes = []
    for record in data["smokes"]:
        if not overlaps(record, moment, moment + 1):
            continue
        samples = [p for p in record["positions"] if p["time_ms"] <= moment]
        sample = samples[-1] if samples else None
        smokes.append({"id": record["id"], "actor": record["actor"], "label": record["label"],
                       "owner": record["owner"] if record.get("owner_time_ms") is not None and record["owner_time_ms"] <= moment else None,
                       "start_ms": record["start_ms"], "end_ms": record["end_ms"],
                       "lifecycle": "paired_open_close" if record["end_ms"] is not None else "end_unobserved",
                       "position_cm": sample["position_cm"] if sample else None,
                       "sample_time_ms": sample["time_ms"] if sample else None,
                       "sample_age_ms": moment - sample["time_ms"] if sample else None,
                       "position_source": sample["source"] if sample else None,
                       "position_method": sample["method"] if sample else None,
                       "unresolved_position_updates": record["position_updates_unresolved"],
                       "radius_cm": None, "opaque_active": None})
    return {"status": "partial_supported_observations", "smokes": smokes[:MAX_SNAPSHOT_SMOKES],
            "omitted": max(0, len(smokes) - MAX_SNAPSHOT_SMOKES)}


def validate_smoke_radius(radius):
    if radius is not None and (isinstance(radius, bool) or not isinstance(radius, (int, float))
                               or not math.isfinite(radius) or not 1 <= radius <= 2000):
        raise QueryError("invalid_spatial_settings", "Candidate smoke radius must be 1-2000 cm.")


def smoke_relation(origin, target, snapshot, radius=None):
    """Optional planar hypothesis; never promote it to confirmed visibility."""
    result = {"status": "unknown", "actual_occlusion": None, "candidate_radius_cm": radius,
              "method": "candidate_planar_disks_at_last_observed_zone_positions; opacity_and_height_unknown",
              "smokes": [], "omitted": snapshot["omitted"]}
    if origin["position_cm"] is None or target["position_cm"] is None:
        result["reason"] = "unusable_player_position"
        return result
    for smoke in snapshot["smokes"]:
        point = smoke["position_cm"]
        separation = point_segment_distance(point[:2], origin["position_cm"][:2], target["position_cm"][:2]) if point else None
        intersects = separation <= radius if separation is not None and radius is not None else None
        result["smokes"].append({"id": smoke["id"], "axis_distance_m": round(separation / 100, 3) if separation is not None else None,
                                 "candidate_disk_intersects": intersects})
    if radius is None:
        result["reason"] = "smoke_volume_extent_not_established"
    elif snapshot["status"] == "not_loaded":
        result["reason"] = "utility_not_loaded"
    elif any(s["candidate_disk_intersects"] is True for s in result["smokes"]):
        result.update(status="candidate_smoke_intersection", reason="assumed_radius_and_actor_lifetime_only")
    elif snapshot["omitted"] or any(s["candidate_disk_intersects"] is None for s in result["smokes"]):
        result["reason"] = "incomplete_smoke_positions_or_budget"
    else:
        result.update(status="no_candidate_smoke_intersection", reason="supported_classes_only; not_proof_of_clear_vision")
    return result


def fight_utility_refs(model, fight, selected):
    start, end = fight["time_ms"], fight["end_ms"] + 1
    pawns = {selected, fight.get("teammate"), fight.get("opponent")}
    data = model.get("utility", {})
    statuses = [r["id"] for r in data.get("statuses", []) if r["player"] in pawns and overlaps(r, start, end)]
    smokes = [r["id"] for r in data.get("smokes", []) if overlaps(r, start, end)]
    return {"status_ids": statuses[:32], "smoke_ids": smokes[:32],
            "omitted_statuses": max(0, len(statuses)-32), "omitted_smokes": max(0, len(smokes)-32),
            "interpretation": "time_overlap_only; not_proof_of_visibility_or_trade_opportunity"}
