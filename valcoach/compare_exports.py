"""Read-only parser-upgrade comparison of two exports of the same replay.

Inventories discover new source information; normalized fact multisets detect
changed meaning without comparing source row IDs or relying on row ordering.
This creates review evidence and never updates baselines or certifies a parser.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from .normalize import load_match
from .source import Export, QueryError


COMPARE_VERSION = 1
FACT_KEYS = {
    "players":("id","state_id","pawn","team","agent_class"),
    "rounds":("number","buy_ms","live_ms","end_ms","result_time_ms","result"),
    "damage":("time_ms","attacker","victim","actual_damage","raw_damage","lethal","weapon","weapon_actor","weapon_class","attribution","bone","wall_penetration","life_sections"),
    "timeline":("kind","time_ms","killer","victim","player","weapon"),
    "casts":("Player","Slot","Round","RoundPhase","CastTime","first_observed_ms"),
    "equipment":("time_ms","player","item","item_class","weapon","scope_round"),
    "resources":("time_ms","player","item","item_class","component","metric","value","status","scope_round","opened_ms","closed_ms"),
    "gunfire":("time_ms","player","item","item_class","item_kind","weapon","ammo_remaining","num_projectiles","burst_shot_number",
               "origin_cm","attack_vectors","firing_state","firing_player_state","tracer_option","yaw_switch","field_status","issues"),
    "team_roles":("team","time_ms","role"),
    "statuses":("player","component","category","effect_path","effect_id","source_id","start_ms","end_ms","scope_end_ms","severity"),
    "smokes":("actor","owner","class_path","label","start_ms","end_ms","scope_end_ms","opaque_start_ms","opaque_end_ms"),
}


def _json(value):
    return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False)


def _page(items,limit):
    return {"total":len(items),"items":items[:limit],"omitted":max(0,len(items)-limit)}


def _digest(path):
    result=hashlib.sha256()
    with path.open('rb') as stream:
        for part in iter(lambda:stream.read(1024*1024),b''):result.update(part)
    return result.hexdigest()


def _field_inventory(path):
    schema=pq.read_schema(path)
    required={'group_path','field_name'}
    if not required.issubset(schema.names):
        return {"status":"missing_columns","missing":sorted(required-set(schema.names)),"fields":{}}
    columns=[n for n in ('group_path','field_name','compatible_checksum','bit_count','value_i64','value_f64','value_str','value_bool') if n in schema.names]
    t=pq.read_table(path,columns=columns)
    typed=pa.array([False]*len(t))
    for column in ('value_i64','value_f64','value_str','value_bool'):
        if column in columns:typed=pc.or_(typed,pc.is_valid(t[column]))
    checksum=t['compatible_checksum'].cast(pa.int64()) if 'compatible_checksum' in columns else pa.nulls(len(t),pa.int64())
    bits=pc.fill_null(t['bit_count'].cast(pa.int64()),0) if 'bit_count' in columns else pa.array([0]*len(t),type=pa.int64())
    grouped=pa.table({'group':t['group_path'].cast(pa.string()),'name':t['field_name'].cast(pa.string()),'checksum':checksum,
                      'rows':pa.array([1]*len(t),type=pa.int64()),'typed':typed.cast(pa.int64()),'bits':bits})
    fields={}
    for r in grouped.group_by(['group','name','checksum']).aggregate([('rows','sum'),('typed','sum'),('bits','sum')]).to_pylist():
        identity={k:r[k] for k in ('group','name','checksum')}
        fields[_json(identity)]={**identity,'rows':r['rows_sum'],'typed_rows':r['typed_sum'],'declared_bits':r['bits_sum']}
    return {"status":"counted","fields":fields}


def inventory_export(root):
    root=Path(root).resolve()
    try:manifest=json.loads((root/'manifest.json').read_text(encoding='utf8'))
    except (OSError,ValueError) as exc:raise QueryError('invalid_manifest',str(exc)) from exc
    tables={}
    for path in sorted(root.glob('*.parquet')):
        file=pq.ParquetFile(path)
        tables[path.stem]={"rows":file.metadata.num_rows,"columns":{f.name:str(f.type) for f in file.schema_arrow},"sha256":_digest(path)}
    fields={name:_field_inventory(root/f'{name}.parquet') for name in ('fields','checkpoint_fields') if name in tables}
    declarations={_json({'group':g.get('path'),'name':f.get('name'),'checksum':f.get('compatible_checksum')})
                  for g in manifest.get('net_field_export_groups',[]) for f in g.get('fields',[])}
    return {"manifest":manifest,"tables":tables,"field_tables":fields,"declarations":sorted(declarations)}


def _scalars(value,prefix=''):
    if isinstance(value,dict):
        return {key:leaf for name,child in value.items() for key,leaf in _scalars(child,prefix+'.'+name if prefix else name).items()}
    return {prefix:value} if value is None or type(value) in (bool,int,float,str) else {}


def compare_inventories(before,after,limit=40):
    tables=[]
    for name in sorted(before['tables'].keys()|after['tables'].keys()):
        old,new=before['tables'].get(name),after['tables'].get(name)
        if old==new:continue
        oldcols=old['columns'] if old else {};newcols=new['columns'] if new else {}
        tables.append({'table':name,'before_rows':old['rows'] if old else None,'after_rows':new['rows'] if new else None,
                       'content_changed':not old or not new or old['sha256']!=new['sha256'],
                       'added_columns':sorted(newcols.keys()-oldcols.keys()),'removed_columns':sorted(oldcols.keys()-newcols.keys()),
                       'type_changes':{c:{'before':oldcols[c],'after':newcols[c]} for c in oldcols.keys()&newcols.keys() if oldcols[c]!=newcols[c]}})
    added,removed,changed,losses,gains=[],[],[],[],[]
    for table in sorted(before['field_tables'].keys()|after['field_tables'].keys()):
        old=before['field_tables'].get(table,{}).get('fields',{})
        new=after['field_tables'].get(table,{}).get('fields',{})
        for key in sorted(old.keys()|new.keys()):
            left,right=old.get(key),new.get(key)
            if left is None:added.append({'table':table,**right})
            elif right is None:removed.append({'table':table,**left})
            elif left!=right:
                item={'table':table,'identity':{k:left[k] for k in ('group','name','checksum')},
                      'before':{k:left[k] for k in ('rows','typed_rows','declared_bits')},'after':{k:right[k] for k in ('rows','typed_rows','declared_bits')}}
                changed.append(item)
                if right['typed_rows']*left['rows'] < left['typed_rows']*right['rows']:losses.append(item)
                if right['typed_rows']*left['rows'] > left['typed_rows']*right['rows']:gains.append(item)
    oldquality=_scalars(before['manifest'].get('quality'));newquality=_scalars(after['manifest'].get('quality'))
    quality=[{'counter':key,'before':oldquality.get(key),'after':newquality.get(key)}
             for key in sorted(oldquality.keys()|newquality.keys()) if oldquality.get(key)!=newquality.get(key) or (key in oldquality)!=(key in newquality)]
    newdecl=sorted(set(after['declarations'])-set(before['declarations']))
    olddecl=sorted(set(before['declarations'])-set(after['declarations']))
    table_changes={**_page(tables,limit),'new_information':any(t['added_columns'] or t['before_rows'] is None for t in tables)}
    return {'table_changes':table_changes,'added_fields':_page(added,limit),'removed_fields':_page(removed,limit),
            'field_observation_changes':_page(changed,limit),'typed_coverage_losses':_page(losses,limit),'typed_coverage_gains':_page(gains,limit),
            'new_declarations':_page([json.loads(v) for v in newdecl],limit),
            'removed_declarations':_page([json.loads(v) for v in olddecl],limit),'quality_changes':_page(quality,limit)}


def fact_multisets(model):
    records={name:model.get(name,[]) for name in ('players','rounds','damage','timeline','casts','equipment','team_roles')}
    records['equipment']=records['equipment']+model.get('checkpoint_state',{}).get('equipment',[])
    records['resources']=model.get('resources',{}).get('observations',[])
    records['gunfire']=model.get('gunfire',{}).get('observations',[])
    records['statuses']=model.get('utility',{}).get('statuses',[])
    records['smokes']=model.get('utility',{}).get('smokes',[])
    result={name:Counter(_json({key:r[key] for key in FACT_KEYS[name] if key in r}) for r in rows) for name,rows in records.items()}
    result['scoreboard']=Counter(_json({'pawn':pawn,'counters':{key:{k:v for k,v in value.items() if k!='source'} for key,value in counters.items()}})
                                for pawn,counters in model.get('scoreboard',{}).items())
    result['smoke_positions']=Counter(_json({'actor':s['actor'],'time_ms':p['time_ms'],'position_cm':p['position_cm']})
                                     for s in model.get('utility',{}).get('smokes',[]) for p in s.get('positions',[]))
    return result


def compare_facts(before,after,limit=40):
    left,right=fact_multisets(before),fact_multisets(after)
    groups={}
    for name in sorted(left):
        removed=left[name]-right[name];added=right[name]-left[name]
        groups[name]={'before':sum(left[name].values()),'after':sum(right[name].values()),
                      'removed':sum(removed.values()),'added':sum(added.values()),
                      'removed_examples':_page([{'fact':json.loads(key),'count':count} for key,count in sorted(removed.items())],limit),
                      'added_examples':_page([{'fact':json.loads(key),'count':count} for key,count in sorted(added.items())],limit)}
    return {'status':'compared','equal':all(not g['added'] and not g['removed'] for g in groups.values()),'groups':groups,
            'normalizer_schema':{'before':before.get('schema_version'),'after':after.get('schema_version')},
            'coverage_before':{'gunfire':before.get('gunfire',{}).get('coverage'),'checkpoints':before.get('checkpoint_state',{}).get('coverage',{}).get('status')},
            'coverage_after':{'gunfire':after.get('gunfire',{}).get('coverage'),'checkpoints':after.get('checkpoint_state',{}).get('coverage',{}).get('status')}}


def _identity(before,after):
    left,right=before['manifest'],after['manifest']
    first,second=left.get('guid'),right.get('guid')
    if any(not isinstance(guid,list) or len(guid)!=4 or not any(guid) or any(type(v) is not int or not 0<=v<=0xffffffff for v in guid) for guid in (first,second)):
        raise QueryError('unverifiable_replay_identity','Both manifests need a replay GUID; filenames alone do not establish the same input.')
    if first!=second or (left.get('source_size_bytes') is not None and right.get('source_size_bytes') is not None and left['source_size_bytes']!=right['source_size_bytes']):
        raise QueryError('different_replay','Compare exports of the same replay, rather than different matches.')
    return {'status':'matched_declared_guid_and_available_size','guid':first,
            'source_size_bytes':{'before':left.get('source_size_bytes'),'after':right.get('source_size_bytes')},
            'replay_build':{'before':left.get('replay_build'),'after':right.get('replay_build')},
            'duration_ms':{'before':left.get('duration_ms'),'after':right.get('duration_ms')},
            'parser_identity':{'before':left.get('parser_version',left.get('parser')),'after':right.get('parser_version',right.get('parser'))}}


def compare_exports(baseline,candidate,limit=40,cache_dir=None):
    if type(limit) is not int or not 1<=limit<=100:raise QueryError('invalid_pagination','Comparison detail limit must be 1-100.')
    roots=[Path(baseline).resolve(),Path(candidate).resolve()]
    cache=Path(cache_dir or Path.cwd()/'.valcoach-cache').resolve()
    if any(cache==root or root in cache.parents for root in roots):
        raise QueryError('invalid_cache','Keep comparison caches outside both raw exports.')
    before,after=inventory_export(roots[0]),inventory_export(roots[1])
    identity=_identity(before,after)
    source=compare_inventories(before,after,limit)
    try:
        left,_=load_match(Export(roots[0]),cache)
        right,_=load_match(Export(roots[1]),cache)
        facts=compare_facts(left,right,limit)
    except (QueryError,OSError,ValueError,KeyError,TypeError) as exc:
        facts={'status':'unavailable','equal':None,'error':{'code':getattr(exc,'code','normalization_failed'),'message':str(exc)}}
    source_changed=any(group['total'] for group in source.values())
    metadata_changed=any(identity[key]['before']!=identity[key]['after'] for key in ('replay_build','duration_ms','parser_identity'))
    return {'schema_version':1,'compare_version':COMPARE_VERSION,'query':'compare_exports','replay_identity':identity,
            'review_required':source_changed or metadata_changed or facts['equal'] is not True,
            'new_information_found':bool(source['added_fields']['total'] or source['typed_coverage_gains']['total']
                or source['new_declarations']['total'] or source['table_changes']['new_information']),
            'source_changes':source,'semantic_comparison':facts,
            'limitations':['This report does not run, install or certify a parser, and never rewrites reference expectations.',
                'Replay identity comes from declared container metadata, not an independently stored hash of the VRF input.',
                'An exporter version/checksum absent from the manifests remains unknown; the currently installed executable is not proof of who produced an old export.',
                'Source hashes/schema/counts flag movement changes; sampled spatial queries and coaching/visibility judgments are not semantically compared.',
                'New declared fields may be unused metadata. Added information needs interpretation and regression checks before support is claimed.']}


def main(argv=None):
    parser=argparse.ArgumentParser(description='Compare saved exports of the same replay before accepting a parser upgrade. No parser execution or downloads.')
    parser.add_argument('--baseline',required=True,type=Path)
    parser.add_argument('--candidate',required=True,type=Path)
    parser.add_argument('--output',type=Path,help='NEW JSON report outside both raw exports')
    parser.add_argument('--limit',type=int,default=40)
    parser.add_argument('--cache-dir',type=Path)
    parser.add_argument('--check',action='store_true',help='Return exit code 2 when the report requires review')
    args=parser.parse_args(argv)
    try:
        if args.output:
            out=args.output.resolve()
            if any(out==root.resolve() or root.resolve() in out.parents for root in (args.baseline,args.candidate)):
                raise QueryError('invalid_output','Keep the report outside both raw exports.')
            if out.exists():raise QueryError('output_exists','Choose a new report path; existing files are preserved.')
        result=compare_exports(args.baseline,args.candidate,args.limit,args.cache_dir)
        text=json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False)
        if args.output:
            args.output.parent.mkdir(parents=True,exist_ok=True)
            with args.output.open('x',encoding='utf8') as stream:stream.write(text+'\n')
            print(f'Wrote {args.output}',file=sys.stderr)
        else:
            if hasattr(sys.stdout,'reconfigure'):sys.stdout.reconfigure(encoding='utf8')
            print(text)
        return 2 if args.check and result['review_required'] else 0
    except (QueryError,OSError,ValueError,KeyError,TypeError) as exc:
        print(json.dumps({'schema_version':1,'error':{'code':getattr(exc,'code','comparison_failed'),'message':str(exc)}}))
        return 1


if __name__=='__main__':raise SystemExit(main())
