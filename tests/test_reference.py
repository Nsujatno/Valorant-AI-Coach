"""Integration checks use the local private export; it is never bundled as a public fixture."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from valcoach.normalize import load_match
from valcoach.query import query_round, select_player
from valcoach.source import Export

ROOT = Path(__file__).resolve().parents[1]
EXPORT = Path(os.environ.get("VALCOACH_TEST_EXPORT", ROOT / "output/first-replay/raw"))
EXPECTED = json.loads((ROOT / "tests/fixtures/reference-match.json").read_text())


@unittest.skipUnless((EXPORT / "manifest.json").is_file(), "Local private replay export not available")
class ReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = Export(EXPORT)
        cls.model, _ = load_match(cls.export)
        cls.player = select_player(cls.model, EXPECTED["selected_subject"])
        cls.results = {n: query_round(cls.model, cls.export, n, cls.player) for n in (1,2,3,5,12,18,20)}

    def test_whole_match_death_reconciliation(self):
        deaths = [e for e in self.model["timeline"] if e["kind"] == "death"]
        self.assertEqual(len(self.model["rounds"]), EXPECTED["rounds"])
        self.assertEqual(len(deaths),EXPECTED["total_deaths"])
        self.assertTrue(all(e["damage_id"] for e in deaths))
        self.assertEqual(sum(e["killer"]==864 for e in deaths),EXPECTED["player_kills"])
        self.assertEqual(sum(e["victim"]==864 for e in deaths),EXPECTED["player_deaths"])

    def test_round_one_exact_deaths_and_alive_counts(self):
        q=self.results[1]
        deaths=[e for e in q["timeline"] if e["kind"]=="death"]
        self.assertEqual(q["round"]["live_ms"],EXPECTED["round_one"]["live_ms"])
        self.assertEqual([[e["time_ms"],e["killer"],e["victim"]] for e in deaths],EXPECTED["round_one"]["deaths"])
        self.assertEqual(deaths[-1]["alive"],{"selected_team":0,"opponents":3})
        self.assertEqual([e["time_ms"] for e in q["timeline"]],sorted(e["time_ms"] for e in q["timeline"]))

    def test_round_one_damage_and_weapon_attribution(self):
        damage=[e for e in self.results[1]["timeline"] if e["kind"]=="damage"]
        outgoing=[e for e in damage if e["attacker"]==864]
        incoming=[e for e in damage if e["victim"]==864]
        self.assertEqual(len(outgoing),7)
        self.assertTrue(all(e["weapon"]=="Bandit" for e in outgoing))
        self.assertAlmostEqual(sum(e["actual_damage"] for e in outgoing),278,places=3)
        self.assertAlmostEqual(sum(e["raw_damage"] for e in outgoing),307.3996,places=3)
        self.assertEqual([e["actual_damage"] for e in incoming],[39,55,6])
        self.assertTrue(all("observed_time_ms" in e for e in damage))

    def test_round_one_cast_deduplication_and_dash_corroboration(self):
        abilities=[e for e in self.results[1]["timeline"] if e["kind"]=="ability"]
        self.assertEqual([e["round_time_ms"] for e in abilities if e["stage"]=="preparation_record"], [4854,26480])
        dashes=[e for e in abilities if e["stage"]=="activation_corroborated"]
        self.assertEqual(len(dashes),1)
        self.assertEqual(dashes[0]["round_time_ms"],8017)
        self.assertEqual(dashes[0]["movement_start_ms"]-45063,8027)
        self.assertEqual([e["round_time_ms"] for e in abilities if e["ability"]=="Cloudburst"],[7374])

    def test_item_pickup_after_death_keeps_new_owner(self):
        q=self.results[1]
        pickup=next(e for e in q["timeline"] if e["kind"]=="ownership" and e["item"]==3966 and e["owner"]==962)
        self.assertEqual(pickup["round_time_ms"],28721)
        self.assertEqual(pickup["alive"]["selected_team"],0)

    def test_completed_trade_example(self):
        expected=EXPECTED["completed_trade"]
        trade=next(t for t in self.results[2]["trades"] if t["victim"]==expected["victim"])
        for key in ("opponent","responder","delay_ms"):
            self.assertEqual(trade[key],expected[key])
        self.assertTrue(trade["completed"])

    def test_surviving_teammate_example(self):
        expected=EXPECTED["surviving_fight"]
        fight=next(f for f in self.results[3]["teammate_fights"] if f["teammate"]==expected["teammate"] and
                   f["opponent"]==expected["opponent"] and f["round_time_ms"]==expected["start_ms"])
        self.assertFalse(fight["teammate_died_in_observed_exchange"])
        self.assertEqual(fight["selected_response"]["other_participation"],"unknown")

    def test_nonlethal_contribution_example(self):
        expected=EXPECTED["nonlethal_contribution"]
        q=self.results[20]
        fight=next(f for f in q["teammate_fights"] if f["teammate"]==expected["teammate"] and
                   f["opponent"]==expected["opponent"] and f["round_time_ms"]==expected["start_ms"])
        self.assertAlmostEqual(fight["selected_response"]["actual_damage"],69.1092,places=3)
        self.assertTrue(all(not q["evidence"][id_]["lethal"] for id_ in fight["selected_response"]["damage_ids"]))

    def test_resurrection_rounds_keep_valid_counts(self):
        for n in (5,12,18):
            q=self.results[n]
            resurrected=[e for e in q["timeline"] if e["kind"]=="resurrection"]
            self.assertEqual(len(resurrected),1)
            self.assertEqual(resurrected[0]["player"],962)
            self.assertTrue(all(0<=e["alive"][side]<=5 for e in q["timeline"] for side in ("selected_team","opponents")))

    def test_all_evidence_links_and_no_cached_model_mutation(self):
        for q in self.results.values():
            for fight in q["teammate_fights"]:
                for ref in fight["damage_ids"]+fight["selected_response"]["damage_ids"]:
                    self.assertIn(ref,q["evidence"])
            json.dumps(q,allow_nan=False)
        self.assertTrue(all("round_time_ms" not in e for e in self.model["timeline"]))
        self.assertTrue(all("round_time_ms" not in d for d in self.model["damage"]))

    def test_cli_unknown_round_and_ambiguous_player_emit_json_errors(self):
        for tail,code in [(["999","--player","254"],"unknown_round"),(["1","--player","Jett"],"ambiguous_player")]:
            p=subprocess.run([sys.executable,"-m","valcoach","round",*tail,"--export",str(EXPORT),"--json"],
                             cwd=ROOT,capture_output=True,text=True)
            self.assertEqual(p.returncode,1)
            self.assertEqual(json.loads(p.stdout)["error"]["code"],code)

    def test_normalization_cache_reused(self):
        cached,reused=load_match(self.export)
        self.assertTrue(reused)
        self.assertEqual(cached["revision"],self.model["revision"])

    def test_cli_preserves_existing_output_and_rejects_cache_in_raw(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"existing.md"
            path.write_text("keep my annotations",encoding="utf-8")
            for tail,code in [(["--output",str(path)],"output_exists"),
                              (["--cache-dir",str(EXPORT/"must-not-be-created")],"invalid_cache"),
                              (["--engagement-gap-ms","-1"],"invalid_settings")]:
                p=subprocess.run([sys.executable,"-m","valcoach","round","1","--player","254",
                                  "--export",str(EXPORT),"--json",*tail],cwd=ROOT,capture_output=True,text=True)
                self.assertEqual(p.returncode,1)
                self.assertEqual(json.loads(p.stdout)["error"]["code"],code)
            self.assertEqual(path.read_text(encoding="utf-8"),"keep my annotations")
            self.assertFalse((EXPORT/"must-not-be-created").exists())


if __name__ == "__main__":
    unittest.main()
