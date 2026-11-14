#!/usr/bin/env python3
"""Migrare semantica locala: prepare -> import -> activate; rollback exporta PG fara GPU.

Modelul colectiei legacy este declarat, nu dedus din dimensiunea vectorilor.
Bariera de storage opreste numai mutatiile indexului; capturile pot continua sa
intre in coada persistenta. Nicio comutare nu se face daca verificarea esueaza.
"""
import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'app')]
import semantic
import semantic_store as store
import settings


def clean(meta):
    return {k:v for k,v in meta.items() if not k.startswith('_')}


def expected(day,feedback):
    vectors,metas=semantic.shard_load(day)
    if vectors is None:
        return np.empty((0,768),np.float32),[],0
    if vectors.shape!=(len(metas),768):
        raise ValueError(f'{day}: dimensiune incompatibila')
    unique={semantic.asset_key(m):i for i,m in enumerate(metas)}
    selected=[];rows=[];skipped=0
    for k in sorted(unique):
        i=unique[k];m=semantic._current_meta(metas[i],feedback)
        if m is None:
            skipped+=1;continue
        try:
            fingerprint=semantic.fingerprint(day,m)
            if os.path.getsize(semantic.source_path(day,m))==0:
                skipped+=1;continue
        except FileNotFoundError:
            skipped+=1;continue
        rows.append({**m,'_fingerprint':fingerprint});selected.append(i)
    return vectors[selected],rows,skipped


def digest(vectors,metas):
    h=hashlib.sha256()
    h.update(np.asarray(vectors,dtype='<f4').tobytes())
    h.update(json.dumps(metas,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode())
    return h.hexdigest()


def prepare():
    cfg=store.control()
    if cfg['backend']!='files':
        raise ValueError('indexul PostgreSQL este deja activ')
    settings.update({'semantic_persistent':True})
    return {'persistent':True,**cfg}


def import_all():
    if not settings.get('semantic_persistent'):
        raise RuntimeError('ruleaza prepare inainte de import')
    if store.control()['backend']!='files':
        raise RuntimeError('importul din fisiere este permis numai inainte de comutare')
    feedback=semantic._feedback();rows=skipped=0
    for day in semantic.indexed_days():
        with semantic.storage_lock(),semantic._write_lock(day):
            mat,meta,ignored=expected(day,feedback)
            sha=digest(mat,meta)
            previous,count,present=store.import_checkpoint(day)
            resumed=previous==sha and count==present==len(meta)
            n=len(meta) if resumed else store.import_rows(day,mat,meta,semantic.normalize_label,sha)
            have={semantic.asset_key(m) for m in meta}
            for old in store.metadata(day):
                if semantic.asset_key(old) not in have:
                    store.remove(day,old)
        rows+=n;skipped+=ignored
        print(json.dumps({'day':day,'imported':n,'already_verified_import':resumed,'excluded_missing_or_neutral':ignored}),flush=True)
    import db
    for day,n in store.metadata():
        if day not in semantic.indexed_days():
            with semantic.storage_lock(),semantic._write_lock(day):
                if day not in semantic.indexed_days():
                    store.retire_day(day)
    with db.conn() as c:c.execute('VACUUM ANALYZE semantic_vectors')
    return {'rows_imported':rows,'excluded_missing_or_neutral':skipped,'model':store.MODEL}


def verify(locked=False):
    """Compara TOTI vectorii si metadatele, nu doar un esantion top-k."""
    reports=[];feedback=semantic._feedback();total=0
    with (nullcontext() if locked else semantic.storage_lock(exclusive=True)):
        pg_days={d for d,n in store.metadata()}
        for day in sorted(set(semantic.indexed_days())|pg_days):
            a,ma,ignored=expected(day,feedback)
            b,mb=store.export_day(day)
            same_meta=ma==mb
            same_vectors=a.shape==b.shape and np.array_equal(a,b)
            same=bool(same_meta and same_vectors)
            reports.append({'day':day,'file_rows':len(ma),'pg_rows':len(mb),'identical':same,'excluded':ignored,
                            'file_digest':digest(a,ma),'pg_digest':digest(b,mb)})
            total+=len(ma)
            print(json.dumps(reports[-1]),flush=True)
        if not all(d['identical'] for d in reports):
            raise RuntimeError('verificarea integrala a esuat; backendul nu a fost comutat')
    return {'rows_verified':total,'days':len(reports),'identical':True,'shards':reports}


def activate():
    if not settings.get('semantic_persistent'):
        raise RuntimeError('persistenta nu este activata')
    with semantic.storage_lock(exclusive=True):
        if store.control()['backend']!='files':
            raise RuntimeError('PostgreSQL este deja activ')
        result=verify(locked=True)
        store.set_backend('postgres',mirror=False)
    return {**result,'backend':'postgres','mirror':False}


def export(target=None,rollback=False):
    target=Path(target or semantic.SEM_DIR)
    target.mkdir(parents=True,exist_ok=True)
    total=0;days=[]
    with semantic.storage_lock(exclusive=True):
        for day,n in store.metadata():
            mat,meta=store.export_day(day)
            fd=semantic.SEM_DIR
            try:
                semantic.SEM_DIR=str(target)
                semantic._shard_write(day,mat,meta)
                restored,metadata=semantic.shard_load(day)
                if not np.array_equal(restored,mat) or metadata!=meta:
                    raise RuntimeError(f'{day}: exportul pentru revenire nu a fost verificat')
            finally:
                semantic.SEM_DIR=fd
            total+=n;days.append(day)
        if rollback:
            if target.resolve()!=Path(semantic.SEM_DIR).resolve():
                raise ValueError('rollback trebuie exportat in directorul activ al indexului')
            for day in set(semantic.indexed_days())-set(days):
                semantic._shard_write(day,np.empty((0,768),np.float32),[])
            store.set_backend('files',mirror=True)
    return {'days':len(days),'rows':total,'backend':'files' if rollback else store.control()['backend'],'exported_to':str(target)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','import','verify','activate','export','rollback','status'])
    p.add_argument('--output',type=Path,help='Raport JSON local')
    p.add_argument('--target',type=Path,help='Director pentru export; rollback foloseste directorul activ')
    args=p.parse_args();t=time.perf_counter()
    if args.action=='prepare':result=prepare()
    elif args.action=='import':result=import_all()
    elif args.action=='verify':result=verify()
    elif args.action=='activate':result=activate()
    elif args.action=='export':
        if not args.target:p.error('export cere --target')
        result=export(args.target)
    elif args.action=='rollback':result=export(rollback=True)
    else:result=store.stats()
    result['elapsed_s']=round(time.perf_counter()-t,2)
    if args.output:
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        os.chmod(args.output,0o600)
    print(json.dumps(result,ensure_ascii=False),flush=True)


if __name__=='__main__':main()
