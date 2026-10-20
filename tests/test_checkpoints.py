"""Checkpoint seeding must respect clocks, explicit round markers and identity."""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from valcoach.checkpoints import GAME_STATE, PLAYER_STATE, reconstruct_checkpoints
from valcoach.equipment import latest_equipment
from valcoach.fight import query_fight
from valcoach.normalize import load_match, SCHEMA_VERSION
from valcoach.resources import resource_state
from valcoach.source import CHECKPOINT_TABLES, Export, QueryError
from tests.test_resources import (AMMO_GROUP, GUN, ROUND, PLAYERS, actor, equipment, guid, model, row)


def fixture(moment=490, round_number=0, index=19):
    players = [{**p,"id":str(p["state_id"])} for p in PLAYERS]
    actors = [actor(time=100),actor(11,PLAYER_STATE,time=100),actor(1,"player_body",time=100),actor(50,GAME_STATE,time=100)]
    cp_rows = [row(moment,"RoundNumber",round_number,actor=50,obj=None,group=GAME_STATE),
               row(moment,"Subject","11",actor=11,obj=None,group=PLAYER_STATE),
               row(moment,"SpawnedCharacter",1,actor=11,obj=None,group=PLAYER_STATE),
               row(moment,"Owner",1),row(moment,"AuthResourceAmount",12),
               row(moment,"AuthResourceAmount",36,obj=102),
               row(moment,"NumUltimatePoints",4,actor=11,obj=None,group=PLAYER_STATE),
               row(moment,"bUltimateActive",False,actor=11,obj=None,group=PLAYER_STATE),
               row(moment,"CurrentEquippable",100,actor=1,obj=103,group="/Script/ShooterGame.AresInventory")]
    for number, record in enumerate(cp_rows):
        record.update(source_row=number,checkpoint_index=index,checkpoint_id=f"cp-{index}")
    tables = {"fields":cp_rows,"actors":[{**a,"time_ms":moment,"checkpoint_index":index,"checkpoint_id":f"cp-{index}"} for a in actors],
              "net_guids":[{**g,"checkpoint_index":index,"checkpoint_id":f"cp-{index}"} for g in
                           [guid(101,"MagazineAmmo"),guid(102,"ReserveAmmo"),guid(103,"InventoryComponent",outer=1)]]}
    m = model([row(510,"Owner",1)],actors=actors,equips=[])
    m["players"] = players
    return m, tables, actors


def seeded(m, tables, actors, rounds=None):
    result = deepcopy(m)
    cp = reconstruct_checkpoints(tables,result["players"],rounds or [ROUND],actors)
    for key in ("observations","ownership"):
        result["resources"][key] += cp.resources[key]
    result["checkpoint_state"] = {"equipment":cp.equipment,"coverage":cp.coverage}
    return result


class CheckpointTests(unittest.TestCase):
    def test_boundary_seed_keeps_earlier_time_and_uses_round_marker_not_index(self):
        m,tables,actors = fixture()
        s = seeded(m,tables,actors)
        state = resource_state(s,ROUND,s["players"][0],1000)
        self.assertEqual(state["weapon"]["magazine"]["value"],12)
        self.assertEqual(state["weapon"]["reserve"]["value"],36)
        self.assertEqual(state["ultimate_points"]["value"],4)
        self.assertIs(state["ultimate_active"]["value"],False)
        self.assertEqual(state["ultimate_points"]["observed_time_ms"],490)
        self.assertEqual(state["ultimate_points"]["observation_age_ms"],510)
        self.assertTrue(state["ultimate_points"]["evidence_ids"][0].startswith("checkpoint_fields:"))
        self.assertEqual(s["timeline"],m["timeline"])
        self.assertEqual(s["checkpoint_state"]["coverage"]["snapshots"][0]["round"],1)
        self.assertEqual(s["checkpoint_state"]["coverage"]["snapshots"][0]["index"],19)

    def test_snapshot_from_future_cannot_fill_an_earlier_time(self):
        m,tables,actors = fixture(moment=1500)
        s=seeded(m,tables,actors)
        before=resource_state(s,ROUND,s["players"][0],1400)
        self.assertIsNone(before["ultimate_points"]["value"])
        self.assertIsNone(before["weapon"])
        self.assertEqual(resource_state(s,ROUND,s["players"][0],1500)["ultimate_points"]["value"],4)

    def test_missing_conflicting_and_inconsistent_round_markers_are_rejected(self):
        for change in ('missing','conflict','time','identity'):
            m,tables,actors=fixture()
            if change=='missing': tables['fields']=[r for r in tables['fields'] if r['field_name']!='RoundNumber']
            if change=='conflict': tables['fields'].append({**tables['fields'][0],'value_i64':1,'source_row':999})
            if change=='time': tables['actors'][0]['time_ms']=491
            if change=='identity': tables['net_guids'][0]['checkpoint_id']='other'
            cp=reconstruct_checkpoints(tables,m['players'],[ROUND],actors)
            self.assertEqual(cp.resources['observations'],[])
            self.assertEqual(cp.equipment,[])
            self.assertEqual(cp.coverage['snapshots_accepted'],0)

    def test_wrong_subject_or_pawn_cannot_seed_player_state_or_equipment(self):
        for field, replacement in [('Subject','other'),('SpawnedCharacter',999)]:
            m,tables,actors=fixture()
            for r in tables['fields']:
                if r['field_name']==field:
                    r['value_str']=replacement if type(replacement) is str else None
                    r['value_i64']=replacement if type(replacement) is int else None
            s=seeded(m,tables,actors)
            state=resource_state(s,ROUND,s['players'][0],1000)
            self.assertIsNone(state['ultimate_points']['value'])
            self.assertEqual(s['checkpoint_state']['equipment'],[])

    def test_wrong_class_missing_live_actor_and_foreign_component_do_not_seed_ammo(self):
        for change in ('class','future_open','component'):
            m,tables,actors=fixture()
            if change=='class': tables['actors'][0]['class_path']='other_class'
            if change=='future_open': actors[0]['time_ms']=600
            if change=='component': tables['net_guids'][0]['outer_net_guid']=999
            s=seeded(m,tables,actors)
            magazine=[r for r in s['resources']['observations'] if r['metric']=='magazine_ammo']
            self.assertEqual(magazine,[])

    def test_later_unknown_update_overrides_known_checkpoint_and_conflicts_stay_unknown(self):
        m,tables,actors=fixture()
        m=model([row(510,'Owner',1),row(700,'AuthResourceAmount',None)],actors=actors)
        m['players']=[{**p,'id':str(p['state_id'])} for p in PLAYERS]
        s=seeded(m,tables,actors)
        self.assertEqual(resource_state(s,ROUND,s['players'][0],600)['weapon']['magazine']['value'],12)
        self.assertIsNone(resource_state(s,ROUND,s['players'][0],1000)['weapon']['magazine']['value'])
        tables['fields'].append({**tables['fields'][4],'value_i64':11,'source_row':999})
        s=seeded(m,tables,actors)
        state=resource_state(s,ROUND,s['players'][0],600)['weapon']['magazine']
        self.assertEqual(state['status'],'conflicting_same_time_observations')
        self.assertIsNone(state['value'])

    def test_same_timestamp_live_and_checkpoint_disagreement_is_not_ordered_by_packet(self):
        m,tables,actors=fixture(moment=600)
        m=model([row(510,'Owner',1),row(600,'AuthResourceAmount',11)],actors=actors,equips=[equipment(550)])
        m['players']=[{**p,'id':str(p['state_id'])} for p in PLAYERS]
        s=seeded(m,tables,actors)
        self.assertEqual(resource_state(s,ROUND,s['players'][0],1000)['weapon']['magazine']['status'],
                         'conflicting_same_time_observations')

    def test_seeds_do_not_leak_to_another_round_or_a_revived_life(self):
        m,tables,actors=fixture()
        s=seeded(m,tables,actors)
        later={**ROUND,'number':2,'buy_ms':4000,'live_ms':5000}
        self.assertIsNone(resource_state(s,later,s['players'][0],6000)['ultimate_points']['value'])
        s['timeline']=[{'kind':'death','victim':1,'time_ms':1500},{'kind':'resurrection','player':1,'time_ms':2000}]
        self.assertIsNone(resource_state(s,ROUND,s['players'][0],2300)['ultimate_points']['value'])
        self.assertIsNone(resource_state(s,ROUND,s['players'][0],2300)['weapon'])

    def test_close_and_guid_reuse_invalidate_the_old_seed(self):
        m,tables,actors=fixture()
        actors.extend([actor(time=1400,event='close'),actor(time=1600)])
        s=seeded(m,tables,actors)
        s['equipment'].append(equipment(1700))
        self.assertIsNone(resource_state(s,ROUND,s['players'][0],1800)['weapon'])

    def test_absent_fields_and_bad_source_quality_never_create_defaults(self):
        m,tables,actors=fixture()
        tables['fields']=[r for r in tables['fields'] if r['field_name'] not in ('bUltimateActive','NumUltimatePoints')]
        s=seeded(m,tables,actors)
        self.assertIsNone(resource_state(s,ROUND,s['players'][0],1000)['ultimate_active']['value'])
        tables['quality']={'checkpoint_partial_rows':1}
        s=seeded(m,tables,actors)
        self.assertEqual(s['checkpoint_state']['coverage']['status'],'rejected_source_quality')
        self.assertEqual(s['checkpoint_state']['equipment'],[])

    def test_optional_checkpoint_files_affect_cache_identity_but_not_match_id(self):
        cache_root=Path(__file__).resolve().parents[1]/'.valcoach-cache'
        cache_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=cache_root) as folder:
            root=Path(folder)
            (root/'manifest.json').write_text(json.dumps({'replay_build':'++Ares-Core+release-13.08'}))
            for table in Export.TABLES:(root/f'{table}.parquet').touch()
            plain=Export(root)
            self.assertIsNone(plain.checkpoints())
            for table in CHECKPOINT_TABLES:(root/f'{table}.parquet').write_bytes(b'initial')
            first=Export(root)
            (root/'checkpoint_fields.parquet').write_bytes(b'changed')
            second=Export(root)
            self.assertEqual(plain.fingerprint,first.fingerprint)
            self.assertEqual(first.fingerprint,second.fingerprint)
            self.assertNotEqual(plain.checkpoint_fingerprint,first.checkpoint_fingerprint)
            self.assertNotEqual(first.checkpoint_fingerprint,second.checkpoint_fingerprint)
            with patch('valcoach.normalize.normalize',side_effect=lambda _: {'schema_version':SCHEMA_VERSION}) as rebuild:
                original,reused=load_match(first,root/'cache')
                self.assertFalse(reused)
                _,reused=load_match(first,root/'cache')
                self.assertTrue(reused)
                changed,reused=load_match(second,root/'cache')
                self.assertFalse(reused)
                self.assertEqual(rebuild.call_count,2)
                self.assertNotEqual(original['revision'],changed['revision'])
                self.assertEqual(original['match_id'],changed['match_id'])
            (root/'checkpoint_actors.parquet').unlink()
            with self.assertRaises(QueryError) as caught:Export(root)
            self.assertEqual(caught.exception.code,'incomplete_checkpoint_export')


ROOT=Path(__file__).resolve().parents[1]
PRIVATE=Path(os.environ.get('VALCOACH_TEST_EXPORT_1308',ROOT/'output/replay-5c31718d/raw'))


@unittest.skipUnless((PRIVATE/'manifest.json').is_file(),'Second private replay export not available')
class CheckpointReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export=Export(PRIVATE)
        cls.model,_=load_match(cls.export)
        cls.player=next(p for p in cls.model['players'] if p['state_id']==58)

    def test_round_two_initial_points_have_checkpoint_source_and_real_earlier_time(self):
        rd=self.model['rounds'][1]
        state=resource_state(self.model,rd,self.player,rd['live_ms'])
        points=state['ultimate_points']
        self.assertEqual(points['value'],2)
        self.assertEqual(points['observed_time_ms'],94996)
        self.assertLess(points['observed_time_ms'],rd['buy_ms'])
        record=query_fight(self.model,self.export,2,self.player,evidence_id=points['evidence_ids'][0])['record']
        self.assertEqual(record['scope_round'],2)
        self.assertEqual(record['source']['checkpoint_index'],1)
        self.assertEqual(record['source']['table'],'checkpoint_fields')
        self.assertIsNone(state['ultimate_active']['value'])

    def test_all_checkpoints_have_explicit_round_mapping_and_missing_first_is_rejected(self):
        coverage=self.model['checkpoint_state']['coverage']
        self.assertEqual(coverage['snapshots_accepted'],23)
        self.assertEqual(coverage['rejections'],{'missing_or_conflicting_round_marker':1})
        self.assertEqual([s['round'] for s in coverage['snapshots']],list(range(2,25)))

    def test_checkpoint_equipment_is_retrievable_without_inventing_a_live_equip_event(self):
        observation=next(r for r in self.model['checkpoint_state']['equipment']
                         if r['scope_round']==2 and r['player']==self.player['pawn'])
        self.assertTrue(observation['id'].startswith('checkpoint_fields:'))
        detail=query_fight(self.model,self.export,2,self.player,evidence_id=observation['id'])['record']
        self.assertEqual(detail['item'],0)
        self.assertNotIn(detail['id'],{e['id'] for e in self.model['equipment']})


if __name__=='__main__':unittest.main()
