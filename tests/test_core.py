"""Small adversarial cases for reconstruction and interpretation boundaries."""

import json
from pathlib import Path
import tempfile
import unittest

from valcoach.normalize import reconstruct_casts, reconstruct_damage
from valcoach.query import (DEFAULTS, Positions, alive, damage_total, group_fights,
                            select_player, teammate_fights, trade_outcomes)
from valcoach.source import Export, QueryError, packed_guid


def field(packet, name, val, row=0, actor=1, obj=10):
    return {"packet_id": packet, "time_ms": packet*100, "actor_net_guid": actor,
            "object_net_guid": obj, "field_name": name, "source_row": row,
            "value_i64": val if isinstance(val, int) and not isinstance(val, bool) else None,
            "value_f64": val if isinstance(val, float) else None,
            "value_bool": val if isinstance(val, bool) else None,
            "value_str": val if isinstance(val, str) else None}


def death(time, killer, victim, id_):
    return {"id": id_, "kind": "death", "time_ms": time, "killer": killer,
            "victim": victim, "damage_id": "damage:"+id_}


def hit(time, attacker, victim, amount=10, lethal=False):
    return {"id": f"damage:{time}:{attacker}:{victim}", "combat_time_ms": time,
            "time_ms": time, "attacker": attacker, "victim": victim,
            "actual_damage": amount, "lethal": lethal}


def model(events=None):
    return {"timeline": events or [], "players": [
        {"id": "a", "state_id": 11, "pawn": 1, "team": 100, "agent": "Jett"},
        {"id": "b", "state_id": 12, "pawn": 2, "team": 100, "agent": "Raze"},
        {"id": "c", "state_id": 13, "pawn": 3, "team": 200, "agent": "Jett"}]}


ROUND = {"number": 1, "live_ms": 0, "end_ms": 100000, "result_time_ms": 90000}


class ReconstructionTests(unittest.TestCase):
    def test_build_allowlist_remains_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for table in Export.TABLES:
                (root / f"{table}.parquet").touch()
            for build in ("13.06", "13.08"):
                (root / "manifest.json").write_text(json.dumps({"replay_build": "++Ares-Core+release-" + build}))
                self.assertTrue(Export(root).fingerprint)
            (root / "manifest.json").write_text(json.dumps({"replay_build": "++Ares-Core+release-99.00"}))
            with self.assertRaises(QueryError) as caught:
                Export(root)
            self.assertEqual(caught.exception.code, "unsupported_build")

    def test_packed_guid_and_incomplete_payload(self):
        self.assertEqual(packed_guid(bytes.fromhex("fd02")), 254)
        self.assertEqual(packed_guid(bytes.fromhex("2104")), 272)
        self.assertIsNone(packed_guid(b"\xff"))

    def test_cast_array_reorder_partial_deltas_and_effect_only_updates(self):
        rows = []
        for packet, index, values in [
            (1, 0, {"Player": "a", "Slot": 5, "Round": 0, "RoundPhase": 4, "CastTime": 4.854}),
            (2, 0, {"Slot": 3, "CastTime": 7.374}),
            (2, 1, {"Player": "a", "Slot": 5, "Round": 0, "RoundPhase": 4, "CastTime": 4.854}),
            (4, 0, {"Round": 1, "Slot": 5, "CastTime": 4.854}),
        ]:
            for member, val in values.items():
                rows.append(field(packet, f"AbilityCastsThisRound[{index}].{member}_123_GUID", val, len(rows)))
        rows.insert(-3, field(3, "AbilityCastsThisRound[0].Effects[0].Value_123_GUID", 99, 100))
        casts = reconstruct_casts(rows)
        self.assertEqual(len(casts), 3)
        self.assertEqual({(c["Slot"], c["Round"], c["CastTime"]) for c in casts},
                         {(5, 0, 4.854), (3, 0, 7.374), (5, 1, 4.854)})
        self.assertFalse(any("Effects" in c for c in casts))

    def test_two_damage_invocations_in_one_packet_preserve_actual_vs_raw(self):
        rows = []
        for amount, raw in [(39.0, 39.0), (6.0, 26.0)]:
            for name, val in [("DamageTaken", amount), ("DamageDealt", raw),
                              ("EventInstigatorPawn", 3), ("EquippableUsed", 7),
                              ("bDamageKilledTarget", amount == 6.0)]:
                rows.append(field(1, "MulticastNotifyDamage_Point."+name, val, len(rows)))
        result = reconstruct_damage(rows, {7: "CompactPistol.CompactPistol_C"}, {})
        self.assertEqual([(d["actual_damage"], d["raw_damage"]) for d in result], [(39.0,39.0),(6.0,26.0)])
        self.assertTrue(result[1]["lethal"])
        self.assertNotEqual(result[0]["id"], result[1]["id"])

    def test_unknown_damage_does_not_become_zero(self):
        self.assertIsNone(damage_total([hit(1, 1, 3, None)]))
        self.assertEqual(damage_total([]), 0)


class ContextTests(unittest.TestCase):
    def test_actual_trade_and_configurable_window(self):
        m = model([death(1000, 3, 2, "first"), death(4000, 1, 3, "response")])
        trades = trade_outcomes(m, ROUND, DEFAULTS)
        self.assertEqual((trades[0]["completed"],trades[0]["responder"],trades[0]["delay_ms"]), (True,1,3000))
        self.assertFalse(trade_outcomes(m, ROUND, {**DEFAULTS,"trade_window_ms":2999})[0]["completed"])

    def test_resurrection_interrupts_trade_and_resets_life(self):
        events = [death(1000,3,2,"first"), {"id":"res","kind":"resurrection","time_ms":2000,"player":2},
                  death(3000,1,3,"response")]
        m = model(events)
        outcome = trade_outcomes(m, ROUND, DEFAULTS)[0]
        self.assertFalse(outcome["completed"])
        self.assertEqual(outcome["stopped_by_resurrection"],"res")
        self.assertFalse(alive(m, ROUND, 2,1500))
        self.assertTrue(alive(m, ROUND,2,2000))

    def test_trade_cannot_cross_round_result(self):
        m = model([death(1000,3,2,"first"),death(3000,1,3,"response")])
        self.assertFalse(trade_outcomes(m,{**ROUND,"result_time_ms":2000},DEFAULTS)[0]["completed"])

    def test_teammate_survives_and_nonlethal_response_counts(self):
        m = model()
        hits = [hit(1000,3,2), hit(1100,2,3),hit(1200,1,3,39)]
        fights = teammate_fights(m,ROUND,m["players"][0],Positions([],250),DEFAULTS,hits,[])
        self.assertEqual(len(fights),1)
        self.assertFalse(fights[0]["teammate_died_in_observed_exchange"])
        self.assertEqual(fights[0]["selected_response"]["actual_damage"],39)
        self.assertIsNone(fights[0]["trade"])

    def test_no_damage_is_unknown_other_participation(self):
        m = model()
        fights = teammate_fights(m,ROUND,m["players"][0],Positions([],250),DEFAULTS,[hit(1000,3,2)],[])
        self.assertEqual(fights[0]["selected_response"]["status"],"no_recorded_damage_contribution")
        self.assertEqual(fights[0]["selected_response"]["other_participation"],"unknown")
        self.assertIsNone(fights[0]["selected_distance_at_start"]["distance_m"])

    def test_player_dead_clips_fight_and_later_damage(self):
        m = model([death(1500,3,1,"selected")])
        hits = [hit(1000,3,2),hit(1600,2,3),hit(1700,1,3)]
        fights = teammate_fights(m,ROUND,m["players"][0],Positions([],250),DEFAULTS,hits,[])
        self.assertEqual(fights[0]["end_ms"],1000)
        self.assertEqual(fights[0]["selected_response"]["damage_ids"],[])

    def test_fight_gap_and_delayed_packet_near_death(self):
        m = model([death(1000,1,3,"opponent")])
        hits = [hit(900,2,3),hit(1001,2,3),hit(5000,3,2)]
        groups = group_fights(hits,m,ROUND,DEFAULTS)
        self.assertEqual([len(g) for g in groups],[2,1])

    def test_freshness_and_no_future_sample_leak(self):
        rows = [{"character_net_guid":p,"time_ms":1000,"source_row":p,"packet_id":1,
                 "pos_x":x,"pos_y":0,"pos_z":0} for p,x in [(1,0),(2,1000)]]
        positions = Positions(rows,250)
        self.assertIsNone(positions.at(1,999))
        self.assertEqual(positions.distance(1,2,1250)["distance_m"],10)
        self.assertIsNone(positions.distance(1,2,1251)["distance_m"])

    def test_duplicate_agent_requires_explicit_identity(self):
        with self.assertRaises(QueryError) as caught:
            select_player(model(),"Jett")
        self.assertEqual(caught.exception.code,"ambiguous_player")
        self.assertEqual(select_player(model(),"11")["pawn"],1)


if __name__ == "__main__":
    unittest.main()
