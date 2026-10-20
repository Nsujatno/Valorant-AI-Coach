"""Resource reconstruction boundaries: identity, ownership, missingness and time."""

from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import struct
import unittest

from valcoach.__main__ import main
from valcoach.fight import query_fight
from valcoach.normalize import load_match
from valcoach.resources import (AMMO_GROUP, CHARGE_GROUPS, COOLDOWN_GROUP, DAGGER_GROUP,
                               STATE_GROUP, query_resources, reconstruct_resources, resource_state, resource_evidence)
from valcoach.source import Export, QueryError


GUN = "/Game/Equippables/Guns/Rifles/AssaultRifle_AK/AssaultRifle_AK_C"
KNIVES = "/Game/Characters/Wushu/S0/Ability_X/Ability_Wushu_X_Dagger_Production.Ability_Wushu_X_Dagger_Production_C"
DASH = "/Game/Characters/Wushu/S0/Ability_E/Ability_Wushu_E_Dash.Ability_Wushu_E_Dash_C"
ROUND = {"number": 1, "buy_ms": 500, "live_ms": 1000, "end_ms": 10000}
PLAYERS = [{"state_id": 11, "pawn": 1, "agent": "Jett"}, {"state_id": 12, "pawn": 2, "agent": "Sova"}]


def row(time, name, val, actor=100, obj=101, group=AMMO_GROUP, index=None, raw=None, bits=None):
    return {"source_row": index if index is not None else time, "time_ms": time, "packet_id": time,
            "actor_net_guid": actor, "object_net_guid": obj, "group_path": group, "field_name": name,
            "value_i64": val if type(val) is int else None, "value_bool": val if type(val) is bool else None,
            "value_f64": val if type(val) is float else None, "value_str": val if type(val) is str else None,
            "raw_bits": raw, "bit_count": bits}


def actor(item=100, path=GUN, time=500, event="open"):
    return {"actor_net_guid": item, "time_ms": time, "event": event, "class_path": path}


def guid(id_, path, outer=100):
    return {"net_guid": id_, "path": path, "outer_net_guid": outer}


def equipment(time=900, item=100):
    return {"id": "equip", "kind": "equipped", "time_ms": time, "player": 1, "item": item,
            "item_class": GUN, "weapon": "Vandal"}


def model(rows, actors=None, guids=None, timeline=None, equips=None):
    actors = actors or [actor(), actor(11,"player_state"), actor(12,"player_state")]
    guids = guids if guids is not None else [guid(101,"MagazineAmmo"),guid(102,"ReserveAmmo")]
    return {"resources": reconstruct_resources(rows, actors, guids, PLAYERS), "players": PLAYERS,
            "timeline": timeline or [], "equipment": [equipment()] if equips is None else equips,
            "rounds": [ROUND], "match_id": "synthetic", "revision": "test", "replay_build": "test"}


class ResourceTests(unittest.TestCase):
    def test_separate_magazine_and_reserve_and_keep_zero(self):
        m = model([row(600,"Owner",1,obj=None), row(700,"AuthResourceAmount",12),
                   row(700,"AuthResourceAmount",36,obj=102,index=701), row(1500,"AuthResourceAmount",0)])
        state = resource_state(m,ROUND,PLAYERS[0],2000)
        self.assertEqual(state["weapon"]["magazine"]["value"],0)
        self.assertEqual(state["weapon"]["reserve"]["value"],36)
        self.assertEqual(state["weapon"]["magazine"]["observed_time_ms"],1500)
        self.assertIsNone(state["shots_fired"])
        self.assertIsNone(state["missed_shots"])
        self.assertIsNone(state["effective_readiness"])

    def test_wrong_group_or_foreign_component_never_supplies_ammo(self):
        m = model([row(600,"Owner",1), row(700,"AuthResourceAmount",99,group="other"),
                   row(800,"AuthResourceAmount",88,obj=102)],guids=[guid(101,"MagazineAmmo"),guid(102,"MagazineAmmo",outer=999)])
        self.assertEqual(m["resources"]["observations"],[])

    def test_pickup_retains_same_item_ammo_but_not_previous_players_events(self):
        m = model([row(600,"Owner",2),row(700,"AuthResourceAmount",7),row(1600,"Owner",1)])
        before = resource_state(m,ROUND,PLAYERS[0],1500)
        after = resource_state(m,ROUND,PLAYERS[0],2000)
        self.assertIsNone(before["weapon"])
        self.assertEqual(after["weapon"]["magazine"]["value"],7)
        self.assertEqual(after["weapon"]["ownership_sources"][0]["row"],1600)
        evidence = resource_evidence(m,ROUND,1,after["weapon"]["magazine"]["evidence_ids"][0])
        self.assertEqual(evidence["player"],2)
        self.assertEqual(evidence["value"],7)
        self.assertIsNone(resource_evidence(m,ROUND,999,evidence["id"]))
        self.assertEqual(query_resources(m,1,PLAYERS[0],1000)["observations"]["total"],0)
        m["resources"]["ownership"].append({"item":100,"opened_ms":500,"time_ms":1900,"owner":0,"source":{}})
        self.assertIsNone(resource_state(m,ROUND,PLAYERS[0],2000)["weapon"])

    def test_future_prior_round_and_invalid_latest_values_do_not_fill(self):
        m = model([row(500,"Owner",1),row(700,"AuthResourceAmount",12),
                   row(1500,"AuthResourceAmount",None),row(3000,"AuthResourceAmount",30)])
        result = resource_state(m,ROUND,PLAYERS[0],2000)["weapon"]["magazine"]
        self.assertIsNone(result["value"])
        self.assertEqual(result["status"],"unknown_value")
        later_round = {**ROUND,"buy_ms":3500,"live_ms":4000}
        self.assertIsNone(resource_state(m,later_round,PLAYERS[0],4500)["weapon"])

    def test_death_and_revive_need_new_life_equip_and_resources(self):
        m = model([row(500,"Owner",1),row(700,"AuthResourceAmount",12)],timeline=[
            {"kind":"death","victim":1,"time_ms":1500},
            {"kind":"resurrection","player":1,"time_ms":2000}])
        self.assertEqual(resource_state(m,ROUND,PLAYERS[0],1800)["life_state"],"dead")
        m["equipment"].append(equipment(2200))
        self.assertIsNone(resource_state(m,ROUND,PLAYERS[0],2300)["weapon"])

    def test_closed_actor_and_reused_guid_do_not_supply_old_counts(self):
        m = model([row(500,"Owner",1),row(700,"AuthResourceAmount",12),row(2500,"Owner",1)],
                  actors=[actor(),actor(time=2000,event="close"),actor(time=2500)])
        self.assertIsNone(resource_state(m,ROUND,PLAYERS[0],2000)["weapon"])
        self.assertIsNone(resource_state(m,ROUND,PLAYERS[0],3000)["weapon"])

    def test_conflicting_same_time_values_stay_unknown(self):
        m = model([row(500,"Owner",1),row(1500,"AuthResourceAmount",3),
                   row(1500,"AuthResourceAmount",4,index=1501)])
        result = resource_state(m,ROUND,PLAYERS[0],2000)["weapon"]["magazine"]
        self.assertEqual(result["status"],"conflicting_same_time_observations")
        self.assertIsNone(result["value"])
        m["resources"]["ownership"].append({"item":100,"opened_ms":500,"time_ms":500,"owner":2,"source":{}})
        self.assertIsNone(resource_state(m,ROUND,PLAYERS[0],2000)["weapon"])

    def test_blueprint_knife_counts_exact_width_signed_and_zero(self):
        for bits, raw, expected in [(32,struct.pack('<i',0),0),(32,struct.pack('<i',5),5),
                                    (31,struct.pack('<i',5),None),(32,b'\x05',None),
                                    (32,struct.pack('<i',-1),None)]:
            m = model([row(500,"Owner",1),row(1000,"Multicast Update Daggers.DaggersRemaining",None,
                      group=DAGGER_GROUP,raw=raw,bits=bits)],actors=[actor(path=KNIVES)],guids=[guid(101,"Comp_Wushu_X_DaggerResource")])
            self.assertEqual(m["resources"]["observations"][0]["value"],expected)

    def test_new_ultimate_activation_does_not_reuse_old_knife_count(self):
        m = model([row(500,"Owner",1),row(1000,"Ammo",None,group="/Script/ShooterGame.BlueprintResourceVisualizationComponent",
                   raw=struct.pack('<i',2),bits=32),row(2000,"bUltimateActive",True,actor=11,obj=None)],
                   actors=[actor(path=KNIVES),actor(11,"player_state")],guids=[guid(101,"BlueprintResourceVisualization")])
        result = resource_state(m,ROUND,PLAYERS[0],2500)
        self.assertTrue(result["ultimate_active"]["value"])
        self.assertIsNone(result["abilities"][0]["resources"]["blade_storm_knives"]["value"])

    def test_resource_channels_are_separate_and_cooldown_not_readiness(self):
        group = "/Script/ShooterGame.SignatureAbilityResourceComponent"
        m = model([row(500,"Owner",1),row(1000,"AuthResourceAmount",0,group=group),
                   row(1100,"AuthSignatureChargeAmount",1,group=group),
                   row(1200,"CurrentTemporaryCharges",1,group=group),
                   row(1300,"CooldownActive",True,group=COOLDOWN_GROUP),
                   row(1400,"CooldownSeconds",20.0,group=COOLDOWN_GROUP)],
                   actors=[actor(path=DASH)],guids=[guid(101,"SignatureAbilityResource")])
        state = resource_state(m,ROUND,PLAYERS[0],2000)
        resources = state["abilities"][0]["resources"]
        self.assertEqual([resources[k]["value"] for k in ('charge_resource','signature_charge_resource','temporary_charges')],[0,1,1])
        self.assertTrue(resources["cooldown_active"]["value"])
        self.assertIsNone(state["effective_readiness"])

    def test_weapon_state_does_not_mix_zoom_and_main_machine(self):
        m = model([row(500,"Owner",1),row(1000,"CurrentState",200,group=STATE_GROUP),
                   row(1100,"CurrentState",201,obj=102,group=STATE_GROUP),
                   row(1300,"CurrentState",0,group=STATE_GROUP)],
                   guids=[guid(101,"EquippableStateMachine"),guid(102,"ZoomStateMachine"),
                          guid(200,"ReloadState"),guid(201,"FiringState")])
        state = resource_state(m,ROUND,PLAYERS[0],1200)
        self.assertEqual(state["weapon"]["state"]["value"],"ReloadState")
        self.assertIsNone(resource_state(m,ROUND,PLAYERS[0],1500)["weapon"]["state"]["value"])

    def test_pagination_and_query_boundaries(self):
        m = model([row(500,"Owner",1)]+[row(t,"AuthResourceAmount",t//100) for t in range(1000,2000,100)])
        first = query_resources(m,1,PLAYERS[0],500,limit=3)
        second = query_resources(m,1,PLAYERS[0],500,limit=3,offset=3)
        self.assertEqual(first["observations"]["total"],10)
        self.assertEqual(first["observations"]["next_offset"],3)
        self.assertTrue(set(r["id"] for r in first["observations"]["records"]).isdisjoint(r["id"] for r in second["observations"]["records"]))
        for kwargs in ({"at_ms":True},{"at_ms":9000},{"at_ms":1,"before_ms":5001},{"at_ms":1,"limit":201},{"at_ms":1,"offset":-1}):
            with self.assertRaises(QueryError): query_resources(m,1,PLAYERS[0],**kwargs)


ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path(os.environ.get("VALCOACH_TEST_EXPORT_1308",ROOT/"output/replay-5c31718d/raw"))


@unittest.skipUnless((PRIVATE/"manifest.json").is_file(),"Second private replay export not available")
class ResourceReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export = Export(PRIVATE)
        cls.model,_ = load_match(cls.export)
        cls.selected = next(p for p in cls.model["players"] if p["state_id"]==58)

    def test_r24_zero_knives_and_spectre_ammo_at_contact(self):
        result = query_resources(self.model,24,self.selected,64639)
        state = result["state"]
        knives = next(a for a in state["abilities"] if a["ability"]=="Blade Storm")["resources"]["blade_storm_knives"]
        self.assertEqual(knives["value"],0)
        live = next(r for r in self.model["rounds"] if r["number"]==24)["live_ms"]
        self.assertEqual(knives["observed_time_ms"]-live,14993)
        self.assertIs(state["ultimate_active"]["value"],False)
        self.assertEqual(state["weapon"]["weapon"],"Spectre")
        self.assertEqual(state["weapon"]["magazine"]["value"],25)
        self.assertEqual(state["weapon"]["reserve"]["value"],90)
        self.assertEqual(state["weapon"]["state"]["value"],"ReadyingState")
        fight = query_fight(self.model,self.export,24,self.selected,at_ms=64639)
        self.assertEqual(fight["state_at_anchor"]["resources"],state)
        evidence = query_fight(self.model,self.export,24,self.selected,evidence_id=knives["evidence_ids"][0])
        self.assertEqual(evidence["record"]["value"],0)
        reserve = query_fight(self.model,self.export,24,self.selected,evidence_id=state["weapon"]["reserve"]["evidence_ids"][0])
        self.assertEqual(reserve["record"]["value"],90)
        self.assertNotEqual(reserve["record"]["player"],self.selected["pawn"])

    def test_r24_depletion_transition_and_no_future_leak(self):
        before = query_resources(self.model,24,self.selected,14992)["state"]
        knives = next(a for a in before["abilities"] if a["ability"]=="Blade Storm")["resources"]["blade_storm_knives"]
        self.assertEqual(knives["value"],1)
        self.assertTrue(before["ultimate_active"]["value"])

    def test_r1_reload_state_and_later_ammo_refill_remain_distinct(self):
        before = query_resources(self.model,1,self.selected,29676)["state"]["weapon"]
        after = query_resources(self.model,1,self.selected,30520)["state"]["weapon"]
        self.assertEqual(before["state"]["value"],"ReloadState")
        self.assertEqual(before["magazine"]["value"],1)
        self.assertEqual(after["magazine"]["value"],8)
        self.assertEqual(after["reserve"]["value"],17)
        self.assertEqual(before["item"],after["item"])

    def test_cli_json_and_readable_resource_view(self):
        args = ['resources','24','--export',str(PRIVATE),'--player','58','--at-ms','64639']
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(main(args+['--json']),0)
        self.assertEqual(json.loads(output.getvalue())["state"]["weapon"]["magazine"]["value"],25)
        output = io.StringIO()
        with redirect_stdout(output): self.assertEqual(main(args),0)
        self.assertIn('blade_storm_knives: 0',output.getvalue())


ORIGINAL = Path(os.environ.get("VALCOACH_TEST_EXPORT",ROOT/"output/first-replay/raw"))


@unittest.skipUnless((ORIGINAL/"manifest.json").is_file(),"Original private replay export not available")
class OriginalResourceReferenceTests(unittest.TestCase):
    def test_1306_knife_zero_is_exposed_without_enabling_other_builds(self):
        m,_ = load_match(Export(ORIGINAL))
        selected = next(p for p in m["players"] if p["state_id"]==254)
        state = query_resources(m,6,selected,49199)["state"]
        knives = next(a for a in state["abilities"] if a["ability"]=="Blade Storm")
        self.assertEqual(knives["resources"]["blade_storm_knives"]["value"],0)
        self.assertEqual(state["life_state"],"alive")


if __name__ == '__main__': unittest.main()
