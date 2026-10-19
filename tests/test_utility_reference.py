"""Private source regressions; observed utility intervals await playback review."""

import json
import os
from pathlib import Path
import unittest

from valcoach.normalize import load_match
from valcoach.query import query_round, select_player
from valcoach.render import render_round, render_spatial
from valcoach.source import Export
from valcoach.spatial import SpatialSettings, query_spatial

ROOT = Path(__file__).resolve().parents[1]
EXPORT = Path(os.environ.get("VALCOACH_TEST_EXPORT_1308", ROOT / "output/replay-5c31718d/raw"))


@unittest.skipUnless((EXPORT / "manifest.json").is_file(), "Second private replay export not available")
class UtilityReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = Export(EXPORT)
        cls.model, _ = load_match(cls.export)
        cls.player = select_player(cls.model, "58")

    def test_r3_blind_effect_pair_preserves_source_and_unknown_severity(self):
        records = self.model["utility"]["statuses"]
        blind = next(r for r in records if r["player"] == 1270 and r["start_ms"] == 231340)
        self.assertEqual(blind["category"], "blind_visual")
        self.assertEqual(blind["end_ms"], 232841)
        self.assertEqual(blind["start_sources"][0]["row"], 197105)
        self.assertIsNone(blind["severity"])
        report = query_spatial(self.model, self.export, 3, self.player, 25200, 27200,
                               SpatialSettings(step_ms=500), [select_player(self.model, "62")])
        first, last = report["snapshots"][0], report["snapshots"][-1]
        self.assertEqual(first["players"][0]["utility_status"]["effects"][0]["category"], "blind_visual")
        self.assertEqual(last["players"][0]["utility_status"]["effects"], [])
        self.assertIsNone(first["relations"][0]["actual_visibility"])
        self.assertIn("blind_visual", render_spatial(report))
        json.dumps(report, allow_nan=False)

    def test_smoke_spawn_owner_and_close_are_not_an_opaque_volume(self):
        smoke = next(s for s in self.model["utility"]["smokes"] if s["actor"] == 4104)
        self.assertEqual((smoke["start_ms"], smoke["end_ms"], smoke["owner"]), (48285, 66533, 770))
        self.assertEqual(smoke["positions"][0]["position_cm"], [-1151.5, -5117.10009765625, 275])
        self.assertIsNone(smoke["radius_cm"])
        self.assertIsNone(smoke["opaque_start_ms"])
        self.assertTrue(smoke["position_updates_unresolved"] > 0)

    def test_concussion_is_distinct_and_all_supported_pairs_are_preserved(self):
        data = self.model["utility"]
        concussion = [s for s in data["statuses"] if s["category"] == "concussion_visual"]
        self.assertEqual([(s["player"], s["start_ms"], s["end_ms"]) for s in concussion],
                         [(866, 1133895, 1135302), (968, 1137904, 1139318)])
        self.assertEqual(data["coverage"]["unpaired_supported_starts"], 0)
        self.assertTrue(all("_Equip_" not in s["effect_path"] and "Windup" not in s["effect_path"] for s in data["statuses"]))
        selected_updates = [b for b in data["blind_updates"] if b["player"] == 1270]
        self.assertEqual(len(selected_updates), 10)
        self.assertTrue(all(b["severity"] is None and b["duration_ms"] is None for b in selected_updates))

    def test_round_fight_utility_references_resolve_without_changing_old_counts(self):
        report = query_round(self.model, self.export, 3, self.player)
        for fight in report["teammate_fights"] + report["player_fights"]:
            refs = fight["utility_context"]
            self.assertTrue(all(key in report["evidence"] for key in refs["status_ids"] + refs["smoke_ids"]))
        self.assertTrue(any(r.get("category") == "blind_visual" and r.get("player") == 1270 for r in report["utility"]["records"]))
        self.assertIn("## Utility observations", render_round(report))
        self.assertEqual(report["coverage"]["deaths_matched_to_damage"], report["coverage"]["death_events"])
