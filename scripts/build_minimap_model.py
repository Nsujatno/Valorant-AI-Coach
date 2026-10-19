"""Build an inspectable 2D candidate from local public minimap assets.

Offline development tool; Pillow is needed here, not by the replay runtime.
Opaque artwork is a floor footprint hypothesis, not game collision or height.
"""

import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path

from PIL import Image


def area(points):
    return sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(points, points[1:]+points[:1]))/2


def contours(image):
    pixels = image.getchannel("A")
    width, height = image.size
    filled = {(x, y) for y in range(height) for x in range(width) if pixels.getpixel((x, y)) >= 128}
    outgoing = defaultdict(set)
    for x, y in filled:
        for neighbor, start, end in (
            ((x,y-1),(x,y),(x+1,y)), ((x+1,y),(x+1,y),(x+1,y+1)),
            ((x,y+1),(x+1,y+1),(x,y+1)), ((x-1,y),(x,y+1),(x,y))):
            if neighbor not in filled:
                outgoing[start].add(end)
    directions = {(1,0): 0, (0,1): 1, (-1,0): 2, (0,-1): 3}
    loops = []
    while outgoing:
        start = min(outgoing)
        current, previous, loop = start, (start[0]-1, start[1]), []
        while True:
            loop.append(current)
            heading = directions[(current[0]-previous[0], current[1]-previous[1])]
            def turn(end):
                change = (directions[(end[0]-current[0], end[1]-current[1])]-heading)%4
                return {1: 0, 0: 1, 3: 2, 2: 3}[change]
            end = min(outgoing[current], key=turn)
            outgoing[current].remove(end)
            if not outgoing[current]:
                del outgoing[current]
            previous, current = current, end
            if current == start:
                break
        loops.append(loop)
    return loops


def simplify_open(points, tolerance):
    if len(points) < 3:
        return points
    a, b = points[0], points[-1]
    dx, dy = b[0]-a[0], b[1]-a[1]
    def deviation(p):
        t = max(0, min(1, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy)))
        return math.hypot(p[0]-a[0]-t*dx, p[1]-a[1]-t*dy)
    index = max(range(1, len(points)-1), key=lambda i: deviation(points[i]))
    if deviation(points[index]) <= tolerance:
        return [a, b]
    return simplify_open(points[:index+1], tolerance)[:-1]+simplify_open(points[index:], tolerance)


def simplify_loop(points, tolerance):
    split = max(range(len(points)), key=lambda i: math.dist(points[0], points[i]))
    return (simplify_open(points[:split+1], tolerance)[:-1]
            +simplify_open(points[split:]+points[:1], tolerance)[:-1])


def build(image_path, metadata_path, annotations_path):
    image = Image.open(image_path).convert("RGBA")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    image_hash = hashlib.sha256(image_path.read_bytes()).hexdigest()
    if (annotations["image_size"] != list(image.size) or annotations["map_asset"] != metadata["mapUrl"]
            or annotations["image_sha256"] != image_hash):
        raise ValueError("Entrance annotations must match the map asset, image dimensions and checksum.")
    width, height = image.size
    def world(point):
        u, v = point[0]/width, point[1]/height
        return [round((v-metadata["yScalarToAdd"])/metadata["yMultiplier"], 4),
                round((u-metadata["xScalarToAdd"])/metadata["xMultiplier"], 4)]
    loops = contours(image)
    outer = max(loops, key=area)
    holes = sorted((p for p in loops if area(p) < -16), key=lambda p: (min(p), area(p)))
    footprint = [world(p) for p in simplify_loop(outer, 0.75)]
    blockers = [{"id": f"minimap-void-{i+1:02}", "polygon_cm": [world(p) for p in simplify_loop(hole, 0.75)],
                 "height": "unknown", "source": "Enclosed transparent minimap void; candidate footprint, not collision."}
                for i, hole in enumerate(holes)]
    entrances = []
    for entry in annotations["entrances"]:
        converted = {k: entry[k] for k in ("id", "label", "max_range_cm", "source")}
        for key in ("segment", "observer_polygon", "approach_polygon"):
            converted[key+"_cm"] = [world(p) for p in entry[key+"_px"]]
        converted["annotation_px"] = {k: v for k, v in entry.items() if k.endswith("_px")}
        entrances.append(converted)
    return {"schema_version": 2, "id": metadata["displayName"].lower()+"-full-minimap-v1",
            "name": metadata["displayName"]+" full minimap footprint (candidate)",
            "map_asset": metadata["mapUrl"], "coordinate_space": "world_cm_xy", "validated_builds": [],
            "candidate_builds": annotations["candidate_builds"],
            "projection": {k: metadata[k] for k in ("xMultiplier", "yMultiplier", "xScalarToAdd", "yScalarToAdd")},
            "height_policy": "2D minimap footprint; floors, eye/camera positions and cover height unvalidated",
            "grid_resolution_cm": 50, "max_grid_cells": 100000, "actor_radius_cm": 35, "edge_uncertainty_cm": 30,
            "provenance": {"metadata_url": "https://valorant-api.com/v1/maps", "image_url": metadata["displayIcon"],
                "metadata_snapshot_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
                "image_sha256": image_hash, "image_size": list(image.size),
                "annotation_sha256": hashlib.sha256(annotations_path.read_bytes()).hexdigest(),
                "builder_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "annotation_method": "Alpha >= 128 floor outline and enclosed voids; 0.75 pixel simplification. Manual entrance cross-sections.",
                "coordinate_calibration": "Public projection; prior user position checks are case-specific, not full-map calibration.",
                "validation_status": "Unverified candidate; minimap artwork is not authoritative collision, walkability or visibility.",
                "ignored_small_voids": sum(-16 <= area(p) < 0 for p in loops),
                "detached_components": sum(area(p) > 0 and p is not outer for p in loops)},
            "coverage_polygon_cm": footprint, "walking_polygon_cm": footprint,
            "sight_blockers": blockers, "movement_blockers": blockers, "regions": [],
            "landmarks": [{"label": c["superRegionName"]+" "+c["regionName"],
                           "position_cm": [c["location"]["x"], c["location"]["y"]]}
                          for c in metadata["callouts"]], "entrances": entrances}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--metadata", required=True, type=Path)
    parser.add_argument("--annotations", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    model = build(args.image, args.metadata, args.annotations)
    with args.output.open("x", encoding="utf-8") as handle:
        json.dump(model, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    print(f"Built {len(model['coverage_polygon_cm'])} outline vertices, {len(model['sight_blockers'])} enclosed voids, "
          f"{len(model['landmarks'])} landmarks and {len(model['entrances'])} entrances.")
