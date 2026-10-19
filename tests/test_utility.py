"""Utility reconstruction boundaries: observations must not become certainty."""

from copy import deepcopy
from pathlib import Path
import json
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from valcoach.effects import (effect_calls, reconstruct_utility, smoke_instances, status_intervals)
from valcoach.source import Export, QueryError
from valcoach.utility import (interval_end, player_status_at, smoke_relation,
                              smoke_snapshot, utility_window, validate_smoke_radius)

EFFECT = "FXC_Phoenix_E_Blinded_Production_C"
GROUP = "/Script/ShooterGame.EffectManagerComponent_ClassNetCache"
SMOKE = "/Game/Characters/Wushu/S0/Ability_4/GameObject_Wushu_4_SmokeZone.GameObject_Wushu_4_SmokeZone_C"
ROUND = {"number": 1, "buy_ms": 0, "live_ms": 100, "end_ms": 5000}


def fields(function, time, packet, row, actor=1, component=10, effect_id=4, source="server", container=100):
    values = {"EffectID": effect_id, "SourceID": source, "bLocalEffect": False, "bTransient": False}
    if function == "MulticastPlayContinuousEffect":
        values = {"EffectContainer": container, **values}
    return [{"time_ms": time, "packet_id": packet, "actor_net_guid": actor,
             "object_net_guid": component, "source_row": row + i, "group_path": GROUP,
             "field_name": function + "." + name, "raw_bits": None,
             "value_i64": v if type(v) is int else None, "value_f64": None,
             "value_bool": v if type(v) is bool else None, "value_str": v if type(v) is str else None}
            for i, (name, v) in enumerate(values.items())]


def start(time=1000, row=0, **kwargs):
    return fields("MulticastPlayContinuousEffect", time, 42, row, **kwargs)


def stop(time=2000, row=10, **kwargs):
    return fields("MulticastStopContinuousEffect", time, 43, row, **kwargs)


def actor(event, time, row, guid=20, cls=SMOKE):
    return {"event": event, "time_ms": time, "source_row": row, "actor_net_guid": guid,
            "class_path": cls, "packet_id": 7, "spawn_x": 500, "spawn_y": 200, "spawn_z": 300}


class MemoryExport:
    def __init__(self, rows):
        self.rows = rows

    def utility_fields(self, pawns, smokes):
        return self.rows


class EffectTests(unittest.TestCase):
    def reconstruct(self, rows, boundaries=None):
        return status_intervals(effect_calls(rows), {100: EFFECT}, boundaries or {})

    def test_start_stop_pair_with_provenance(self):
        records, coverage = self.reconstruct(start() + stop())
        record = records[0]
        self.assertEqual((record["start_ms"], record["end_ms"]), (1000, 2000))
        self.assertEqual(record["start_sources"][0]["row"], 0)
        self.assertEqual(record["end_sources"][0]["row"], 10)
        self.assertIsNone(record["severity"])
        self.assertEqual(coverage["unpaired_supported_starts"], 0)

    def test_repeated_calls_in_same_packet_are_distinct(self):
        rows = start(effect_id=4) + start(row=5, effect_id=5) + stop(effect_id=4) + stop(row=14, effect_id=5)
        records, _ = self.reconstruct(rows)
        self.assertEqual(len(records), 2)
        self.assertEqual([r["end_ms"] for r in records], [2000, 2000])

    def test_component_source_player_and_flags_scope_stop_matching(self):
        variants = [{"component": 11}, {"source": "other"}, {"actor": 2}]
        for kwargs in variants:
            with self.subTest(kwargs=kwargs):
                records, _ = self.reconstruct(start() + stop(**kwargs))
                self.assertIsNone(records[0]["end_ms"])
        rows = stop()
        rows[-1]["value_bool"] = True
        self.assertIsNone(self.reconstruct(start() + rows)[0][0]["end_ms"])

    def test_missing_stops_do_not_invent_duration_or_cross_life_boundary(self):
        records, _ = self.reconstruct(start(), {1: [1500]})
        model = {"utility": {"statuses": records}}
        self.assertEqual(player_status_at(model, 1, 1400)["effects"][0]["interval_evidence"], "possibly_active_end_unobserved")
        self.assertEqual(player_status_at(model, 1, 1500)["effects"], [])
        self.assertIsNone(records[0]["end_ms"])

    def test_reused_effect_id_closes_support_scope_without_fake_stop(self):
        rows = start() + start(time=1500, row=5, container=999) + stop()
        records, _ = self.reconstruct(rows)
        self.assertEqual(interval_end(records[0]), 1500)
        self.assertIsNone(records[0]["end_ms"])
        records, _ = self.reconstruct(rows, {1: [1200]})
        self.assertEqual(interval_end(records[0]), 1200)

    def test_equip_windup_and_unknown_containers_are_not_status_applications(self):
        calls = effect_calls(start())
        for path in ("FXC_Vampire_4_NearsightAOE_Equip_C", "FXC_Iris_Thumper_Windup_Concuss_C", "unknown"):
            records, coverage = status_intervals(calls, {100: path}, {})
            self.assertEqual(records, [])
            self.assertEqual(coverage["unmapped_effect_starts"], 1)

    def test_incomplete_pair_key_stays_unpaired(self):
        rows = start()
        rows = [r for r in rows if not r["field_name"].endswith("SourceID")]
        records, coverage = self.reconstruct(rows + stop())
        self.assertIsNone(records[0]["end_ms"])
        self.assertEqual(coverage["incomplete_effect_keys"], 1)

    def test_raw_blind_update_is_not_a_flash_hit_or_duration(self):
        row = {**start()[0], "field_name": "ActiveBlinds", "raw_bits": b"\x01\x02"}
        result = reconstruct_utility(MemoryExport([row]), [], [{"pawn": 1}], [ROUND], [], {})
        self.assertEqual(result["statuses"], [])
        self.assertEqual(result["blind_updates"][0]["status"], "undecoded_blind_array_update")
        self.assertIsNone(result["blind_updates"][0]["duration_ms"])
        json.dumps(result, allow_nan=False)


class SmokeTests(unittest.TestCase):
    def test_dormancy_does_not_end_smoke_and_projectile_is_excluded(self):
        records = smoke_instances([actor("open", 1000, 0), actor("dormant", 1100, 1), actor("close", 3000, 2),
                                   actor("open", 1200, 3, guid=22, cls="Projectile_Wushu_4_Smoke_C")])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["end_ms"], 3000)
        self.assertIsNone(records[0]["opaque_start_ms"])
        self.assertIsNone(records[0]["radius_cm"])

    def test_actor_guid_reuse_bounds_unclosed_instance(self):
        records = smoke_instances([actor("open", 1000, 0), actor("open", 2000, 1), actor("close", 3000, 2)])
        self.assertEqual(interval_end(records[0]), 2000)
        self.assertIsNone(records[0]["end_ms"])
        self.assertEqual(records[1]["end_ms"], 3000)

    def test_smoke_snapshot_never_uses_future_position_or_owner(self):
        record = smoke_instances([actor("open", 1000, 0)])[0]
        record.update(owner=1, owner_time_ms=1500)
        record["positions"].append({"time_ms": 1500, "position_cm": [900, 900, 300], "source": {}, "method": "test"})
        model = {"utility": {"smokes": [record]}}
        snapshot = smoke_snapshot(model, 1200)["smokes"][0]
        self.assertEqual(snapshot["position_cm"], [500, 200, 300])
        self.assertIsNone(snapshot["owner"])
        self.assertEqual(smoke_snapshot(model, 1500)["smokes"][0]["position_cm"], [900, 900, 300])

    def test_smoke_relation_requires_explicit_radius_and_never_confirms_occlusion(self):
        record = smoke_instances([actor("open", 1000, 0)])[0]
        snapshot = smoke_snapshot({"utility": {"smokes": [record]}}, 1200)
        a, b = {"position_cm": [0, 0, 300]}, {"position_cm": [1000, 0, 300]}
        self.assertEqual(smoke_relation(a, b, snapshot)["status"], "unknown")
        result = smoke_relation(a, b, snapshot, 250)
        self.assertEqual(result["status"], "candidate_smoke_intersection")
        self.assertIsNone(result["actual_occlusion"])
        self.assertEqual(smoke_relation(a, b, snapshot, 100)["status"], "no_candidate_smoke_intersection")

    def test_no_observation_and_missing_position_do_not_establish_clear_vision(self):
        empty = smoke_snapshot({"utility": {"smokes": []}}, 1200)
        a = {"position_cm": [0, 0, 0]}
        result = smoke_relation(a, a, empty, 250)
        self.assertIsNone(result["actual_occlusion"])
        self.assertIn("not_proof_of_clear_vision", result["reason"])
        self.assertEqual(smoke_relation({"position_cm": None}, a, empty, 250)["status"], "unknown")

    def test_window_and_snapshot_caps_report_omissions(self):
        smoke = smoke_instances([actor("open", 1000, 0)])[0]
        records = [{**deepcopy(smoke), "id": str(i)} for i in range(250)]
        model = {"utility": {"version": 1, "smokes": records, "statuses": [], "blind_updates": [], "coverage": {}}}
        window = utility_window(model, ROUND, 1000, 2000)
        self.assertEqual((window["returned"], window["total"], window["truncated"]), (200, 250, True))
        snapshot = smoke_snapshot(model, 1200)
        self.assertEqual(snapshot["omitted"], 218)
        self.assertEqual(smoke_relation({"position_cm": [0, 0, 0]}, {"position_cm": [1, 1, 1]}, snapshot, 1)["status"], "unknown")

    def test_invalid_radii_are_rejected(self):
        for radius in (0, 2001, float("nan"), True, "400"):
            with self.assertRaises(QueryError):
                validate_smoke_radius(radius)
        validate_smoke_radius(None)

    def test_source_filter_preserves_rows_and_excludes_weapon_effects(self):
        rows = start() + start(row=5, actor=99)
        values = [{k: v for k, v in row.items() if k != "source_row"} for row in rows]
        with tempfile.TemporaryDirectory() as directory:
            export = Export.__new__(Export)
            export.root = Path(directory)
            pq.write_table(pa.Table.from_pylist(values), export.root / "fields.parquet")
            selected = export.utility_fields([1], [])
            self.assertEqual([r["source_row"] for r in selected], list(range(5)))
            self.assertTrue(all(r["actor_net_guid"] == 1 for r in selected))
