#!/usr/bin/env python3
"""Compara pgvector exact cu NumPy pe shard-uri inghetate, numai in PostgreSQL de test.

Fara GPU: interogarile sunt vectori ai unor imagini existente. Verifica stocarea si
filtrarea, nu intelegerea limbajului sau precizia vizuala. Nu comuta backendul live.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'app'))
import db
import semantic
import semantic_pg


def identity(r):
    return r['day'],r.get('sursa') or 'detectie',r['cam'],r['fn']


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True,help='Director de shard-uri COPIATE/inghetate')
    p.add_argument('--model-id',required=True,help='Identitate declarata; foloseste legacy/ pentru shard-uri fara provenienta')
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    semantic_pg.init()   # refuza productia inainte de orice scriere
    semantic.SEM_DIR=str(args.source)
    days=semantic.indexed_days()
    if not days:raise ValueError('corpus gol')
    before={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in args.source.iterdir() if x.suffix in {'.npy','.npz','.json'}}
    vectors,meta=semantic.shard_load(days[-1])
    queries=[vectors[i].copy() for i in sorted({0,len(vectors)//2,len(vectors)-1})]
    cameras=sorted({m['cam'] for m in meta})
    since=time.mktime(time.strptime(days[0],'%Y-%m-%d'))
    until=time.mktime(time.strptime(days[-1],'%Y-%m-%d'))+86400
    started=time.perf_counter()
    imported=0
    for day in days:
        n=semantic_pg.import_shard(day,args.model_id)
        imported+=n
        print(json.dumps({'import_day':day,'rows':n}),flush=True)
    import_ms=round((time.perf_counter()-started)*1000,1)
    with db.conn() as c:
        c.execute('ANALYZE semantic_probe_vectors')
        version=c.execute("SELECT extversion FROM pg_extension WHERE extname='vector'").fetchone()[0]
        size=c.execute("SELECT pg_total_relation_size('semantic_probe_vectors')").fetchone()[0]
    checks=[]
    for qi,query in enumerate(queries):
        semantic.embed_text=lambda _,q=query:q
        for visible,labels,hours in [(None,None,None),({cameras[0]},None,None),
                                     (set(),None,None),(None,{'car','truck','bus','motorcycle'},None),
                                     (None,None,[480,600])]:
            f={'visible':visible,'labels':labels,'hours':hours}
            # Doua repetari, ambele raportate; nu confundam cache-ul cu un benchmark rece.
            np_times=[];pg_times=[]
            for _ in range(2):
                t=time.perf_counter()
                hits=list(semantic._hits('probe',since=since,until=until,**f))
                best=sorted(hits,key=lambda r:(r['score'],r['day'],r['time'],r['cam'],r['fn']),reverse=True)[:40]
                np_times.append(round((time.perf_counter()-t)*1000,1))
                t=time.perf_counter()
                found=semantic_pg.search(query,args.model_id,since,until,**f)
                pg_times.append(round((time.perf_counter()-t)*1000,1))
            expected={identity(r):r['score'] for r in best}
            actual={identity(r):r['score'] for r in found}
            # Vecinii aproape egali la frontiera top-k pot avea ordine diferita in float32.
            min_score=min(expected.values(),default=0.)
            tied=all(score >= min_score-2e-6 for key,score in actual.items() if key not in expected)
            common=expected.keys() & actual.keys()
            scores_ok=all(abs(expected[k]-actual[k]) < 2e-6 for k in common)
            count=semantic_pg.count(query,args.model_id,since,until,**f)
            same=(expected.keys()==actual.keys() or (len(expected)==len(actual) and tied)) and scores_ok and count==len(hits)
            checks.append({'query_vector':qi,'visible':None if visible is None else sorted(visible),
                           'labels':None if labels is None else sorted(labels),'hours':hours,
                           'numpy_ms':np_times,'postgres_ms':pg_times,'numpy_matches':len(hits),
                           'postgres_matches':count,'top40_overlap':len(common),'equivalent':same})
            print(json.dumps(checks[-1]),flush=True)
    after={x.name:hashlib.sha256(x.read_bytes()).hexdigest() for x in args.source.iterdir() if x.suffix in {'.npy','.npz','.json'}}
    report={'model_id':args.model_id,'pgvector_version':version,'days':days,'imported':imported,
            'import_ms':import_ms,'relation_bytes':size,'frozen_corpus_unchanged':before==after,
            'checks':checks,'visual_accuracy_measured':False,'backend_switched':False}
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if before!=after or not all(c['equivalent'] for c in checks):raise SystemExit(1)


if __name__=='__main__':main()
