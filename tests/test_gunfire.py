"""Firing observations must survive tag shifts and retain inference limits."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import struct
import unittest

from valcoach.__main__ import main
from valcoach.fight import query_fight
from valcoach.gunfire import EFFECT_GROUP, query_gunfire, reconstruct_gunfire
from valcoach.normalize import load_match
from valcoach.source import Export, QueryError
from tests.test_resources import PLAYERS,ROUND,GUN,actor,guid,row


FUNCTION="ReplayPlayContinuousEffectAtLocation"
TAGS={10:"FiringState.NumProjectiles",11:"FiringState.AmmoRemaining",12:"FiringState.FiringPlayerState",
      13:"FiringState.FiringState",14:"FiringState.AttackVector.1",15:"FiringState.BurstShotNumber"}


def invocation(time=1200,packet=None,base=0,projectiles=1,ammo=7,function=FUNCTION,shift=0):
    fields=[('EffectContainer',300),('FloatValues',json.dumps([{'tag':10+shift,'value':projectiles},{'tag':11+shift,'value':ammo}])),
            ('ObjectValues',json.dumps([{'tag':12+shift,'value':11},{'tag':13+shift,'value':203}])),
            ('VectorValues',json.dumps([{'tag':14+shift,'value':{'x':1,'y':0,'z':0}}]))]
    return [{**row(time,f'{function}.{name}',val,actor=2,obj=24,group=EFFECT_GROUP,index=base+i),
             'packet_id':time if packet is None else packet} for i,(name,val) in enumerate(fields)]


def normalized(rows,shift=0,actors=None,guids=None):
    manifest={'net_field_export_groups':[{'path':'NetworkGameplayTagNodeIndex','fields':[
        {'handle':number+shift,'name':name} for number,name in TAGS.items()]}]}
    data=reconstruct_gunfire(rows,manifest,actors or [actor()],guids or [guid(203,'FiringState')],PLAYERS)
    return {'gunfire':data,'damage':[],'players':PLAYERS,'rounds':[ROUND],'match_id':'test','revision':'rev','replay_build':'test'}


class GunfireTests(unittest.TestCase):
    def test_declared_tag_names_resolve_shifted_ids_with_source_and_directions(self):
        for shift in (0,800):
            m=normalized(invocation(shift=shift),shift=shift)
            r=m['gunfire']['observations'][0]
            self.assertEqual((r['player'],r['item'],r['item_kind']),(1,100,'gun'))
            self.assertEqual((r['ammo_remaining'],r['num_projectiles']),(7,1))
            self.assertEqual(r['attack_vectors'],[{'index':1,'direction':[1,0,0]}])
            self.assertIsNone(r['burst_shot_number'])
            self.assertIsNone(r['origin_cm'])
            self.assertIsNone(r['hit_or_miss'])
            self.assertEqual(r['source']['row'],0)

    def test_same_packet_repeated_calls_are_not_overwritten_or_deduplicated(self):
        rows=invocation(packet=10)+invocation(packet=10,base=4,ammo=6)
        records=normalized(rows)['gunfire']['observations']
        self.assertEqual([r['ammo_remaining'] for r in records],[7,6])
        self.assertNotEqual(records[0]['id'],records[1]['id'])

    def test_non_firing_effects_and_missing_tag_table_do_not_become_shots(self):
        rows=invocation()
        rows[1]['value_str']='[{"tag":999,"value":1}]'
        rows[2]['value_str']='[]';rows[3]['value_str']='[]'
        self.assertEqual(normalized(rows)['gunfire']['observations'],[])
        data=reconstruct_gunfire(invocation(),{},[actor()],[guid(203,'FiringState')],PLAYERS)
        self.assertFalse(data['coverage']['tag_names_available'])
        self.assertEqual(data['observations'],[])

    def test_malformed_arrays_and_fractional_counts_remain_explicit(self):
        rows=invocation(projectiles=1.5)
        rows[3]['value_str']='invalid'
        r=normalized(rows)['gunfire']['observations'][0]
        self.assertIsNone(r['num_projectiles'])
        self.assertEqual(r['field_status']['num_projectiles'],'invalid_value')
        self.assertEqual(r['attack_vectors'],[])
        self.assertIn('invalid_or_untyped_VectorValues',r['issues'])
        r=normalized(invocation(projectiles=0,ammo=0))['gunfire']['observations'][0]
        self.assertEqual((r['num_projectiles'],r['ammo_remaining']),(0,0))

    def test_conflicting_tags_do_not_choose_a_value(self):
        rows=invocation()
        rows[1]['value_str']='[{"tag":10,"value":1},{"tag":10,"value":3}]'
        r=normalized(rows)['gunfire']['observations'][0]
        self.assertIsNone(r['num_projectiles'])
        self.assertEqual(r['field_status']['num_projectiles'],'conflicting_values')

    def test_actor_not_yet_open_and_cyclic_outer_chain_do_not_guess_weapon(self):
        for actors,guids in [([actor(time=1500)],[guid(203,'FiringState')]),
                              ([actor()],[guid(203,'FiringState',outer=204),guid(204,'loop',outer=203)])]:
            r=normalized(invocation(),actors=actors,guids=guids)['gunfire']['observations'][0]
            self.assertIsNone(r['item'])
            self.assertEqual(r['item_kind'],'unknown')
            self.assertEqual(r['player'],1)

    def test_multiple_projectiles_and_other_firing_sources_are_not_player_bullet_totals(self):
        for path in ('/Game/Characters/Deadeye/S0/Ability_Q/Gun/Gun_Deadeye_Q_Pistol_C',
                     '/Game/Characters/Killjoy/S0/Ability_E/Ability_Killjoy_E_TurretAttack_C'):
            record=normalized(invocation(projectiles=15),actors=[actor(path=path)])['gunfire']['observations'][0]
            self.assertEqual(record['num_projectiles'],15)
            self.assertEqual(record['item_kind'],'other_firing_source')
            self.assertIsNone(record['hit_or_miss'])

    def test_origin_requires_exact_finite_wire_vector(self):
        for raw,bits,expected in [(struct.pack('<3d',0,1,2),192,[0.0,1.0,2.0]),
                                  (struct.pack('<3d',float('nan'),1,2),192,None),(b'\x00',192,None)]:
            rows=invocation()+[row(1200,FUNCTION+'.248',None,actor=2,obj=24,group=EFFECT_GROUP,index=4,raw=raw,bits=bits)]
            self.assertEqual(normalized(rows)['gunfire']['observations'][0]['origin_cm'],expected)

    def test_pagination_window_and_damage_context_do_not_assign_hits(self):
        m=normalized(invocation(time=1200)+invocation(time=1250,base=10,ammo=6)+invocation(time=3000,base=20))
        m['damage']=[{'id':'damage','time_ms':1260,'attacker':1,'weapon_actor':100}]
        copy=deepcopy(m)
        page=query_gunfire(m,1,PLAYERS[0],start_ms=0,end_ms=1000,limit=1)
        self.assertEqual((page['observations']['total'],page['observations']['next_offset']),(2,1))
        self.assertEqual(page['observations']['records'][0]['nearby_damage']['damage_ids'],['damage'])
        other=query_gunfire(m,1,PLAYERS[0],start_ms=0,end_ms=1000,limit=1,offset=1)
        self.assertEqual(other['observations']['records'][0]['nearby_damage']['damage_ids'],['damage'])
        self.assertIsNone(other['observations']['records'][0]['hit_or_miss'])
        self.assertEqual(m,copy)
        for args in ({'start_ms':True},{'start_ms':-501},{'limit':101},{'damage_window_ms':-1}):
            with self.assertRaises(QueryError):query_gunfire(m,1,PLAYERS[0],**args)


ROOT=Path(__file__).resolve().parents[1]
PRIVATE=Path(os.environ.get('VALCOACH_TEST_EXPORT_1308',ROOT/'output/replay-5c31718d/raw'))
ORIGINAL=Path(os.environ.get('VALCOACH_TEST_EXPORT',ROOT/'output/first-replay/raw'))


@unittest.skipUnless((ORIGINAL/'manifest.json').is_file(),'Original private replay export not available')
class OriginalGunfireReferenceTests(unittest.TestCase):
    def test_original_build_resolves_gun_and_projectile_observations(self):
        model,_=load_match(Export(ORIGINAL))
        player=next(p for p in model['players'] if p['state_id']==254)
        result=query_gunfire(model,1,player)
        records=result['observations']['records']
        self.assertGreater(result['observations']['total'],0)
        self.assertTrue(any(r['item_kind']=='gun' and r['num_projectiles'] is not None for r in records))
        self.assertTrue(model['gunfire']['coverage']['tag_names_available'])


@unittest.skipUnless((PRIVATE/'manifest.json').is_file(),'Second private replay export not available')
class GunfireReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.export=Export(PRIVATE);cls.model,_=load_match(cls.export)
        cls.player=next(p for p in cls.model['players'] if p['state_id']==58)

    def test_tagged_effects_and_firing_without_nearby_damage_are_exposed(self):
        result=query_gunfire(self.model,2,self.player,start_ms=28000,end_ms=31000)
        self.assertGreater(result['observations']['total'],0)
        records=result['observations']['records']
        self.assertTrue(any(not r['nearby_damage']['damage_ids'] for r in records))
        for r in records:
            detail=query_fight(self.model,self.export,2,self.player,evidence_id=r['id'])['record']
            self.assertEqual(detail['attack_vectors'],r['attack_vectors'])
            self.assertIsNone(detail['hit_or_miss'])

    def test_cli_and_item_categories_remain_explicit(self):
        output=io.StringIO()
        with redirect_stdout(output):
            code=main(['gunfire','2','--export',str(PRIVATE),'--player','58','--start-ms','28000','--end-ms','31000','--json'])
        self.assertEqual(code,0)
        self.assertEqual(json.loads(output.getvalue())['query'],'gunfire')
        self.assertTrue(any(r['item_kind']=='other_firing_source' and 'TurretAttack' in r['item_class']
                            for r in self.model['gunfire']['observations']))


if __name__=='__main__':unittest.main()
