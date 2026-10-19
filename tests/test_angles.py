"""Map entrance estimates with independent synthetic geometry and replay checks."""

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import random
import tempfile
import unittest

from valcoach.fight import FightSettings, query_fight
from valcoach.geometry import contains, crosses, edge_distance, edges, inside_segment, near_boundary, segment_distance
from valcoach.map_model import load_map_model
from valcoach.normalize import load_match
from valcoach.query import select_player
from valcoach.source import Export, QueryError
from valcoach.spatial import SpatialSettings, query_spatial
from tests.test_spatial import ASSET, SQUARE, MemoryExport, match, movement

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path(os.environ.get("VALCOACH_TEST_EXPORT_1308", ROOT/"output/replay-5c31718d/raw"))


def entrance():
    return {"id": "test-opening", "label": "Test entrance", "segment_cm": [[500, 100], [500, 900]],
            "observer_polygon_cm": [list(p) for p in SQUARE],
            "approach_polygon_cm": [list(p) for p in SQUARE], "max_range_cm": 2000,
            "source": "Synthetic geometry independent of the reference match"}


class EntranceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/"map.json"
        self.data = deepcopy(load_map_model(ASSET).data)
        self.data.update(coverage_polygon_cm=[list(p) for p in SQUARE], walking_polygon_cm=[list(p) for p in SQUARE],
                         sight_blockers=[], movement_blockers=[], landmarks=[], entrances=[entrance()])

    def report(self, origin=(100, 500, 0), yaw=0, target=(800, 900, 0), settings=None, pitch=0, rows=None):
        self.path.write_text(json.dumps(self.data), encoding="utf-8")
        model = match()
        model["map_asset"] = ASSET
        return query_spatial(model, MemoryExport(rows if rows is not None else [movement(1,1000,origin,yaw,pitch), movement(2,1000,target)]),
                             1, model["players"][0], 0, 1,
                             settings or SpatialSettings(allow_candidate_map=True), map_path=self.path)

    def candidate(self, **kwargs):
        return self.report(**kwargs)["snapshots"][0]["entrance_context"]["candidates"][0]

    def test_facing_left_and_enemy_right_are_separate_estimates(self):
        candidate = self.candidate(yaw=math.degrees(math.atan2(-250,400)))
        self.assertEqual(candidate["facing_side"], "left")
        self.assertEqual(candidate["targets"][0]["side"], "right")
        self.assertEqual(candidate["intersection_cm"], [500,250])
        self.assertIsNone(candidate["targets"][0]["actual_visibility"])
        self.assertEqual(candidate["model_validation"], "candidate_unverified")

    def test_endpoint_order_and_observer_side_determine_left_right(self):
        self.data["entrances"][0]["segment_cm"].reverse()
        self.assertEqual(self.candidate(yaw=-30)["facing_side"], "left")
        opposite = self.candidate(origin=(900,500,0), yaw=150, target=(200,100,0))
        self.assertEqual(opposite["facing_side"], "left")
        self.assertEqual(opposite["targets"][0]["side"], "right")

    def test_looking_away_parallel_out_of_range_and_outside_observer_area(self):
        for yaw in (180,90):
            with self.subTest(yaw=yaw):
                self.assertEqual(self.report(yaw=yaw)["snapshots"][0]["entrance_context"]["candidates"], [])
        self.data["entrances"][0]["max_range_cm"] = 300
        self.assertEqual(self.report()["snapshots"][0]["entrance_context"]["candidates"], [])
        self.data["entrances"][0]["observer_polygon_cm"] = [[700,100],[900,100],[900,900],[700,900]]
        self.assertEqual(self.report()["snapshots"][0]["entrance_context"]["candidates"], [])

    def test_blocker_before_entrance_does_not_become_a_clear_hold(self):
        self.data["sight_blockers"] = [{"id":"wall", "polygon_cm":[[250,200],[350,200],[350,800],[250,800]], "source":"test"}]
        candidate = self.candidate()
        self.assertEqual(candidate["status"], "blocked_before_entrance")
        self.assertEqual(candidate["path_to_entrance"]["blocker_ids"], ["wall"])

    def test_jamb_and_third_boundaries_remain_uncertain(self):
        for y, expected in ((100,"jamb_boundary_uncertain"), (100+800/3,"side_boundary_uncertain"),
                            (500,"center"), (900,"jamb_boundary_uncertain")):
            with self.subTest(y=y):
                self.assertEqual(self.candidate(yaw=math.degrees(math.atan2(y-500,400)))["facing_side"], expected)

    def test_target_outside_opening_is_not_a_visible_enemy_in_the_opening(self):
        self.data["entrances"][0]["segment_cm"] = [[500,400],[500,600]]
        target = self.candidate()["targets"][0]
        self.assertEqual(target["side"], "right_of_opening")
        self.assertFalse(target["within_opening_projection"])
        self.assertIsNone(target["actual_visibility"])
        self.assertEqual(self.candidate(target=(300,800,0))["targets"], [])

    def test_missing_facing_stale_data_vertical_view_and_candidate_gate(self):
        for kwargs, reason in (({"yaw":None},"observer_position_or_facing_unavailable"),
                               ({"pitch":90},"near_vertical_facing"),
                               ({"rows":[]},"observer_position_or_facing_unavailable"),
                               ({"settings":SpatialSettings()},"map_model_not_validated_for_build; candidate_opt_in_required")):
            with self.subTest(reason=reason):
                context = self.report(**kwargs)["snapshots"][0]["entrance_context"]
                self.assertEqual(context["reason"], reason)
                self.assertIsNone(context["player_intent"])
        self.assertEqual(self.candidate(pitch=None)["facing_side"], "center")

    def test_multiple_candidates_are_bounded_and_omissions_reported(self):
        self.data["entrances"] = [{**entrance(), "id":str(i)} for i in range(6)]
        report = self.report()
        context = report["snapshots"][0]["entrance_context"]
        self.assertEqual(len(context["candidates"]), 4)
        self.assertEqual(context["omitted"], 2)
        self.assertEqual(report["coverage"]["entrance_candidates_omitted"], 2)
        self.assertTrue(report["coverage"]["truncated"])

    def test_invalid_entrance_annotations_have_stable_errors(self):
        for update in ({"segment_cm":[[0,0],[0,0]]}, {"max_range_cm":float("nan")},
                       {"segment_cm":[[0,0,0],[500,0]]}, {"approach_polygon_cm":[]}):
            with self.subTest(update=update):
                self.data["entrances"] = [{**entrance(), **update}]
                self.path.write_text(json.dumps(self.data), encoding="utf-8")
                with self.assertRaises(QueryError) as caught:
                    load_map_model(ASSET, self.path)
                self.assertEqual(caught.exception.code, "invalid_map_model")


class FullSunsetFootprintTests(unittest.TestCase):
    def test_floor_extends_to_b_a_mid_and_both_spawns(self):
        model = load_map_model(ASSET)
        for uv in ((.06,.62),(.80,.38),(.50,.50),(.50,.95),(.48,.06)):
            with self.subTest(uv=uv):
                point = model.transform.unproject(uv)
                self.assertTrue(contains(point, model.coverage))
                self.assertFalse(any(contains(point,b["polygon"]) for b in model.sight_blockers))
        a, b = (model.transform.unproject(uv) for uv in ((.39,.22),(.62,.22)))
        self.assertEqual(model.inspect(a,b,route=False)[0]["status"], "blocked_in_annotated_2d_model")
        self.assertEqual(model.data["validated_builds"], [])

    def test_edge_prefilter_preserves_clearance_and_crossing_results(self):
        rng = random.Random(7)
        polygon = ((0,0),(1000,0),(1000,1000),(600,1000),(600,300),(400,300),(400,1000),(0,1000))
        for _ in range(100):
            a, b = [(rng.uniform(-50,1050),rng.uniform(-50,1050)) for _ in range(2)]
            original = (contains(a,polygon) and contains(b,polygon)
                        and all(not crosses(a,b,c,d) and segment_distance(a,b,c,d)>30 for c,d in edges(polygon)))
            self.assertEqual(inside_segment(a,b,polygon,30), original)
            self.assertEqual(near_boundary(a,polygon,30), edge_distance(a,polygon)<=30)


@unittest.skipUnless((PRIVATE/"manifest.json").is_file(), "Second private replay export not available")
class R4EntranceReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = Export(PRIVATE)
        cls.model, _ = load_match(cls.export)
        cls.selected = select_player(cls.model,"58")

    def test_user_reported_left_main_hold_has_opposite_raze_projection(self):
        targets = [select_player(self.model,"64"),select_player(self.model,"56")]
        result = query_spatial(self.model,self.export,4,self.selected,69232,69233,
                               SpatialSettings(allow_candidate_map=True),targets)
        context = result["snapshots"][0]["entrance_context"]
        candidate = context["candidates"][0]
        self.assertEqual(candidate["label"], "B Main")
        self.assertEqual(candidate["facing_side"], "left")
        self.assertEqual(candidate["targets"][0]["pawn"], 538)
        self.assertEqual(candidate["targets"][0]["side"], "right_of_opening")
        self.assertIsNone(context["actual_visibility"])
        self.assertEqual(candidate["model_validation"], "candidate_unverified")

    def test_compact_fight_uses_same_entrance_core_and_keeps_geometry_small(self):
        result = query_fight(self.model,self.export,4,self.selected,at_ms=72146,
                             settings=FightSettings(allow_candidate_map=True))
        anchor = next(s for s in result["snapshots"] if s["round_time_ms"] == 72146)
        self.assertEqual(anchor["entrance_context"]["candidates"][0]["facing_side"], "left")
        self.assertNotIn("coverage_polygon_cm", result["map_model"])
        self.assertLess(len(json.dumps(result,separators=(",", ":")).encode()), 22000)
