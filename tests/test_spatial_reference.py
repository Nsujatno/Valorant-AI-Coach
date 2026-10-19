"""Private-export spatial regressions; map estimates remain independently unverified."""

import os
from pathlib import Path
import unittest

from valcoach.normalize import load_match
from valcoach.query import select_player
from valcoach.source import Export
from valcoach.spatial import SpatialSettings, query_spatial

ROOT = Path(__file__).resolve().parents[1]
NEW_EXPORT = Path(os.environ.get("VALCOACH_TEST_EXPORT_1308", ROOT/"output/replay-5c31718d/raw"))
OLD_EXPORT = Path(os.environ.get("VALCOACH_TEST_EXPORT", ROOT/"output/first-replay/raw"))


@unittest.skipUnless((NEW_EXPORT/"manifest.json").is_file(), "Second private replay export not available")
class SunsetSpatialTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = Export(NEW_EXPORT)
        cls.model, _ = load_match(cls.export)
        cls.player = select_player(cls.model, "58")
        cls.targets = [select_player(cls.model, s) for s in ("54", "64", "72")]
        cls.report = query_spatial(cls.model, cls.export, 2, cls.player, 29635, 29885,
                                   SpatialSettings(step_ms=125, allow_candidate_map=True), cls.targets,
                                   map_path=ROOT/"valcoach/mapdata/sunset-full-v1.json")

    def test_r2_recorded_samples_and_bearings(self):
        first, second = self.report["snapshots"]
        self.assertEqual([first["time_ms"], second["time_ms"]], [154633, 154758])
        self.assertEqual(first["players"][0]["position_cm"], [2022.83, -3944.78, 500.41])
        self.assertTrue(all(p["sample_age_ms"] == 0 for p in first["players"]))
        self.assertEqual(first["players"][0]["source"]["table"], "movement")
        relations = {r["to"]: r for r in first["relations"]}
        self.assertAlmostEqual(relations[538]["horizontal_offset_deg"], -2.606, places=3)
        self.assertAlmostEqual(relations[968]["horizontal_offset_deg"], 10.191, places=3)
        self.assertEqual(second["players"][1]["status"], "player_dead")
        self.assertIsNone(second["players"][1]["position_cm"])

    def test_candidate_estimates_preserve_uncertainty(self):
        self.assertEqual(self.report["map_model"]["id"], "sunset-full-minimap-v1")
        self.assertEqual(self.report["map_model"]["validation"], "candidate_unverified")
        relations = {r["to"]: r for r in self.report["snapshots"][0]["relations"]}
        self.assertEqual(relations[968]["static_sightline"]["status"], "blocked_in_annotated_2d_model")
        self.assertEqual(relations[968]["static_sightline"]["blocker_ids"], ["minimap-void-01"])
        # The full outline adds a corner absent from the earlier B-only trace.
        # Grazing that corner's uncertainty band does not establish a clear line.
        self.assertEqual(relations[538]["static_sightline"]["status"], "unknown")
        self.assertEqual(relations[538]["static_sightline"]["reason"], "near_sight_blocker_edge")
        self.assertEqual(relations[538]["static_sightline"]["blocker_ids"], ["minimap-void-02"])
        self.assertEqual(relations[770]["walking_access"]["length_m"], 16.481)
        self.assertTrue(all(r["actual_visibility"] is None and r["actual_access"] is None for r in relations.values()))
        self.assertIn("unvalidated", relations[968]["height_assessment"])

    def test_default_does_not_enable_unverified_geometry(self):
        report = query_spatial(self.model, self.export, 2, self.player, 29635, 29636, targets=self.targets)
        self.assertEqual(report["map_model"]["validation"], "unvalidated_for_build")
        self.assertTrue(all(r["static_sightline"]["status"] == "unknown" for r in report["snapshots"][0]["relations"]))


@unittest.skipUnless((OLD_EXPORT/"manifest.json").is_file(), "Original private replay export not available")
class UnmodeledSpatialTests(unittest.TestCase):
    def test_original_export_has_positions_without_a_map_model(self):
        export = Export(OLD_EXPORT)
        model, _ = load_match(export)
        report = query_spatial(model, export, 1, select_player(model, "254"), 5000, 5001)
        self.assertIsNone(report["map_model"])
        self.assertTrue(any(p["status"] == "observed" for p in report["snapshots"][0]["players"]))
        self.assertTrue(all(r["static_sightline"]["status"] == "unknown" for r in report["snapshots"][0]["relations"]))
