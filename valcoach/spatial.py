"""Bounded replay positions, replicated facing and explicitly scoped map estimates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path

from .angles import entrance_context
from .map_model import MapModel, load_map_model
from .query import Positions, alive, player_labels
from .source import Export, QueryError, provenance
from .utility import player_status_at, smoke_relation, smoke_snapshot, utility_window, validate_smoke_radius

SPATIAL_VERSION = 3
MAX_WINDOW_MS = 10000
MAX_RELATIONS = 500
MAX_ROUTE_EVALUATIONS = 32


@dataclass(frozen=True)
class SpatialSettings:
    step_ms: int = 250
    limit: int = 50
    movement_max_age_ms: int = 250
    allow_candidate_map: bool = False
    smoke_radius_cm: float | None = None

    def validate(self):
        validate_smoke_radius(self.smoke_radius_cm)
        for name in ("step_ms", "limit", "movement_max_age_ms"):
            value = getattr(self,name)
            if isinstance(value,bool) or not isinstance(value,int) or value < (0 if name == "movement_max_age_ms" else 1):
                raise QueryError("invalid_spatial_settings", f"Invalid {name}.")
        if self.limit > 200 or self.movement_max_age_ms > 1000:
            raise QueryError("invalid_spatial_settings", "Use --limit <= 200 and movement freshness <= 1000 ms.")


def _finite(values) -> bool:
    return all(isinstance(n,(int,float)) and not isinstance(n,bool) and math.isfinite(n) for n in values)


def _wrap(degrees: float) -> float:
    return (degrees+180)%360-180


def facing_vector(yaw: float, pitch: float) -> list[float]:
    y,p = math.radians(yaw), math.radians(pitch)
    return [round(math.cos(p)*math.cos(y),7), round(math.cos(p)*math.sin(y),7), round(math.sin(p),7)]


def relative_bearing(origin: dict, target: dict) -> dict:
    delta = [b-a for a,b in zip(origin["position_cm"],target["position_cm"])]
    horizontal, distance = math.hypot(*delta[:2]), math.sqrt(sum(n*n for n in delta))
    result = {"distance_m": round(distance/100,3), "horizontal_offset_deg": None,
              "vertical_offset_deg": None, "angular_offset_deg": None}
    if distance < 1e-7 or origin["yaw_deg"] is None:
        result["bearing_reason"] = "coincident_positions" if distance < 1e-7 else "missing_replicated_rotation"
        return result
    yaw, pitch = math.degrees(math.atan2(delta[1],delta[0])), math.degrees(math.atan2(delta[2],horizontal))
    result["horizontal_offset_deg"] = round(_wrap(yaw-origin["yaw_deg"]),3) if horizontal else None
    result["bearing_method"] = "replicated_rotation_to_pawn_origin; not camera_or_hitbox_visibility"
    if origin["pitch_deg"] is None:
        result["bearing_reason"] = "missing_replicated_pitch"
        return result
    forward = facing_vector(origin["yaw_deg"],origin["pitch_deg"])
    dot = sum(a*b/distance for a,b in zip(forward,delta))
    result.update({"vertical_offset_deg": round(_wrap(pitch-origin["pitch_deg"]),3),
                   "angular_offset_deg": round(math.degrees(math.acos(max(-1.0,min(1.0,dot)))),3)})
    return result


def _sample_status(row: dict | None, living: bool) -> str:
    if row is None:
        return "missing_or_stale"
    if not _finite([row.get("pos_"+axis) for axis in "xyz"]):
        return "nonfinite_position"
    if abs(row["pos_x"]+50000) <= 100 and abs(row["pos_z"]+49900) <= 100:
        return "parked_actor"
    return "observed" if living else "player_dead"


def _observation(model: dict, round_: dict, positions: Positions, pawn: int,
                 moment: int, map_model: MapModel | None, map_status: str) -> dict:
    row = positions.at(pawn,moment)
    living = alive(model,round_,pawn,moment)
    status = _sample_status(row,living)
    if status == "observed" and any(
        e["kind"] == "resurrection" and e["player"] == pawn
        and max(round_["live_ms"],row["time_ms"]+1) <= e["time_ms"] <= moment
        for e in model["timeline"]
    ):
        status = "sample_before_resurrection"
    result = {"pawn": pawn, "status": status, "life_state": "alive" if living else "dead",
              "position_cm": None, "yaw_deg": None, "pitch_deg": None, "facing": None,
              "source": provenance(row,"movement") if row else None,
              "sample_time_ms": row["time_ms"] if row else None,
              "sample_age_ms": moment-row["time_ms"] if row else None, "map_uv": None, "regions": []}
    if status != "observed":
        return result
    result["position_cm"] = [round(row["pos_"+axis],4) for axis in "xyz"]
    for source, key in (("yaw", "yaw_deg"), ("pitch", "pitch_deg")):
        if _finite([row.get(source)]):
            result[key] = row[source] % 360
    if result["yaw_deg"] is not None and result["pitch_deg"] is not None:
        result["facing"] = facing_vector(result["yaw_deg"],result["pitch_deg"])
    if map_model:
        result["map_uv"] = list(map_model.transform.project(result["position_cm"]))
        if map_status != "unvalidated_for_build":
            result["regions"] = map_model.region(result["position_cm"][:2])
    return result


def _unknown(reason: str) -> dict:
    return {"status": "unknown", "reason": reason}


def _relation(origin: dict, target: dict, map_model: MapModel | None,
              map_status: str, evaluate_route: bool) -> dict:
    result = {"from": origin["pawn"], "to": target["pawn"],
              "actual_visibility": None, "actual_access": None, "dynamic_occlusion": "unknown"}
    reason = next((p["status"] for p in (origin,target) if p["status"] != "observed"), None)
    if reason:
        return {**result, "distance_m": None, "static_sightline": _unknown(reason), "walking_access": _unknown(reason)}
    result.update(relative_bearing(origin,target))
    if map_model is None or map_status == "unvalidated_for_build":
        reason = "map_model_unavailable" if map_model is None else "map_model_not_validated_for_build; candidate_opt_in_required"
        return {**result, "static_sightline": _unknown(reason), "walking_access": _unknown(reason)}
    sight, access = map_model.inspect(tuple(origin["position_cm"][:2]),tuple(target["position_cm"][:2]),route=evaluate_route)
    return {**result, "static_sightline": sight, "walking_access": access, "model_validation": map_status,
            "height_assessment": map_model.data["height_policy"]}


def _window(model: dict, number: int, start: int, end: int) -> dict:
    round_ = next((r for r in model["rounds"] if r["number"] == number),None)
    if round_ is None:
        raise QueryError("unknown_round", f"Round {number} does not exist in this export.")
    if any(isinstance(t,bool) or not isinstance(t,int) for t in (start,end)) or not start < end:
        raise QueryError("invalid_time_range", "Use integer start/end milliseconds with start < end.")
    if end-start > MAX_WINDOW_MS:
        raise QueryError("window_too_large", "Spatial windows are limited to 10000 ms.")
    if start < round_["buy_ms"]-round_["live_ms"] or end > round_["end_ms"]-round_["live_ms"]:
        raise QueryError("invalid_time_range", "The spatial window must stay inside this round, including its buy/post phases.")
    return round_


def _snapshots(model: dict, round_: dict, selected: dict, targets: list[dict], positions: Positions,
               start: int, end: int, settings: SpatialSettings, map_model: MapModel | None, map_status: str,
               sample_times_ms: list[int] | None = None):
    snapshots, relations, routes = [], 0, 0
    for offset in (sample_times_ms if sample_times_ms is not None else range(start,end,settings.step_ms)):
        if len(snapshots) == settings.limit:
            break
        moment = round_["live_ms"]+offset
        observations = [_observation(model,round_,positions,p["pawn"],moment,map_model,map_status) for p in [selected,*targets]]
        for observation in observations:
            observation["utility_status"] = player_status_at(model, observation["pawn"], moment)
        smoke_state = smoke_snapshot(model, moment)
        pairs = []
        for target in observations[1:]:
            if relations >= MAX_RELATIONS:
                break
            evaluate = routes < MAX_ROUTE_EVALUATIONS
            relation = _relation(observations[0],target,map_model,map_status,evaluate)
            relation["smoke_context"] = smoke_relation(observations[0], target, smoke_state, settings.smoke_radius_cm)
            pairs.append(relation)
            relations += 1
            if evaluate and map_model and map_status != "unvalidated_for_build" and all(p["status"] == "observed" for p in (observations[0],target)):
                routes += 1
        angles = entrance_context(map_model, map_status, observations[0], observations[1:])
        snapshots.append({"time_ms": moment, "round_time_ms": offset, "players": observations, "relations": pairs,
                          "entrance_context": angles,
                          "smoke_observations": smoke_state,
                          "relations_omitted": len(targets)-len(pairs)})
    return snapshots, relations, routes


def query_spatial(model: dict, export: Export, number: int, selected: dict, start_ms: int, end_ms: int,
                   settings: SpatialSettings | None = None, targets: list[dict] | None = None,
                   map_path: Path | None = None, sample_times_ms: list[int] | None = None) -> dict:
    config = settings or SpatialSettings()
    config.validate()
    round_ = _window(model,number,start_ms,end_ms)
    if sample_times_ms is not None and (not sample_times_ms or len(sample_times_ms) > config.limit
            or any(type(t) is not int or not start_ms <= t < end_ms for t in sample_times_ms)
            or sample_times_ms != sorted(set(sample_times_ms))):
        raise QueryError("invalid_sample_times", "Explicit samples must be distinct, ordered, inside the window and within the snapshot limit.")
    targets = [p for p in (targets if targets is not None else model["players"]) if p["pawn"] != selected["pawn"]]
    if len(targets) > 16 or len({p["pawn"] for p in targets}) != len(targets):
        raise QueryError("invalid_spatial_targets", "Use at most 16 distinct target players.")
    map_model = load_map_model(model.get("map_asset"),map_path)
    map_status = map_model.applicability(model["replay_build"],config.allow_candidate_map) if map_model else "unavailable"
    start, end = round_["live_ms"]+start_ms, round_["live_ms"]+end_ms
    rows = export.movement(max(round_["buy_ms"],start-config.movement_max_age_ms),end,[p["pawn"] for p in [selected,*targets]])
    positions = Positions(rows,config.movement_max_age_ms)
    snapshots, relation_count, route_count = _snapshots(model,round_,selected,targets,positions,start_ms,end_ms,config,map_model,map_status,sample_times_ms)
    requested = len(sample_times_ms) if sample_times_ms is not None else (end_ms-start_ms+config.step_ms-1)//config.step_ms
    context = [{**e,"round_time_ms":e["time_ms"]-round_["live_ms"]} for e in model["timeline"] if start <= e["time_ms"] < end]
    utility = utility_window(model, round_, start, end, {p["pawn"] for p in [selected, *targets]})
    route_omissions = sum(r["walking_access"].get("reason") == "route_evaluation_budget"
                          for s in snapshots for r in s["relations"])
    angle_omissions = sum(s["entrance_context"]["omitted"] for s in snapshots)
    utility_omissions = sum(s["smoke_observations"]["omitted"]
                            + sum(p["utility_status"]["omitted"] for p in s["players"]) for s in snapshots)
    return {"schema_version": 1, "spatial_version": SPATIAL_VERSION, "query": "spatial",
            "match_id": model["match_id"], "revision": model["revision"], "replay_build": model["replay_build"],
            "map_asset": model.get("map_asset"), "round": round_, "selected_player": selected,
            "players": player_labels(model,selected), "settings": asdict(config),
            "window": {"start_ms":start_ms,"end_ms":end_ms,"clock":"round_live_relative_ms; start inclusive/end exclusive",
                       "explicit_sample_times_ms":sample_times_ms},
            "map_model": map_model.describe(model["replay_build"],config.allow_candidate_map) if map_model else None,
            "snapshots": snapshots, "context_events": context[:100], "utility": utility,
            "coverage": {"snapshots_requested":requested,"snapshots_returned":len(snapshots),
                "next_start_ms": start_ms+len(snapshots)*config.step_ms if len(snapshots)<requested else None,
                "relation_candidates":requested*len(targets),"relations_returned":relation_count,
                "relation_limit":MAX_RELATIONS,"route_evaluations":route_count,"route_evaluation_limit":MAX_ROUTE_EVALUATIONS,
                "routes_omitted_by_budget":route_omissions,
                "utility_snapshot_details_omitted":utility_omissions,
                "entrance_candidates_omitted": angle_omissions,
                "context_events_total":len(context),"context_events_returned":min(100,len(context)),
                "truncated":len(snapshots)<requested or relation_count<requested*len(targets) or len(context)>100 or route_omissions>0 or utility["truncated"] or utility_omissions>0 or angle_omissions>0,
                "limitations": ["Last observed samples only; no future samples or interpolation.",
                    "Replicated facing and pawn-origin bearings are not confirmed attention, shots, camera or hitbox visibility.",
                    "Map-derived results are planar approximations; model validation and edge uncertainty remain explicit.",
                    "Smoke actor lifetimes and player visual-effect intervals are partial observations, not confirmed opacity or impairment.",
                    "Smoke radius is an explicit optional hypothesis; doors, temporary walls, floor/eye height and player knowledge remain unknown.",
                    "Walking routes use annotated 2D space and configured clearance, not movement physics, safety or exact arrival time.",
                    "A route missing from a partial model does not establish in-game inaccessibility.",
                    "Candidate map use is opt-in and never establishes an independently verified coaching mistake."]}}
