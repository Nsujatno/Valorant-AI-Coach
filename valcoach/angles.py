"""Facing-to-entrance estimates, distinct from visibility and player intention."""

import math

from .geometry import contains, distance

MAX_ANGLE_CANDIDATES = 4


def _cross(a, b):
    return a[0]*b[1]-a[1]*b[0]


def _intersection(origin, direction, a, b):
    edge = (b[0]-a[0], b[1]-a[1])
    delta = (a[0]-origin[0], a[1]-origin[1])
    denominator = _cross(direction, edge)
    if abs(denominator) < 1e-8:
        return None
    return _cross(delta, edge)/denominator, _cross(delta, direction)/denominator


def _side(fraction, margin):
    if abs(fraction) <= margin or abs(fraction-1) <= margin:
        return "jamb_boundary_uncertain"
    if fraction < 0:
        return "left_of_opening"
    if fraction > 1:
        return "right_of_opening"
    if abs(fraction-1/3) <= margin or abs(fraction-2/3) <= margin:
        return "side_boundary_uncertain"
    return "left" if fraction < 1/3 else "right" if fraction > 2/3 else "center"


def _ordered_segment(origin, segment):
    midpoint = tuple((a+b)/2 for a, b in zip(*segment))
    toward = (midpoint[0]-origin[0], midpoint[1]-origin[1])
    # Unreal XY convention: positive yaw turns right. Left/right is relative
    # to the observer, independent of minimap rotation or endpoint order.
    return sorted(segment, key=lambda p: _cross(toward, (p[0]-origin[0], p[1]-origin[1])))


def _target_sides(origin, players, entrance, a, b, margin):
    result = []
    for player in players:
        if player["status"] != "observed" or not contains(tuple(player["position_cm"][:2]), entrance["approach_polygon_cm"]):
            continue
        target = player["position_cm"][:2]
        direction = (target[0]-origin[0], target[1]-origin[1])
        hit = _intersection(origin, direction, a, b)
        # Only actors on/beyond the entrance, not allies on the observer side.
        if hit is None or not 0 < hit[0] <= 1+1e-8:
            continue
        result.append({"pawn": player["pawn"], "side": _side(hit[1], margin),
                       "fraction_from_left": round(hit[1], 4),
                       "within_opening_projection": 0 <= hit[1] <= 1,
                       "actual_visibility": None})
    return result


def entrance_context(model, map_status, observer, players):
    base = {"status": "unknown", "candidates": [], "omitted": 0,
            "side_convention": "left/right from observer toward entrance; positive Unreal yaw is right",
            "actual_visibility": None, "player_intent": None}
    if model is None:
        return {**base, "reason": "map_model_unavailable"}
    if map_status == "unvalidated_for_build":
        return {**base, "reason": "map_model_not_validated_for_build; candidate_opt_in_required"}
    if observer["status"] != "observed" or observer["yaw_deg"] is None:
        return {**base, "reason": "observer_position_or_facing_unavailable"}
    pitch = observer["pitch_deg"]
    if pitch is not None and abs(math.cos(math.radians(pitch))) < 0.1:
        return {**base, "reason": "near_vertical_facing"}
    origin = tuple(observer["position_cm"][:2])
    yaw = math.radians(observer["yaw_deg"])
    direction = (math.cos(yaw), math.sin(yaw))
    candidates = []
    for entrance in model.entrances:
        if not contains(origin, entrance["observer_polygon_cm"]):
            continue
        a, b = _ordered_segment(origin, entrance["segment_cm"])
        intersection = _intersection(origin, direction, a, b)
        if intersection is None:
            continue
        along, fraction = intersection
        if not 0 < along <= entrance["max_range_cm"] or not -1e-8 <= fraction <= 1+1e-8:
            continue
        fraction = max(0.0, min(1.0, fraction))
        point = (origin[0]+along*direction[0], origin[1]+along*direction[1])
        sight, _ = model.inspect(origin, point, route=False)
        width = distance(a, b)
        margin = model.data["edge_uncertainty_cm"]/width
        candidates.append({"entrance_id": entrance["id"], "label": entrance["label"],
            "status": "blocked_before_entrance" if sight["status"] == "blocked_in_annotated_2d_model" else "facing_entrance_estimate",
            "model_validation": map_status, "facing_side": _side(fraction, margin),
            "fraction_from_left": round(fraction, 4), "intersection_cm": [round(v, 4) for v in point],
            "distance_m": round(along/100, 3), "path_to_entrance": sight,
            "near_jamb": min(fraction, 1-fraction)*width <= model.data["edge_uncertainty_cm"],
            "targets": _target_sides(origin, players, entrance, a, b, margin),
            "method": "replicated_horizontal_facing_ray_to_annotated_cross_section; thirds with edge tolerance"})
    candidates.sort(key=lambda c: (c["distance_m"], c["entrance_id"]))
    return {**base, "status": "entrance_estimates" if candidates else "no_matching_annotated_entrance",
            "candidates": candidates[:MAX_ANGLE_CANDIDATES], "omitted": max(0, len(candidates)-MAX_ANGLE_CANDIDATES),
            "annotation_count": len(model.entrances)}
