"""Spatial evidence boundaries and synthetic geometry, not gameplay truth."""

from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import pyarrow as pa
import pyarrow.parquet as pq

from valcoach.__main__ import main
from valcoach.geometry import WalkingGrid, distance, sightline
from valcoach.map_model import MapTransform, load_map_model
from valcoach.render.spatial import render_spatial, render_spatial_svg
from valcoach.source import Export, QueryError
from valcoach.spatial import SpatialSettings, facing_vector, query_spatial, relative_bearing

ASSET = "/Game/Maps/Juliett/Juliett"
BUILD = "++Ares-Core+release-13.08"
SQUARE = ((0, 0), (1000, 0), (1000, 1000), (0, 1000))
WALL = {"id": "wall", "polygon": ((400, 200), (600, 200), (600, 800), (400, 800))}


def match(events=None):
    return {"match_id": "synthetic", "revision": "synthetic-revision", "replay_build": BUILD,
            "map_asset": "unmodeled", "rounds": [{"number": 1, "buy_ms": 500, "live_ms": 1000, "end_ms": 20000}],
            "timeline": events or [], "players": [
                {"id": "a", "state_id": 11, "pawn": 1, "team": 1, "agent": "Jett"},
                {"id": "b", "state_id": 12, "pawn": 2, "team": 2, "agent": "Sova"}]}


def movement(pawn, time, xyz=(100, 100, 0), yaw=0, pitch=0, row=0):
    return {"character_net_guid": pawn, "time_ms": time, "source_row": row, "packet_id": 42,
            **dict(zip(("pos_x", "pos_y", "pos_z"), xyz)), "yaw": yaw, "pitch": pitch}


class MemoryExport:
    def __init__(self, rows):
        self.rows = rows

    def movement(self, start, end, pawns):
        self.request = (start, end, pawns)
        return [r for r in self.rows if start <= r["time_ms"] < end and r["character_net_guid"] in pawns]


class GeometryTests(unittest.TestCase):
    def test_blocked_line_can_have_a_walking_detour(self):
        a, b = (200, 500), (800, 500)
        self.assertEqual(sightline(a, b, SQUARE, [WALL], 10)["status"], "blocked_in_annotated_2d_model")
        grid = WalkingGrid(SQUARE, [WALL], 50, 30)
        route = grid.route(a, b)
        self.assertEqual(route["status"], "walking_route_found_in_model")
        self.assertGreater(route["length_m"], distance(a, b)/100)
        self.assertTrue(all(grid.connection_valid(x, y) for x, y in zip(route["vertices_cm"], route["vertices_cm"][1:])))

    def test_low_cover_can_block_movement_without_being_a_sight_blocker(self):
        a, b = (200, 500), (800, 500)
        self.assertEqual(sightline(a, b, SQUARE, [], 10)["status"], "clear_in_annotated_2d_model")
        self.assertGreater(WalkingGrid(SQUARE, [WALL], 50, 30).route(a, b)["length_m"], 6)

    def test_edges_inside_blockers_and_partial_coverage_remain_unknown(self):
        for a, b, reason in [((200, 190), (800, 190), "near_sight_blocker_edge"),
                             ((200, 205), (800, 205), "near_sight_blocker_edge"),
                             ((500, 500), (800, 500), "sample_inside_sight_blocker"),
                             ((-10, 500), (800, 500), "outside_or_near_coverage_edge"),
                             ((0, 100), (800, 100), "outside_or_near_coverage_edge")]:
            with self.subTest(reason=reason):
                result = sightline(a, b, SQUARE, [WALL], 10)
                self.assertEqual((result["status"], result["reason"]), ("unknown", reason))

    def test_concave_coverage_cannot_bridge_an_unmodeled_gap(self):
        concave = ((0, 0), (1000, 0), (1000, 1000), (600, 1000), (600, 300), (400, 300), (400, 1000), (0, 1000))
        self.assertEqual(sightline((200, 800), (800, 800), concave, [], 0)["status"], "unknown")

    def test_closed_wall_is_no_route_only_within_model(self):
        wall = {"id": "closed", "polygon": ((400, -20), (600, -20), (600, 1020), (400, 1020))}
        result = WalkingGrid(SQUARE, [wall], 50, 30).route((200, 500), (800, 500))
        self.assertEqual(result["status"], "no_route_in_modeled_area")

    def test_doorway_and_actor_clearance_are_separate_from_a_point_ray(self):
        walls = [{"id": "bottom", "polygon": ((400, -20), (600, -20), (600, 455), (400, 455))},
                 {"id": "top", "polygon": ((400, 545), (600, 545), (600, 1020), (400, 1020))}]
        a, b = (200, 500), (800, 500)
        self.assertEqual(sightline(a, b, SQUARE, walls, 10)["status"], "clear_in_annotated_2d_model")
        self.assertEqual(WalkingGrid(SQUARE, walls, 25, 30).route(a, b)["status"], "walking_route_found_in_model")
        self.assertEqual(WalkingGrid(SQUARE, walls, 25, 50).route(a, b)["status"], "no_route_in_modeled_area")

    def test_route_endpoints_and_grid_size_fail_conservatively(self):
        self.assertEqual(WalkingGrid(SQUARE, [], 50, 30).route((20, 500), (800, 500))["status"], "unknown")
        with self.assertRaises(ValueError):
            WalkingGrid(SQUARE, [], 1, 1)


class MapModelTests(unittest.TestCase):
    def test_transform_axes_and_inverse(self):
        transform = MapTransform(0.000078, -0.000078, 0.5, 0.515625)
        self.assertEqual(transform.project((1000, 2000)), (0.656, 0.437625))
        for actual, expected in zip(transform.unproject(transform.project((1000, 2000))), (1000, 2000)):
            self.assertAlmostEqual(actual, expected)

    def test_build_matching_never_silently_validates_candidate(self):
        model = load_map_model(ASSET)
        self.assertEqual(model.applicability(BUILD, False), "unvalidated_for_build")
        self.assertEqual(model.applicability(BUILD, True), "candidate_unverified")
        self.assertEqual(model.applicability("different-build", True), "unvalidated_for_build")
        self.assertEqual(model.data["validated_builds"], [])
        self.assertIsNone(load_map_model("different-map"))

    def test_mismatched_map_and_bad_geometry_have_stable_errors(self):
        base = load_map_model(ASSET).data
        cases = [("map_model_mismatch", {"map_asset": "different-map"}),
                 ("invalid_map_model", {"projection": {}}),
                 ("invalid_map_model", {"walking_polygon_cm": [[0, 0], [1000, 1000], [0, 1000], [1000, 0]]}),
                 ("invalid_map_model", {"edge_uncertainty_cm": float("nan")}),
                 ("invalid_map_model", {"grid_resolution_cm": 0})]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"model.json"
            for code, updates in cases:
                with self.subTest(updates=updates):
                    path.write_text(json.dumps({**base, **updates}), encoding="utf-8")
                    with self.assertRaises(QueryError) as caught:
                        load_map_model(ASSET, path)
                    self.assertEqual(caught.exception.code, code)

    def test_geometry_revision_changes_without_replay_revision(self):
        original = load_map_model(ASSET)
        data = deepcopy(original.data)
        data["edge_uncertainty_cm"] += 1
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"model.json"
            path.write_text(json.dumps(data), encoding="utf-8")
            self.assertNotEqual(load_map_model(ASSET, path).revision, original.revision)


class SpatialTests(unittest.TestCase):
    def report(self, rows, start=0, end=1, settings=None, model=None, targets=None):
        model = model or match()
        return query_spatial(model, MemoryExport(rows), 1, model["players"][0], start, end, settings, targets)

    def test_facing_angle_wrap_and_vertical_components(self):
        self.assertEqual(facing_vector(90, 0), [0, 1, 0])
        self.assertEqual(facing_vector(0, 90), [0, 0, 1])
        origin = {"position_cm": [0, 0, 0], "yaw_deg": 359, "pitch_deg": 355}
        target = {"position_cm": [100, 0, 0]}
        result = relative_bearing(origin, target)
        self.assertEqual(result["horizontal_offset_deg"], 1)
        self.assertEqual(result["vertical_offset_deg"], 5)
        self.assertAlmostEqual(result["angular_offset_deg"], 5.099, places=3)
        origin["yaw_deg"] = None
        self.assertIsNone(relative_bearing(origin, target)["angular_offset_deg"])
        origin["position_cm"] = target["position_cm"]
        self.assertEqual(relative_bearing(origin, target)["bearing_reason"], "coincident_positions")

    def test_last_observation_freshness_and_provenance_without_future_leak(self):
        rows = [movement(1, 900, row=7), movement(1, 1100, xyz=(900, 900, 0), row=8), movement(2, 1000, xyz=(200, 100, 0), row=9)]
        report = self.report(rows)
        observation = report["snapshots"][0]["players"][0]
        self.assertEqual(observation["position_cm"], [100, 100, 0])
        self.assertEqual(observation["sample_age_ms"], 100)
        self.assertEqual(observation["source"], {"table": "movement", "row": 7, "packet_id": 42})
        relation = report["snapshots"][0]["relations"][0]
        self.assertEqual(relation["distance_m"], 1)
        self.assertIsNone(relation["actual_visibility"])
        self.assertEqual(relation["static_sightline"]["reason"], "map_model_unavailable")
        stale = self.report(rows, settings=SpatialSettings(movement_max_age_ms=99))
        self.assertEqual(stale["snapshots"][0]["players"][0]["status"], "missing_or_stale")

    def test_nonfinite_parked_missing_rotation_and_missing_player_stay_unknown(self):
        for xyz, status in [((math.nan, 0, 0), "nonfinite_position"), ((-50000, 0, -49900), "parked_actor")]:
            report = self.report([movement(1, 1000, xyz), movement(2, 1000)])
            self.assertEqual(report["snapshots"][0]["players"][0]["status"], status)
            self.assertIsNone(report["snapshots"][0]["relations"][0]["distance_m"])
            json.dumps(report, allow_nan=False)
        report = self.report([movement(1, 1000, yaw=None), movement(2, 1000, (200, 100, 0))])
        self.assertIsNone(report["snapshots"][0]["relations"][0]["horizontal_offset_deg"])
        self.assertEqual(self.report([])["snapshots"][0]["players"][0]["status"], "missing_or_stale")

    def test_partial_rotation_preserves_known_yaw_and_horizontal_bearing(self):
        report = self.report([movement(1, 1000, pitch=None), movement(2, 1000, (200, 100, 0))])
        observation = report["snapshots"][0]["players"][0]
        relation = report["snapshots"][0]["relations"][0]
        self.assertEqual(observation["yaw_deg"], 0)
        self.assertIsNone(observation["pitch_deg"])
        self.assertEqual(relation["horizontal_offset_deg"], 0)
        self.assertIsNone(relation["angular_offset_deg"])

    def test_death_resurrection_cannot_reuse_position_from_previous_life(self):
        model = match([{"kind": "death", "victim": 1, "time_ms": 1100},
                       {"kind": "resurrection", "player": 1, "time_ms": 1200}])
        rows = [movement(1, 1050), movement(2, 1050)]
        dead = self.report(rows, 100, 101, model=model)
        self.assertEqual(dead["snapshots"][0]["players"][0]["status"], "player_dead")
        revived = self.report(rows, 200, 201, model=model)
        self.assertEqual(revived["snapshots"][0]["players"][0]["status"], "sample_before_resurrection")
        fresh = self.report([*rows, movement(1, 1200)], 200, 201, model=model)
        self.assertEqual(fresh["snapshots"][0]["players"][0]["status"], "observed")

    def test_candidate_requires_opt_in_and_preserves_height_dynamic_unknowns(self):
        model = match()
        model["map_asset"] = ASSET
        rows = [movement(1, 1000, (2023, -3945, 500)), movement(2, 1000, (-424, -4786, 300))]
        default = self.report(rows, model=model)
        self.assertEqual(default["snapshots"][0]["relations"][0]["static_sightline"]["status"], "unknown")
        candidate = self.report(rows, model=model, settings=SpatialSettings(allow_candidate_map=True))
        relation = candidate["snapshots"][0]["relations"][0]
        self.assertEqual(relation["static_sightline"]["status"], "blocked_in_annotated_2d_model")
        self.assertIn("unvalidated", relation["height_assessment"])
        self.assertEqual(relation["dynamic_occlusion"], "unknown")
        self.assertIsNone(relation["actual_access"])

    def test_window_settings_and_target_errors(self):
        for start, end, code in [(0, 0, "invalid_time_range"), (-501, 0, "invalid_time_range"),
                                 (0, 10001, "window_too_large"), (18000, 19001, "invalid_time_range")]:
            with self.subTest(start=start, end=end), self.assertRaises(QueryError) as caught:
                self.report([], start, end)
            self.assertEqual(caught.exception.code, code)
        for settings in (SpatialSettings(step_ms=0), SpatialSettings(limit=201), SpatialSettings(movement_max_age_ms=1001)):
            with self.assertRaises(QueryError):
                self.report([], settings=settings)
        with self.assertRaises(QueryError) as caught:
            self.report([], targets=[match()["players"][1]]*2)
        self.assertEqual(caught.exception.code, "invalid_spatial_targets")

    def test_snapshot_relation_and_route_limits_are_explicit(self):
        report = self.report([], 0, 1000, SpatialSettings(step_ms=1, limit=2))
        self.assertEqual(report["coverage"]["next_start_ms"], 2)
        self.assertEqual(report["coverage"]["snapshots_requested"], 1000)
        self.assertTrue(report["coverage"]["truncated"])
        model = match()
        model["map_asset"] = ASSET
        model["players"] += [{**model["players"][1], "id": str(p), "pawn": p} for p in range(3, 18)]
        rows = [movement(p["pawn"], 1000, (2023, -3945-p["pawn"], 500)) for p in model["players"]]
        report = self.report(rows, 0, 100, SpatialSettings(step_ms=1, limit=100, allow_candidate_map=True), model)
        coverage = report["coverage"]
        self.assertEqual(coverage["relations_returned"], 500)
        self.assertEqual(coverage["route_evaluations"], 32)
        self.assertEqual(coverage["routes_omitted_by_budget"], 468)
        self.assertTrue(coverage["truncated"])

    def test_movement_parquet_rotation_optional_and_source_rows_survive_filter(self):
        with tempfile.TemporaryDirectory() as directory:
            export = Export.__new__(Export)
            export.root = Path(directory)
            rows = [{**movement(1, t, row=i), "vel_x": 0, "vel_y": 0, "vel_z": 0} for i, t in enumerate((800, 1000))]
            for rotation in (True, False):
                values = [{k: v for k, v in r.items() if k != "source_row" and (rotation or k not in ("yaw", "pitch"))} for r in rows]
                pq.write_table(pa.Table.from_pylist(values), export.root/"movement.parquet")
                filtered = export.movement(900, 1100, [1])
                self.assertEqual(filtered[0]["source_row"], 1)
                self.assertEqual("yaw" in filtered[0], rotation)

    def test_diagram_xml_escaping_and_checksum_guard(self):
        model = match()
        model["map_asset"] = ASSET
        model["players"][1]["agent"] = "<script>&"
        report = self.report([movement(1, 1000, (2023, -3945, 500)), movement(2, 1000, (2115, -5590, 500))], model=model)
        svg = render_spatial_svg(report)
        ET.fromstring(svg)
        self.assertIn("&lt;script&gt;&amp;", svg)
        self.assertIn("actual visibility/access unknown", svg)
        self.assertIn("Static results describe an annotated 2D model", render_spatial(report))
        with tempfile.TemporaryDirectory() as directory:
            image = Path(directory)/"wrong.png"
            image.write_bytes(b"\x89PNG\r\n\x1a\nwrong")
            with self.assertRaises(QueryError) as caught:
                render_spatial_svg(report, image)
            self.assertEqual(caught.exception.code, "invalid_map_image")

    def test_diagram_frames_positions_outside_the_geometry_area(self):
        model = match()
        model["map_asset"] = ASSET
        transform = load_map_model(ASSET).transform
        a, b = transform.unproject((0.72, 0.46)), transform.unproject((0.81, 0.55))
        report = self.report([movement(1, 1000, (*a, 300)), movement(2, 1000, (*b, 300))], model=model)
        root = ET.fromstring(render_spatial_svg(report))
        viewport = root.find("{http://www.w3.org/2000/svg}svg")
        x, y, width, height = map(float, viewport.attrib["viewBox"].split())
        for u, v in ((0.72, 0.46), (0.81, 0.55)):
            self.assertTrue(x < u*1024 < x+width and y < v*1024 < y+height)
        self.assertEqual(report["snapshots"][0]["relations"][0]["static_sightline"]["status"], "unknown")

    def test_cli_protects_existing_outputs_and_raw_paths_before_reading(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root/"raw"
            raw.mkdir()
            existing = root/"existing.svg"
            existing.write_text("preserve", encoding="utf-8")
            base = ["spatial", "1", "--export", str(raw), "--player", "11", "--start-ms", "0", "--end-ms", "1", "--json"]
            for path, code in [(existing, "output_exists"), (raw/"unsafe.svg", "invalid_output")]:
                stdout = io.StringIO()
                with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
                    self.assertEqual(main([*base, "--diagram", str(path)]), 1)
                self.assertEqual(json.loads(stdout.getvalue())["error"]["code"], code)
            self.assertEqual(existing.read_text(), "preserve")
            with patch("valcoach.__main__.Export", return_value=MemoryExport([])), patch("valcoach.__main__.load_match", return_value=(match(), False)):
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    self.assertEqual(main(base), 0)
                self.assertEqual(json.loads(stdout.getvalue())["query"], "spatial")


if __name__ == "__main__":
    unittest.main()
