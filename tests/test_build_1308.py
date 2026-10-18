"""Compatibility cases for the second private replay, not a blanket build audit."""

import json
import os
from pathlib import Path
import unittest

from valcoach.normalize import load_match
from valcoach.query import query_round, select_player
from valcoach.render import render_round
from valcoach.source import Export

ROOT = Path(__file__).resolve().parents[1]
EXPORT = Path(os.environ.get("VALCOACH_TEST_EXPORT_1308", ROOT / "output/replay-5c31718d/raw"))
EXPECTED = json.loads((ROOT / "tests/fixtures/reference-13.08.json").read_text())


@unittest.skipUnless((EXPORT / "manifest.json").is_file(), "Second private replay export not available")
class Build1308Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = Export(EXPORT)
        cls.model, _ = load_match(cls.source)
        cls.player = select_player(cls.model, EXPECTED["selected_subject"])
        cls.reports = {n: query_round(cls.model, cls.source, n, cls.player) for n in (1,2,6,9,11,14,17,20,22,24)}

    def test_identity_is_rediscovered_not_copied_from_first_replay(self):
        self.assertEqual(self.player["pawn"], EXPECTED["selected_pawn"])
        self.assertEqual(self.player["state_id"], EXPECTED["selected_state"])
        self.assertEqual(self.player["agent"], "Jett")
        iris = next(p for p in self.model["players"] if p["agent_internal"] == "Iris")
        self.assertEqual(iris["agent"], EXPECTED["user_confirmed_agent"]["display"])
        self.assertIn("/Characters/Iris/", iris["agent_class"])
        self.assertEqual(self.reports[1]["players"][str(iris["pawn"])], "teammate Miks")

    def test_user_confirmed_miks_ghost_kill_preserves_source_identity(self):
        expected = EXPECTED["round_one_miks_kill"]
        event = next(e for e in self.reports[1]["timeline"] if e["kind"] == "death"
                     and e["killer"] == EXPECTED["user_confirmed_agent"]["pawn"]
                     and e["victim"] == expected["victim"])
        self.assertEqual(event["weapon"], expected["weapon"])
        self.assertEqual(event["round_time_ms"], expected["round_time_ms"])
        self.assertEqual(event["source"]["table"], "events")
        rendered = render_round(self.reports[1])
        self.assertIn("teammate Miks kills enemy Clove — Ghost", rendered)
        self.assertNotIn("agent Iris", rendered)

    def test_all_deaths_reconcile_and_self_death_is_not_opponent_kill(self):
        deaths = [e for e in self.model["timeline"] if e["kind"] == "death"]
        pawn = self.player["pawn"]
        self.assertEqual(len(deaths), EXPECTED["total_death_events"])
        self.assertTrue(all(e["damage_id"] for e in deaths))
        self.assertEqual(sum(e["killer"] == pawn and e["victim"] != pawn for e in deaths), EXPECTED["selected_opponent_kills"])
        self.assertEqual(sum(e["victim"] == pawn for e in deaths), EXPECTED["selected_death_events"])

    def test_user_confirmed_surviving_teammate_fight_is_retained(self):
        expected = EXPECTED["confirmed_surviving_fight"]
        report = self.reports[expected["round"]]
        fight = next(f for f in report["teammate_fights"] if f["teammate"] == expected["teammate"]
                     and f["opponent"] == expected["opponent"] and f["round_time_ms"] == expected["start_ms"])
        self.assertEqual(fight["end_ms"] - report["round"]["live_ms"], expected["end_ms"])
        self.assertFalse(fight["teammate_died_in_observed_exchange"])
        self.assertEqual(fight["teammate_damage_dealt"], expected["dealt"])
        self.assertEqual(fight["teammate_damage_taken"], expected["taken"])
        self.assertEqual(fight["selected_response"]["status"], "no_recorded_damage_contribution")
        self.assertEqual(fight["selected_response"]["other_participation"], "unknown")
        kill = next(e for e in report["timeline"] if e["kind"] == "death"
                    and e["killer"] == expected["teammate"] and e["victim"] == expected["opponent"])
        self.assertEqual(kill["weapon"], expected["weapon"])

    def test_user_confirmed_nonlethal_contribution_is_independent_of_kill_credit(self):
        expected = EXPECTED["confirmed_nonlethal_contribution"]
        report = self.reports[expected["round"]]
        fight = next(f for f in report["teammate_fights"] if f["teammate"] == expected["teammate"]
                     and f["opponent"] == expected["opponent"] and f["selected_response"]["actual_damage"] == 100)
        hits = [report["evidence"][ref] for ref in fight["selected_response"]["damage_ids"]]
        self.assertEqual([h["actual_damage"] for h in hits], expected["selected_damage"])
        self.assertTrue(all(h["weapon"] == expected["selected_weapon"] and not h["lethal"] for h in hits))
        kill = next(e for e in report["timeline"] if e["kind"] == "death" and e["victim"] == expected["opponent"])
        self.assertEqual(kill["killer"], expected["teammate"])
        self.assertEqual(kill["weapon"], expected["finishing_weapon"])
        self.assertEqual(report["evidence"][kill["damage_id"]]["actual_damage"], expected["finishing_damage"])

    def test_user_confirmed_completed_trade_links_both_deaths(self):
        expected = EXPECTED["confirmed_trade"]
        report = self.reports[expected["round"]]
        trade = next(t for t in report["trades"] if t["victim"] == expected["victim"])
        self.assertTrue(trade["completed"])
        for key in ("opponent", "responder", "delay_ms"):
            self.assertEqual(trade[key], expected[key])
        response = report["evidence"][trade["response_death_id"]]
        self.assertEqual(response["weapon"], expected["response_weapon"])
        self.assertEqual(response["victim"], expected["opponent"])

    def test_round_boundaries_and_halftime_sides(self):
        self.assertEqual(len(self.model["rounds"]), EXPECTED["rounds"])
        self.assertEqual(self.reports[1]["round"]["live_ms"], EXPECTED["round_one_live_ms"])
        self.assertEqual(self.reports[1]["round"]["selected_side"], "defender")
        self.assertEqual(self.reports[24]["round"]["selected_side"], "attacker")
        self.assertTrue(all(r["buy_ms"] < r["live_ms"] < r["end_ms"] for r in self.model["rounds"]))
        self.assertTrue(all(len(r["result"]) == 4 for r in self.model["rounds"]))

    def test_first_round_preparation_is_distinct_from_actual_dash(self):
        abilities = [e for e in self.reports[1]["timeline"] if e["kind"] == "ability"]
        preparations = [e["round_time_ms"] for e in abilities if e["stage"] == "preparation_record"]
        dashes = [e for e in abilities if e["stage"] == "activation_corroborated"]
        self.assertEqual(preparations, [EXPECTED["round_one_prepare_ms"]])
        self.assertEqual(len(dashes), 1)
        self.assertEqual(dashes[0]["round_time_ms"], EXPECTED["round_one_dash_equip_ms"])
        self.assertTrue(dashes[0]["movement_sources"])

    def test_four_clove_resurrections_keep_alive_counts_valid(self):
        for number in (6,11,17,22):
            report = self.reports[number]
            revives = [e for e in report["timeline"] if e["kind"] == "resurrection"]
            self.assertEqual(len(revives), 1)
            self.assertEqual(revives[0]["player"], 866)
            self.assertTrue(all(0 <= e["alive"][side] <= 5 for e in report["timeline"] for side in ("selected_team","opponents")))

    def test_post_round_self_death_is_labeled_without_fake_trade(self):
        report = self.reports[20]
        event = next(e for e in report["timeline"] if e["kind"] == "death" and e["killer"] == e["victim"] == self.player["pawn"])
        self.assertTrue(event["post_round"])
        self.assertEqual(event["time_ms"], EXPECTED["self_attributed_post_round_death_ms"])
        self.assertFalse(any(t["death_id"] == event["id"] for t in report["trades"]))
        text = render_round(report)
        self.assertIn("self-attributed death", text)
        self.assertNotIn("you kill you", text)


if __name__ == "__main__":
    unittest.main()
