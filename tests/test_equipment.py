"""Observed equip changes must not become invented ammo or ultimate readiness."""

import os
from pathlib import Path
import unittest

from valcoach.equipment import latest_equipment
from valcoach.fight import query_fight
from valcoach.normalize import load_match
from valcoach.query import select_player
from valcoach.source import Export

ROUND = {"buy_ms":500, "live_ms":1000, "end_ms":10000}


def equipped(time, item, weapon=None, row=1):
    return {"id":f"eq:{row}", "kind":"equipped", "time_ms":time, "player":1, "item":item,
            "weapon":weapon, "item_class":f"unknown-class:{item}",
            "source":{"table":"fields", "row":row}}


class EquipmentTests(unittest.TestCase):
    def state(self, observations, moment=5000, timeline=None):
        return latest_equipment({"equipment":observations, "timeline":timeline or []},ROUND,1,moment)

    def test_earlier_blade_storm_does_not_override_later_weapon_or_invent_counts(self):
        observations = [equipped(1000,10,"Blade Storm"), equipped(4000,20,"Vandal",2), equipped(6000,30,"Spectre",3)]
        result = self.state(observations)
        self.assertEqual(result["observations"][0]["weapon"], "Vandal")
        self.assertEqual(result["observed_time_ms"], 4000)
        self.assertIsNone(result["ammunition_remaining"])
        self.assertIsNone(result["ability_charges_remaining"])
        self.assertEqual(result["observations"][0]["source"]["row"], 2)

    def test_cleared_equipment_does_not_fall_back_to_older_gun(self):
        result = self.state([equipped(1000,10,"Vandal"),equipped(4000,0,row=2)])
        self.assertEqual(result["status"], "no_item_observed")
        self.assertIsNone(result["observations"][0]["weapon"])

    def test_same_time_conflicting_fields_remain_ambiguous(self):
        result = self.state([equipped(4000,10,"Spectre"),equipped(4000,0,row=2)])
        self.assertEqual(result["status"], "conflicting_same_time_observations")
        self.assertEqual({r["item"] for r in result["observations"]}, {0,10})

    def test_missing_and_prior_round_observations_remain_unknown(self):
        for records in ([],[equipped(100,10,"Blade Storm")]):
            result = self.state(records)
            self.assertEqual(result["status"], "unknown")
            self.assertEqual(result["observations"], [])

    def test_death_and_revive_require_a_new_life_observation(self):
        timeline = [{"kind":"death", "victim":1, "time_ms":3000},
                    {"kind":"resurrection", "player":1, "time_ms":4000}]
        old = [equipped(2000,10,"Blade Storm")]
        self.assertEqual(self.state(old,3500,timeline)["reason"], "player_dead")
        self.assertEqual(self.state(old,timeline=timeline)["status"], "unknown")
        self.assertEqual(self.state(old+[equipped(4500,20,"Vandal",2)],timeline=timeline)["observations"][0]["weapon"], "Vandal")

    def test_temporary_ability_identity_is_preserved_without_a_gun_guess(self):
        records = [equipped(1000,10,"Vandal"), equipped(4000,20,row=2)]
        result = self.state(records)
        self.assertEqual(result["status"], "equipped_observation")
        self.assertEqual(result["observations"][0]["item_class"], "unknown-class:20")
        self.assertIsNone(result["observations"][0]["weapon"])

    def test_same_time_observation_cap_reports_omissions(self):
        result = self.state([equipped(4000,i,row=i) for i in range(10)])
        self.assertEqual(len(result["observations"]), 8)
        self.assertEqual(result["omitted"], 2)
        self.assertEqual(result["status"], "conflicting_same_time_observations")


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path(os.environ.get("VALCOACH_TEST_EXPORT_1308",ROOT/"output/replay-5c31718d/raw"))


@unittest.skipUnless((PRIVATE/"manifest.json").is_file(), "Second private replay export not available")
class R24EquipmentReferenceTests(unittest.TestCase):
    def test_spectre_at_contact_and_equipment_evidence_are_exposed(self):
        export = Export(PRIVATE)
        model, _ = load_match(export)
        selected = select_player(model,"58")
        result = query_fight(model,export,24,selected,at_ms=64639)
        equipment = result["state_at_anchor"]["equipment"]
        self.assertEqual(equipment["status"], "equipped_observation")
        self.assertEqual(equipment["observations"][0]["weapon"], "Spectre")
        self.assertIsNone(equipment["ability_charges_remaining"])
        self.assertTrue(any(e["kind"] == "equipped" and e.get("weapon") == "Spectre" for e in result["events"]))
        observation = equipment["observations"][0]
        detail = query_fight(model,export,24,selected,evidence_id=observation["id"])
        self.assertEqual(detail["record"]["source"], observation["source"])
        self.assertEqual(detail["record"]["item"], 64906)
