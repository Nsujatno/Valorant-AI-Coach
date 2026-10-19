"""Versioned, validated map descriptions; images and replay decoding stay separate."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.resources import files
import json
import math
from pathlib import Path

from .geometry import Polygon, WalkingGrid, contains, distance, edges, segment_distance, sightline
from .source import QueryError


def _number(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Coordinates and model settings must be finite numbers.")
    return float(value)


def _polygon(vertices) -> Polygon:
    if not isinstance(vertices, list) or not 3 <= len(vertices) <= 512:
        raise ValueError("A polygon needs 3-512 vertices.")
    result = tuple(tuple(_number(n) for n in p) for p in vertices)
    if any(len(p) != 2 for p in result) or any(distance(a,b) < 1e-7 for a,b in edges(result)):
        raise ValueError("Polygon vertices must be distinct XY pairs.")
    area = abs(sum(a[0]*b[1]-b[0]*a[1] for a,b in edges(result)))/2
    if area < 1e-6:
        raise ValueError("Polygon area is zero.")
    segments = list(edges(result))
    for i, (a,b) in enumerate(segments):
        for j, (c,d) in enumerate(segments):
            if j > i+1 and not (i == 0 and j == len(segments)-1) and segment_distance(a,b,c,d) < 1e-7:
                raise ValueError("Self-intersecting polygons are unsupported.")
    return result


@dataclass(frozen=True)
class MapTransform:
    x_multiplier: float
    y_multiplier: float
    x_scalar: float
    y_scalar: float

    @classmethod
    def from_dict(cls, data):
        result = cls(*(_number(data[key]) for key in ("xMultiplier", "yMultiplier", "xScalarToAdd", "yScalarToAdd")))
        if result.x_multiplier == 0 or result.y_multiplier == 0:
            raise ValueError("Map transform multipliers cannot be zero.")
        return result

    def project(self, point) -> tuple[float, float]:
        x,y = point[:2]
        return y*self.x_multiplier+self.x_scalar, x*self.y_multiplier+self.y_scalar

    def unproject(self, uv) -> tuple[float, float]:
        u,v = uv
        return (v-self.y_scalar)/self.y_multiplier, (u-self.x_scalar)/self.x_multiplier


@dataclass
class MapModel:
    data: dict
    revision: str
    transform: MapTransform
    coverage: Polygon
    walking: WalkingGrid
    sight_blockers: list[dict]
    regions: list[dict]
    entrances: list[dict]

    def applicability(self, build: str, allow_candidate: bool) -> str:
        if build in self.data["validated_builds"]:
            return "validated"
        if allow_candidate and build in self.data["candidate_builds"]:
            return "candidate_unverified"
        return "unvalidated_for_build"

    def region(self, point) -> list[dict]:
        return [{"id": item["id"], "label": item["label"]} for item in self.regions if contains(point, item["polygon"])]

    def describe(self, build: str, allow_candidate: bool) -> dict:
        return {"id": self.data["id"], "name": self.data["name"], "revision": self.revision,
                "map_asset": self.data["map_asset"], "validation": self.applicability(build, allow_candidate),
                "validated_builds": self.data["validated_builds"], "candidate_builds": self.data["candidate_builds"],
                "provenance": self.data["provenance"], "projection": self.data["projection"],
                "height_policy": self.data["height_policy"], "coverage_polygon_cm": self.coverage,
                "walking_polygon_cm": self.walking.surface,
                "movement_blockers": self.walking.blockers, "sight_blockers": self.sight_blockers,
                "regions": self.regions, "entrances": self.entrances,
                "landmarks": self.data.get("landmarks", []),
                "edge_uncertainty_cm": self.data["edge_uncertainty_cm"]}

    def inspect(self, a, b, *, route: bool = True) -> tuple[dict, dict]:
        sight = sightline(a,b,self.coverage,self.sight_blockers,self.data["edge_uncertainty_cm"])
        access = (self.walking.route(a,b) if route else {"status": "unknown", "reason": "route_evaluation_budget"})
        return sight, access


def _blockers(items: list[dict]) -> list[dict]:
    if len(items) > 32 or len({item["id"] for item in items}) != len(items):
        raise ValueError("Use at most 32 blockers with unique IDs per layer.")
    return [{"id": item["id"], "polygon": _polygon(item["polygon_cm"]),
             "height": item.get("height", "unknown"), "source": item["source"]} for item in items]


def _entrances(items: list[dict]) -> list[dict]:
    if not isinstance(items, list) or len(items) > 32 or len({e["id"] for e in items}) != len(items):
        raise ValueError("Use at most 32 entrances with unique IDs.")
    result = []
    for entry in items:
        segment = tuple(tuple(_number(v) for v in p) for p in entry["segment_cm"])
        if len(segment) != 2 or any(len(p) != 2 for p in segment) or distance(*segment) < 1:
            raise ValueError("An entrance needs two distinct XY endpoints.")
        maximum = _number(entry["max_range_cm"])
        if not 1 <= maximum <= 10000:
            raise ValueError("Entrance range must be 1-10000 cm.")
        result.append({"id": entry["id"], "label": entry["label"], "segment_cm": segment,
                       "observer_polygon_cm": _polygon(entry["observer_polygon_cm"]),
                       "approach_polygon_cm": _polygon(entry["approach_polygon_cm"]),
                       "max_range_cm": maximum, "source": entry["source"]})
    return result


def load_map_model(map_asset: str | None, path: Path | None = None) -> MapModel | None:
    """A matching built-in candidate is optional; explicit mismatches are errors."""
    builtins = {"/Game/Maps/Juliett/Juliett": "sunset-full-v1.json"}
    if path is None and map_asset not in builtins:
        return None
    try:
        raw = path.read_bytes() if path is not None else files("valcoach").joinpath("mapdata", builtins[map_asset]).read_bytes()
        if len(raw) > 256000:
            raise ValueError("Map models are limited to 256 KB.")
        data = json.loads(raw)
        if data["schema_version"] not in (1, 2) or data["coordinate_space"] != "world_cm_xy":
            raise ValueError("Unsupported map schema or coordinate space.")
        if data["map_asset"] != map_asset:
            raise QueryError("map_model_mismatch", "Map model asset does not match this replay.")
        for key in ("validated_builds", "candidate_builds"):
            if not isinstance(data[key], list) or any(not isinstance(b,str) for b in data[key]):
                raise ValueError("Model builds must be explicit lists of strings.")
        transform = MapTransform.from_dict(data["projection"])
        coverage, surface = _polygon(data["coverage_polygon_cm"]), _polygon(data["walking_polygon_cm"])
        sight, movement = _blockers(data["sight_blockers"]), _blockers(data["movement_blockers"])
        resolution, radius, epsilon = (_number(data[key]) for key in ("grid_resolution_cm", "actor_radius_cm", "edge_uncertainty_cm"))
        if resolution < 10 or radius < 1 or epsilon < 0 or max(resolution,radius,epsilon) > 500:
            raise ValueError("Invalid grid, clearance or edge-uncertainty settings.")
        regions = [{"id": r["id"], "label": r["label"], "polygon": _polygon(r["polygon_cm"])} for r in data["regions"]]
        if len(regions) > 32:
            raise ValueError("Use at most 32 semantic regions.")
        entrances = _entrances(data.get("entrances", []))
        landmarks = data.get("landmarks", [])
        if not isinstance(landmarks, list) or len(landmarks) > 64:
            raise ValueError("Use at most 64 landmark points.")
        for landmark in landmarks:
            if len(landmark["position_cm"]) != 2 or not isinstance(landmark["label"], str):
                raise ValueError("Landmarks need a label and XY position.")
            for n in landmark["position_cm"]:
                _number(n)
        maximum = data.get("max_grid_cells", 20000)
        if type(maximum) is not int or not 1 <= maximum <= 100000:
            raise ValueError("Walking grids are limited to 100000 cells.")
        code = b"".join(Path(__file__).with_name(name).read_bytes()
                        for name in ("map_model.py", "geometry.py", "angles.py"))
        revision = hashlib.sha256(raw+code).hexdigest()
        grid = WalkingGrid(surface,movement,resolution,radius,max_cells=maximum)
        return MapModel(data,revision,transform,coverage,grid,sight,regions,entrances)
    except QueryError:
        raise
    except (OSError, ValueError, KeyError, TypeError, IndexError) as exc:
        raise QueryError("invalid_map_model", f"Cannot load map model: {exc}") from exc
