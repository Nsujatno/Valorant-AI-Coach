"""Readable spatial evidence and a self-contained diagnostic SVG."""

from __future__ import annotations

import base64
import hashlib
from html import escape
import json
import math
from pathlib import Path

from ..map_model import MapTransform
from ..source import QueryError
from .common import amount, player, seconds, table
from .utility import utility_section


def _header(result: dict) -> list[str]:
    model, window = result["map_model"], result["window"]
    lines = [f"# Spatial inspection — round {result['round']['number']}", "",
             f"Window: {seconds(window['start_ms'])} to {seconds(window['end_ms'])}, end exclusive. "
             "All times below are since live start.", "",
             f"Match `{result['match_id']}` · replay revision `{result['revision'][:16]}`.", ""]
    if model:
        lines += [f"Map model: **{model['validation']}** · `{model['id']}` · geometry revision `{model['revision'][:16]}`.", ""]
    else:
        lines += ["No map model is available; position and facing evidence remain usable.", ""]
    return lines + ["Static results describe an annotated 2D model. Actual visibility, access, dynamic blockers and player knowledge remain unknown."]


def _positions(result: dict) -> list[str]:
    rows = []
    for snapshot in result["snapshots"]:
        for observation in snapshot["players"]:
            position = observation["position_cm"]
            source = observation["source"]
            rows.append((seconds(snapshot["round_time_ms"]),player(result["players"],observation["pawn"]),observation["status"],
                         ", ".join(amount(n) for n in position) if position else "unknown",
                         f"{amount(observation['yaw_deg'])}/{amount(observation['pitch_deg'])}",
                         observation["sample_age_ms"] if source else "unknown", f"movement:{source['row']}" if source else "none"))
    return ["## Position and facing samples", "", *table(["Time","Player","State","XYZ (cm)","Yaw/pitch (deg)","Age (ms)","Source row"],rows)]


def _relations(result: dict) -> list[str]:
    rows = []
    for snapshot in result["snapshots"]:
        for relation in snapshot["relations"]:
            sight, access = relation["static_sightline"], relation["walking_access"]
            sight_text = sight["status"] + (": " + ", ".join(sight["blocker_ids"]) if sight.get("blocker_ids") else "")
            route_text = access["status"]
            route_text += f" ({access['length_m']:g} m)" if "length_m" in access else ": " + access.get("reason","")
            rows.append((seconds(snapshot["round_time_ms"]),player(result["players"],relation["to"]),
                         amount(relation["distance_m"]),amount(relation.get("horizontal_offset_deg")),sight_text,route_text))
    return ["## Relations from the selected player", "",
            "Signed heading offsets compare replicated rotation with the target's pawn position. They do not prove an aim target.", "",
            *table(["Time","Target","Distance (m)","Horizontal offset (deg)","Static model sightline","Modeled walking access"],rows)]


def _angles(result: dict) -> list[str]:
    rows = []
    for snapshot in result["snapshots"]:
        context = snapshot["entrance_context"]
        for candidate in context["candidates"]:
            targets = "; ".join(player(result["players"],t["pawn"])+": "+t["side"] for t in candidate["targets"])
            rows.append((seconds(snapshot["round_time_ms"]), candidate["label"], candidate["facing_side"],
                         candidate["status"], candidate["path_to_entrance"]["status"], targets or "none sampled"))
    return ["## Entrance-facing estimates", "", "Left/right is from your position looking toward the entrance. "
            "These are horizontal facing estimates; height, visibility, crosshair pixels and intent remain unknown.", "",
            *table(["Time", "Entrance", "Facing side", "Estimate", "Path in model", "Players beyond entrance"], rows)]


def render_spatial(result: dict) -> str:
    coverage = result["coverage"]
    sections = [_header(result),_positions(result),_angles(result),_relations(result),utility_section(result),[
        "## Coverage and limits", "",
        f"Snapshots: {coverage['snapshots_returned']}/{coverage['snapshots_requested']}; "
        f"relations: {coverage['relations_returned']}/{coverage['relation_candidates']}; "
        f"route evaluations: {coverage['route_evaluations']}/{coverage['route_evaluation_limit']} allowed.", "",
        *(f"- {item}" for item in coverage["limitations"])]]
    if coverage["next_start_ms"] is not None:
        sections[-1].append(f"- Continue the sampled window from round +{coverage['next_start_ms']} ms.")
    return "\n\n".join("\n".join(section) for section in sections) + "\n"


def _image_data(path: Path | None, model: dict) -> str | None:
    if path is None:
        return None
    if path.stat().st_size > 2*1024*1024:
        raise QueryError("invalid_map_image", "Map image must be a PNG smaller than 2 MiB.")
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or hashlib.sha256(data).hexdigest() != model["provenance"].get("image_sha256"):
        raise QueryError("invalid_map_image", "Map PNG must match the map model's recorded image checksum.")
    return "data:image/png;base64," + base64.b64encode(data).decode("ascii")


def _diagram_geometry(model: dict, project) -> list[str]:
    def points(polygon):
        return " ".join(f"{x:.2f},{y:.2f}" for x,y in map(project,polygon))
    shapes = [f'<polygon points="{points(model["coverage_polygon_cm"])}" fill="none" stroke="#dcb869" stroke-width="0.8" stroke-dasharray="3 2"/>',
              f'<polygon points="{points(model["walking_polygon_cm"])}" fill="#27b3ac" fill-opacity="0.08" stroke="#38d5c9" stroke-width="0.7"/>']
    for blocker in model["sight_blockers"]:
        shapes.append(f'<polygon points="{points(blocker["polygon"])}" fill="#ed8c40" fill-opacity="0.4" stroke="#ffbc72" stroke-width="0.9"/>')
    return shapes


def _diagram_relations(snapshot: dict, project) -> list[str]:
    observations = {p["pawn"]:p for p in snapshot["players"]}
    shapes = []
    for relation in snapshot["relations"]:
        a,b = observations[relation["from"]],observations[relation["to"]]
        if not a["position_cm"] or not b["position_cm"]:
            continue
        ax,ay = project(a["position_cm"])
        bx,by = project(b["position_cm"])
        status = relation["static_sightline"]["status"]
        color = {"blocked_in_annotated_2d_model":"#ff726c","clear_in_annotated_2d_model":"#7de2a8"}.get(status,"#dcb869")
        shapes.append(f'<line x1="{ax}" y1="{ay}" x2="{bx}" y2="{by}" stroke="{color}" stroke-width="0.8" stroke-dasharray="2 1.5"/>')
        route = relation["walking_access"]
        if route.get("vertices_cm") and not route["vertices_truncated"]:
            vertices = " ".join(f"{x:.2f},{y:.2f}" for x,y in map(project,route["vertices_cm"]))
            shapes.append(f'<polyline points="{vertices}" fill="none" stroke="#75baff" stroke-width="0.7" stroke-dasharray="1.2 1.5"/>')
    return shapes


def _diagram_players(snapshot: dict, result: dict, project, transform) -> list[str]:
    shapes = []
    for observation in snapshot["players"]:
        if observation["position_cm"] is None:
            continue
        x,y = project(observation["position_cm"])
        color = "#fff499" if observation["pawn"] == result["selected_player"]["pawn"] else "#79c6ff"
        shapes.append(f'<circle cx="{x}" cy="{y}" r="2.2" fill="{color}" stroke="#111827" stroke-width="0.7"/>')
        label = player(result["players"],observation["pawn"])
        statuses = observation.get("utility_status", {}).get("effects", [])
        if statuses:
            labels = {s["category"].replace("_visual", " effect").replace("_", " ") for s in statuses}
            label += " [" + ", ".join(sorted(labels)) + "]"
        label = escape(label)
        shapes.append(f'<text x="{x+3.5}" y="{y-3}" font-size="5.5" fill="white" stroke="#111827" stroke-width="0.7" paint-order="stroke">{label}</text>')
        if observation["facing"]:
            forward = observation["facing"]
            dx,dy = forward[1]*transform.x_multiplier,forward[0]*transform.y_multiplier
            norm = math.hypot(dx,dy)
            if norm:
                shapes.append(f'<line x1="{x}" y1="{y}" x2="{x+12*dx/norm}" y2="{y+12*dy/norm}" stroke="{color}" stroke-width="1.2" marker-end="url(#heading)"/>')
    return shapes


def _diagram_smokes(snapshot, result, project, transform):
    shapes = []
    radius = result["settings"].get("smoke_radius_cm")
    for smoke in snapshot.get("smoke_observations", {}).get("smokes", []):
        if smoke["position_cm"] is None:
            continue
        x, y = project(smoke["position_cm"])
        if radius is not None:
            rx, ry = radius * abs(transform.x_multiplier) * 1024, radius * abs(transform.y_multiplier) * 1024
            shapes.append(f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="{ry}" fill="#b8a0ff" fill-opacity="0.18" stroke="#c7afff" stroke-width="0.8" stroke-dasharray="2 2"/>')
        shapes.append(f'<circle cx="{x}" cy="{y}" r="1.6" fill="#c7afff"/>')
        shapes.append(f'<text x="{x+3}" y="{y+7}" font-size="4.5" fill="#e0d5ff" stroke="#111827" stroke-width="0.7" paint-order="stroke">{escape(smoke["label"])} (lifecycle)</text>')
    return shapes


def _diagram_angles(snapshot, result, project):
    shapes = []
    entrances = {e["id"]: e for e in result["map_model"]["entrances"]}
    observer = next(p for p in snapshot["players"] if p["pawn"] == result["selected_player"]["pawn"])
    for candidate in snapshot["entrance_context"]["candidates"]:
        a, b = map(project, entrances[candidate["entrance_id"]]["segment_cm"])
        start, end = project(observer["position_cm"]), project(candidate["intersection_cm"])
        shapes += [f'<line x1="{a[0]}" y1="{a[1]}" x2="{b[0]}" y2="{b[1]}" stroke="#f0a9ff" stroke-width="1.8"/>',
                   f'<line x1="{start[0]}" y1="{start[1]}" x2="{end[0]}" y2="{end[1]}" stroke="#fff499" stroke-width="1" stroke-dasharray="3 1"/>',
                   f'<circle cx="{end[0]}" cy="{end[1]}" r="2.5" fill="#fff499"/>',
                   f'<text x="{end[0]+4}" y="{end[1]-5}" font-size="5" fill="#fff499" stroke="#111827" stroke-width="0.7" paint-order="stroke">{escape(candidate["label"])}: facing {escape(candidate["facing_side"])}</text>']
    return shapes


def render_map_svg(model: dict, image_path: Path | None = None) -> str:
    """Whole-map annotation diagnostic, independent of any private replay."""
    transform = MapTransform.from_dict(model["projection"])
    def project(point):
        return tuple(n*1024 for n in transform.project(point))
    image = _image_data(image_path,model)
    shapes = _diagram_geometry(model,project)
    for landmark in model["landmarks"]:
        x, y = project(landmark["position_cm"])
        shapes += [f'<circle cx="{x}" cy="{y}" r="2" fill="#fff499"/>',
                   f'<text x="{x+4}" y="{y-4}" font-size="10" fill="white" stroke="#111827" stroke-width="2" paint-order="stroke">{escape(landmark["label"])}</text>']
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="1200" viewBox="0 0 1100 1200">'
            '<rect width="1100" height="1200" fill="#101724"/>'
            '<g font-family="Segoe UI,sans-serif" fill="#eef3ff">'
            '<text x="30" y="37" font-size="25">Sunset: full 2D minimap footprint</text>'
            '<text x="30" y="65" font-size="14">Candidate geometry; minimap artwork is not validated collision or height.</text>'
            f'<text x="30" y="88" font-size="13">{len(model["coverage_polygon_cm"])} outline vertices / {len(model["sight_blockers"])} enclosed voids / {len(model["landmarks"])} callout points</text>'
            f'<svg x="38" y="110" width="1024" height="1024" viewBox="0 0 1024 1024">'
            f'<image href="{image or ""}" x="0" y="0" width="1024" height="1024"/>{"".join(shapes)}</svg>'
            '<text x="30" y="1173" font-size="14">Teal: floor-outline hypothesis / orange: enclosed voids / yellow: public callout points, not regions</text></g></svg>')


def render_spatial_svg(result: dict, image_path: Path | None = None) -> str:
    model = result["map_model"]
    if model is None or not result["snapshots"]:
        raise QueryError("diagram_unavailable", "A spatial diagram needs a map model and at least one snapshot.")
    transform = MapTransform.from_dict(model["projection"])
    def project(point):
        return tuple(n*1024 for n in transform.project(point))
    polygon = [project(p) for p in model["coverage_polygon_cm"]]
    snapshot = result["snapshots"][0]
    observed = [project(p["position_cm"]) for p in snapshot["players"] if p["position_cm"] is not None]
    observed += [project(s["position_cm"]) for s in snapshot.get("smoke_observations", {}).get("smokes", [])
                 if s["position_cm"] is not None]
    active_entrances = {c["entrance_id"] for c in snapshot["entrance_context"]["candidates"]}
    observed += [project(p) for e in model["entrances"] if e["id"] in active_entrances for p in e["segment_cm"]]
    # Show the inspected players/entrances; full-map geometry remains in JSON.
    if observed:
        polygon = observed
    x,y = min(p[0] for p in polygon)-20,min(p[1] for p in polygon)-20
    w,h = max(p[0] for p in polygon)-x+20,max(p[1] for p in polygon)-y+20
    if w < 140:
        x, w = x-(140-w)/2, 140
    if h < 140:
        y, h = y-(140-h)/2, 140
    image = _image_data(image_path,model)
    background = f'<image href="{image}" x="0" y="0" width="1024" height="1024"/>' if image else ''
    shapes = (_diagram_geometry(model,project)+_diagram_smokes(snapshot,result,project,transform)
              +_diagram_relations(snapshot,project)+_diagram_angles(snapshot,result,project)
              +_diagram_players(snapshot,result,project,transform))
    title = f"Round {result['round']['number']} at {seconds(snapshot['round_time_ms'])}"
    ages = [p["sample_age_ms"] for p in snapshot["players"] if p["position_cm"] is not None]
    oldest = str(max(ages)) if ages else "unknown"
    metadata = {"match_id": result["match_id"], "replay_revision": result["revision"],
                "geometry_revision": model["revision"], "replay_build": result["replay_build"],
                "time_ms": snapshot["time_ms"], "round_time_ms": snapshot["round_time_ms"],
                "smoke_observations": snapshot.get("smoke_observations"),
                "player_status_observations": {p["pawn"]: p.get("utility_status") for p in snapshot["players"]},
                "samples": [{k: p[k] for k in ("pawn", "status", "sample_time_ms", "sample_age_ms", "source")}
                            for p in snapshot["players"]]}
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="1000" height="880" viewBox="0 0 1000 880">'
            f'<metadata>{escape(json.dumps(metadata, ensure_ascii=True))}</metadata>'
            '<rect width="1000" height="880" fill="#101724"/>'
            '<defs><marker id="heading" markerWidth="4" markerHeight="4" refX="3" refY="2" orient="auto"><path d="M0,0 L4,2 L0,4" fill="context-stroke"/></marker></defs>'
            f'<g fill="#eef3ff" font-family="Segoe UI, sans-serif"><text x="30" y="38" font-size="25">{escape(title)}</text>'
            f'<text x="30" y="65" font-size="15">Model: {escape(model["validation"])} — 2D footprints; actual visibility/access unknown</text>'
            f'<text x="30" y="85" font-size="12">Global {snapshot["time_ms"]} ms · oldest displayed sample {oldest} ms · geometry {escape(model["revision"][:16])}</text></g>'
            f'<svg x="30" y="95" width="940" height="670" viewBox="{x} {y} {w} {h}">{background}{"".join(shapes)}</svg>'
            '<g fill="#d2dbe8" font-family="Segoe UI, sans-serif" font-size="14">'
            '<text x="30" y="795">Orange: annotated blocker · teal: walking surface · dashed border: modeled coverage</text>'
            '<text x="30" y="821">Red: blocked 2D line · green: clear 2D line · amber: unknown · blue dashes: modeled walking route</text>'
            '<text x="30" y="847">Yellow ray: entrance-facing estimate · pink: entrance · first snapshot only; actual vision unknown</text></g></svg>')
