"""Focused context selection, bounded output and agreement with the query cores."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from valcoach.__main__ import main
from valcoach.fight import FightSettings, query_fight
from valcoach.normalize import load_match
from valcoach.query import DEFAULTS, query_round, select_player
from valcoach.source import Export, QueryError
from valcoach.spatial import query_spatial, SpatialSettings
from tests.test_spatial import match, movement, MemoryExport


def synthetic():
    model = match()
    model["players"].append({"id": "c", "state_id": 13, "pawn": 3, "team": 1, "agent": "Fade"})
    round_ = {**model["rounds"][0], "selected_side": "attacker"}
    death = {"id": "kill", "kind": "death", "time_ms": 2200, "round_time_ms": 1200,
             "killer": 1, "victim": 2, "weapon": "Vandal"}
    hits = [{"id": "hit1", "kind": "damage", "time_ms": 2000, "round_time_ms": 1000,
             "attacker": 1, "victim": 2, "actual_damage": 40, "raw_damage": 40, "weapon": "Vandal"},
            {"id": "hit2", "kind": "damage", "time_ms": 2200, "round_time_ms": 1200,
             "attacker": 1, "victim": 2, "actual_damage": 85, "raw_damage": 160, "weapon": "Vandal"}]
    plant = {"id": "plant", "kind": "spikePlanted", "player": 3, "time_ms": 1500, "round_time_ms": 500}
    fight = {"id": "fight1", "kind": "player_fight", "player": 1, "opponent": 2,
             "time_ms": 2000, "end_ms": 2200, "damage_ids": ["hit1", "hit2"], "death_ids": ["kill"],
             "actual_damage_dealt": 125, "actual_damage_taken": 0}
    model["timeline"] = [plant, death]
    report = {"round": round_, "settings": dict(DEFAULTS), "players": {"1": "you", "2": "enemy Sova", "3": "teammate Fade"},
              "timeline": [plant, *hits, death], "player_fights": [fight], "teammate_fights": [],
              "evidence": {e["id"]: e for e in [plant, *hits, death, fight]}}
    rows = [movement(p, t, (100*p, 100, 0), row=p*10+t) for p in (1, 2, 3) for t in (1000, 2000, 2200)]
    return model, report, MemoryExport(rows)


class FightTests(unittest.TestCase):
    def run_query(self, report=None, **kwargs):
        model, baseline, export = synthetic()
        with patch("valcoach.fight.query_round", return_value=report or baseline):
            return query_fight(model, export, 1, model["players"][0], **kwargs)

    def test_index_pagination_and_empty_page(self):
        index = self.run_query(limit=1)
        self.assertEqual(index["query"], "fight_index")
        self.assertEqual(index["fights"][0]["id"], "fight1")
        self.assertEqual(index["pagination"]["total"], 1)
        self.assertEqual(self.run_query(offset=5)["fights"], [])

    def test_context_reuses_spatial_evidence_and_round_state(self):
        result = self.run_query(at_ms=1000)
        self.assertEqual(result["fight"]["actual_damage_dealt"], 125)
        anchor = next(s for s in result["snapshots"] if s["time_ms"] == 2000)
        self.assertEqual(anchor["players"][0]["position_cm"], [100, 100, 0])
        self.assertEqual(anchor["players"][0]["source"]["table"], "movement")
        self.assertEqual(result["state_at_anchor"]["last_recorded_objective"]["id"], "plant")
        self.assertEqual(result["state_at_anchor"]["alive"], {"selected_team": 2, "opponents": 1})
        self.assertIsNone(result["coverage"]["actual_visibility"])
        self.assertLessEqual(len(result["snapshots"]), 3)

    def test_damage_precedes_linked_death_at_same_time(self):
        events = self.run_query(fight_id="fight1")["events"]
        self.assertLess(next(i for i, e in enumerate(events) if e["id"] == "hit2"),
                        next(i for i, e in enumerate(events) if e["id"] == "kill"))

    def test_teammate_damage_is_included_even_if_not_in_round_timeline(self):
        _, report, _ = synthetic()
        report["player_fights"] = []
        report["teammate_fights"] = [{"id": "teamfight", "kind": "teammate_fight", "teammate": 3, "opponent": 2,
            "time_ms": 2000, "end_ms": 2200, "damage_ids": ["hit1", "hit2"],
            "teammate_damage_dealt": 125, "teammate_damage_taken": 0,
            "selected_response": {"status": "no_recorded_damage_contribution", "actual_damage": 0,
                                  "damage_ids": [], "other_participation": "unknown"}}]
        report["timeline"] = [report["evidence"]["plant"], report["evidence"]["kill"]]
        report["evidence"]["hit1"]["attacker"] = 3
        result = self.run_query(report, fight_id="teamfight")
        self.assertTrue(any(e["id"] == "hit1" and e["attacker"] == 3 for e in result["events"]))
        self.assertEqual(result["fight"]["selected_response"]["other_participation"], "unknown")

    def test_ambiguous_and_missing_time_selection_are_explicit(self):
        _, report, _ = synthetic()
        report["teammate_fights"] = [{**report["player_fights"][0], "id": "another"}]
        for kwargs, code in [({"report": report, "at_ms": 1000}, "ambiguous_fight"),
                             ({"at_ms": 6000}, "no_fight_near_time"),
                             ({"fight_id": "missing"}, "unknown_fight")]:
            with self.subTest(code=code), self.assertRaises(QueryError) as caught:
                self.run_query(**kwargs)
            self.assertEqual(caught.exception.code, code)

    def test_nearest_timestamp_tolerance_and_round_boundaries(self):
        self.assertEqual(self.run_query(at_ms=2000)["fight"]["id"], "fight1")
        with self.assertRaises(QueryError):
            self.run_query(at_ms=-501)

    def test_evidence_lookup_returns_one_normalized_record(self):
        result = self.run_query(evidence_id="hit2")
        self.assertEqual(result["query"], "evidence")
        self.assertEqual(result["record"]["raw_damage"], 160)
        self.assertNotIn("snapshots", result)
        with self.assertRaises(QueryError) as caught:
            self.run_query(evidence_id="not-in-round")
        self.assertEqual(caught.exception.code, "unknown_evidence")

    def test_utility_evidence_can_be_retrieved_beyond_bounded_round_list(self):
        model, report, export = synthetic()
        model["utility"] = {"statuses": [{"id": "late-status", "player": 1, "start_ms": 19000,
                                         "end_ms": None, "scope_end_ms": 20000, "severity": None}]}
        with patch("valcoach.fight.query_round", return_value=report):
            result = query_fight(model, export, 1, model["players"][0], evidence_id="late-status")
            self.assertEqual(result["record"]["id"], "late-status")
            self.assertIsNone(result["record"]["severity"])
            model["utility"]["statuses"][0]["start_ms"] = 21000
            with self.assertRaises(QueryError):
                query_fight(model, export, 1, model["players"][0], evidence_id="late-status")

    def test_budgets_report_omissions(self):
        _, report, _ = synthetic()
        report["timeline"] += [{**report["evidence"]["hit1"], "id": "extra"+str(i), "time_ms": 2001+i} for i in range(40)]
        result = self.run_query(report, fight_id="fight1")
        self.assertEqual(len(result["events"]), 16)
        self.assertTrue(result["coverage"]["truncated"])
        self.assertGreater(result["coverage"]["events_total"], 16)

    def test_long_group_late_anchor_is_bounded_and_explicit(self):
        _, report, _ = synthetic()
        report["player_fights"][0]["end_ms"] = 17000
        result = self.run_query(report, at_ms=14000, settings=FightSettings(5000, 5000))
        window = result["window"]
        self.assertLessEqual(window["end_ms"]-window["start_ms"], 10000)
        self.assertTrue(window["start_ms"] <= 15000 < window["end_ms"])
        self.assertTrue(result["coverage"]["fight_outside_window"])

    def test_invalid_settings_fail_before_round_work(self):
        for kwargs in ({"limit": 0}, {"offset": -1}, {"settings": FightSettings(before_ms=-1)},
                       {"fight_id": "x", "at_ms": 1}):
            with patch("valcoach.fight.query_round") as core, self.assertRaises(QueryError):
                model, _, export = synthetic()
                query_fight(model, export, 1, model["players"][0], **kwargs)
            core.assert_not_called()

    def test_cli_compact_json_and_error_codes(self):
        model, report, export = synthetic()
        with patch("valcoach.__main__.Export", return_value=export), \
             patch("valcoach.__main__.load_match", return_value=(model, True)), \
             patch("valcoach.fight.query_round", return_value=report):
            for options, expected in [([], "fight_index"), (["--at-ms", "1000"], "fight")]:
                stdout = io.StringIO()
                with redirect_stdout(stdout):
                    code = main(["fight", "1", "--export", "unused", "--player", "11", "--json", *options])
                self.assertEqual(code, 0)
                self.assertEqual(len(stdout.getvalue().splitlines()), 1)
                self.assertEqual(json.loads(stdout.getvalue())["query"], expected)

    def test_explicit_spatial_samples_are_validated(self):
        model, _, export = synthetic()
        for times in ([1000, 999], [], [5000], [1000, 1000]):
            with self.subTest(times=times), self.assertRaises(QueryError):
                query_spatial(model, export, 1, model["players"][0], 0, 2000,
                              SpatialSettings(limit=3), sample_times_ms=times)


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path(os.environ.get("VALCOACH_TEST_EXPORT_1308", ROOT/"output/replay-5c31718d/raw"))


@unittest.skipUnless((PRIVATE/"manifest.json").is_file(), "Second private replay export not available")
class FightReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = Export(PRIVATE)
        cls.model, _ = load_match(cls.export)
        cls.selected = select_player(cls.model, "58")

    def test_r2_context_preserves_alternative_threat_and_unknown_participation(self):
        result = query_fight(self.model, self.export, 2, self.selected, at_ms=29635)
        self.assertEqual(result["fight"]["teammate"], 770)
        self.assertEqual(result["fight"]["selected_response"]["other_participation"], "unknown")
        anchor = next(s for s in result["snapshots"] if s["round_time_ms"] == 29635)
        raze = next(r for r in anchor["relations_from_selected"] if r["to"] == 538)
        self.assertAlmostEqual(raze["horizontal_offset_deg"], -2.606, places=3)
        self.assertEqual(result["state_at_anchor"]["last_recorded_objective"]["kind"], "spikePlanted")
        self.assertEqual(sum(e.get("actual_damage", 0) for e in result["events"] if e.get("attacker") == 770), 35)
        self.assertIsNone(result["coverage"]["actual_visibility"])

    def test_r9_success_and_payload_reduction_match_round_core(self):
        result = query_fight(self.model, self.export, 9, self.selected, at_ms=2500)
        full = query_round(self.model, self.export, 9, self.selected)
        self.assertEqual(result["fight"]["actual_damage_dealt"], 125)
        self.assertEqual(result["fight"]["actual_damage_taken"], 0)
        encoded = json.dumps(result, separators=(",", ":"))
        self.assertLess(len(encoded.encode()), 20000)
        self.assertLess(len(encoded), len(json.dumps(full, separators=(",", ":")))*0.3)
        for event in result["events"]:
            resolved = query_fight(self.model, self.export, 9, self.selected, evidence_id=event["id"])
            self.assertEqual(resolved["record"]["id"], event["id"])

    def test_query_does_not_mutate_cached_facts_and_includes_utility(self):
        before = deepcopy(self.model)
        result = query_fight(self.model, self.export, 3, self.selected, at_ms=25112)
        self.assertEqual(self.model, before)
        self.assertIn(result["utility"]["status"], ("partial_supported_observations", "not_loaded"))
        for sample in result["snapshots"]:
            self.assertTrue(all(p["sample_time_ms"] is None or p["sample_time_ms"] <= sample["time_ms"] for p in sample["players"]))
