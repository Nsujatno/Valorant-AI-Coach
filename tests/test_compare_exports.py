"""Upgrade reporting distinguishes source discovery, drift and fact meaning."""

from contextlib import redirect_stdout
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest

import pyarrow as pa
import pyarrow.parquet as pq

from valcoach.compare_exports import compare_exports,compare_facts,compare_inventories,inventory_export,main
from valcoach.source import QueryError


ROOT=Path(__file__).resolve().parents[1]


def write_export(root,rows,quality=None,guid=None):
    root.mkdir()
    manifest={'guid':guid or [1,2,3,4],'source_size_bytes':1234,'replay_build':'++Ares-Core+release-13.08',
              'quality':quality if quality is not None else {'partial_rows':0},'net_field_export_groups':[]}
    (root/'manifest.json').write_text(json.dumps(manifest))
    schema=pa.schema([('group_path',pa.string()),('field_name',pa.string()),('compatible_checksum',pa.uint32()),
                      ('bit_count',pa.uint32()),('value_i64',pa.int64()),('value_f64',pa.float64()),
                      ('value_str',pa.string()),('value_bool',pa.bool_())])
    pq.write_table(pa.Table.from_pylist(rows,schema=schema),root/'fields.parquet')


def field(name='Ammo',val=9):
    return {'group_path':'component','field_name':name,'compatible_checksum':10,'bit_count':32,'value_i64':val,
            'value_f64':None,'value_str':None,'value_bool':None}


def facts():
    return {'schema_version':6,'players':[{'id':'person','pawn':1,'state_id':11,'team':10}],
            'damage':[{'id':'damage:old','source':{'row':1},'time_ms':1200,'attacker':1,'victim':2,'actual_damage':40}],
            'casts':[{'Player':'person','Slot':5,'Round':0,'RoundPhase':4,'CastTime':2.5,'source':{'row':2}}],
            'resources':{'observations':[{'id':'old','source':{'row':3},'time_ms':1000,'player':1,'item':100,'metric':'magazine_ammo','value':9}]},
            'gunfire':{'observations':[{'id':'old','time_ms':1200,'player':1,'item':100,'ammo_remaining':8,'num_projectiles':1,
                'attack_vectors':[{'index':1,'direction':[1,0,0]}],'component_sources':[{'row':8}]}]},
            'scoreboard':{'1':{'kills':{'value':1,'observed_time_ms':2000,'status':'observed','source':{'row':20}}}}}


class SemanticComparisonTests(unittest.TestCase):
    def test_source_row_ids_and_order_do_not_change_fact_meaning(self):
        before=facts();after=deepcopy(before)
        after['damage'][0]['id']='different';after['damage'][0]['source']={'row':999,'packet_id':300}
        after['casts'][0]['source']={'row':1000}
        after['resources']['observations'][0]['id']='new';after['resources']['observations'][0]['source']={'row':3000}
        after['gunfire']['observations'][0]['component_sources']=[{'row':4000}]
        after['scoreboard']['1']['kills']['source']={'row':9999}
        result=compare_facts(before,after)
        self.assertTrue(result['equal'])

    def test_damage_multiplicity_is_preserved(self):
        before=facts();after=deepcopy(before);after['damage'].append(deepcopy(after['damage'][0]))
        result=compare_facts(before,after)
        self.assertFalse(result['equal'])
        self.assertEqual(result['groups']['damage']['added'],1)

    def test_ammo_cast_timing_and_projectile_changes_are_reported(self):
        before=facts();after=deepcopy(before)
        after['resources']['observations'][0]['value']=8
        after['casts'][0]['CastTime']=3.5
        after['gunfire']['observations'][0]['num_projectiles']=3
        result=compare_facts(before,after)
        for group in ('resources','casts','gunfire'):
            self.assertEqual((result['groups'][group]['removed'],result['groups'][group]['added']),(1,1))

    def test_examples_are_bounded_but_total_differences_are_complete(self):
        before=facts();after=deepcopy(before)
        after['resources']['observations']=[{**before['resources']['observations'][0],'time_ms':1000+i,'value':i} for i in range(10)]
        result=compare_facts(before,after,limit=1)['groups']['resources']
        self.assertEqual(result['added'],10)
        self.assertEqual(result['added_examples']['total'],10)
        self.assertEqual(result['added_examples']['omitted'],9)


class InventoryComparisonTests(unittest.TestCase):
    def setUp(self):
        cache=ROOT/'.valcoach-cache';cache.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=cache)
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def pair(self,before,after,**kwargs):
        old,new=self.root/'old',self.root/'new'
        write_export(old,before);write_export(new,after,**kwargs)
        return old,new

    def test_new_fields_are_discovered_without_normalizer_support(self):
        old,new=self.pair([field()],[field(),field('NewShotData',3)])
        result=compare_exports(old,new,cache_dir=self.root/'cache')
        self.assertTrue(result['new_information_found'])
        self.assertTrue(result['review_required'])
        self.assertEqual(result['source_changes']['added_fields']['total'],1)
        self.assertEqual(result['semantic_comparison']['status'],'unavailable')
        self.assertIsNone(result['replay_identity']['parser_identity']['before'])

    def test_removed_fields_typed_losses_and_quality_drift_are_explicit(self):
        old,new=self.pair([field(),field('Gone',2)],[field(val=None)],quality={'partial_rows':1})
        result=compare_inventories(inventory_export(old),inventory_export(new))
        self.assertEqual(result['removed_fields']['total'],1)
        self.assertEqual(result['typed_coverage_losses']['total'],1)
        self.assertEqual(result['quality_changes']['items'][0]['after'],1)

    def test_field_row_reordering_changes_bytes_but_not_inventory_counts(self):
        rows=[field(val=1),field(val=2),field(val=3)]
        old,new=self.pair(rows,list(reversed(rows)))
        result=compare_inventories(inventory_export(old),inventory_export(new))
        self.assertEqual(result['field_observation_changes']['total'],0)
        self.assertEqual(result['added_fields']['total'],0)
        self.assertTrue(result['table_changes']['items'][0]['content_changed'])

    def test_newly_typed_existing_field_counts_as_new_information(self):
        old,new=self.pair([field(val=None)],[field(val=0)])
        result=compare_exports(old,new,cache_dir=self.root/'cache')
        self.assertTrue(result['new_information_found'])
        self.assertEqual(result['source_changes']['added_fields']['total'],0)
        self.assertEqual(result['source_changes']['typed_coverage_gains']['total'],1)
        self.assertEqual(result['source_changes']['typed_coverage_losses']['total'],0)

    def test_column_type_changes_are_flagged(self):
        old,new=self.pair([field()],[field()])
        table=pq.read_table(new/'fields.parquet')
        index=table.schema.get_field_index('value_i64')
        table=table.set_column(index,'value_i64',table['value_i64'].cast(pa.string()))
        pq.write_table(table,new/'fields.parquet')
        result=compare_inventories(inventory_export(old),inventory_export(new))
        self.assertEqual(result['table_changes']['items'][0]['type_changes']['value_i64'],{'before':'int64','after':'string'})

    def test_new_tables_and_declarations_survive_detail_truncation(self):
        old,new=self.pair([field()],[field()])
        left,right=inventory_export(old),inventory_export(new)
        right['tables']['aaa']={'rows':1,'columns':{'x':'int32'},'sha256':'new'}
        right['tables']['zzz']={'rows':1,'columns':{'y':'int32'},'sha256':'new'}
        right['declarations'].append(json.dumps({'group':'tags','name':'NewFiringTag','checksum':0},sort_keys=True))
        result=compare_inventories(left,right,limit=1)
        self.assertEqual(result['table_changes']['total'],2)
        self.assertEqual(result['table_changes']['omitted'],1)
        self.assertTrue(result['table_changes']['new_information'])
        self.assertEqual(result['new_declarations']['total'],1)

    def test_different_or_unverifiable_replays_are_rejected(self):
        old,new=self.pair([field()],[field()],guid=[1,2,3,5])
        with self.assertRaises(QueryError) as caught:compare_exports(old,new)
        self.assertEqual(caught.exception.code,'different_replay')
        manifest=json.loads((new/'manifest.json').read_text());manifest.pop('guid')
        (new/'manifest.json').write_text(json.dumps(manifest))
        with self.assertRaises(QueryError) as caught:compare_exports(old,new)
        self.assertEqual(caught.exception.code,'unverifiable_replay_identity')

    def test_report_paths_and_existing_annotations_are_protected(self):
        old,new=self.pair([field()],[field()])
        existing=self.root/'notes.json';existing.write_text('keep')
        for output in (existing,old/'report.json',new/'report.json'):
            capture=io.StringIO()
            with redirect_stdout(capture):code=main(['--baseline',str(old),'--candidate',str(new),'--output',str(output)])
            self.assertEqual(code,1)
        self.assertEqual(existing.read_text(),'keep')
        self.assertFalse((old/'report.json').exists())
        self.assertFalse((new/'report.json').exists())
        with self.assertRaises(QueryError):compare_exports(old,new,cache_dir=old/'cache')

    def test_check_mode_requires_review_without_rewriting_baselines(self):
        old,new=self.pair([field()],[field(),field('New',1)])
        output=self.root/'report.json'
        capture=io.StringIO()
        with redirect_stdout(capture):code=main(['--baseline',str(old),'--candidate',str(new),'--output',str(output),'--check'])
        self.assertEqual(code,2)
        self.assertTrue(json.loads(output.read_text())['review_required'])
        self.assertEqual(pq.read_table(old/'fields.parquet').num_rows,1)


PRIVATE=Path(os.environ.get('VALCOACH_TEST_EXPORT_1308',ROOT/'output/replay-5c31718d/raw'))


@unittest.skipUnless((PRIVATE/'manifest.json').is_file(),'Second private replay export not available')
class ComparisonReferenceTests(unittest.TestCase):
    def test_same_export_has_no_drift_and_uses_shared_normalized_facts(self):
        result=compare_exports(PRIVATE,PRIVATE,limit=2)
        self.assertFalse(result['review_required'])
        self.assertFalse(result['new_information_found'])
        self.assertTrue(result['semantic_comparison']['equal'])
        self.assertGreater(result['semantic_comparison']['groups']['gunfire']['before'],0)
        self.assertGreater(result['semantic_comparison']['groups']['resources']['before'],0)
        self.assertIsNone(result['replay_identity']['parser_identity']['before'])


if __name__=='__main__':unittest.main()
