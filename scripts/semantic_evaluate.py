#!/usr/bin/env python3
"""Exporta candidati pentru evaluare vizuala si masoara doar rezultatele etichetate.

Foloseste modelul deja incarcat in serviciul AI local, fara o a doua instanta GPU.
Identitate asset: zi|sursa|camera|fisier. Nu inventeaza ground truth din scoruri.
"""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlparse

import requests


def asset_id(row):
    return '|'.join(str(x) for x in (row['day'],row.get('sursa') or 'detectie',row['cam'],row['fn']))


def measure(case, results):
    positive=set(case.get('positive_assets',[]))
    negative=set(case.get('negative_assets',[]))
    if positive & negative:raise ValueError('acelasi asset este etichetat pozitiv si negativ')
    ids=[asset_id(r) for r in results]
    if not case.get('reviewed'):
        return {'status':'pending_labels','precision_at_k':None,'known_positive_recall_at_k':None}
    judged=positive|negative
    return {'status':'reviewed' if all(i in judged for i in ids) else 'partially_judged',
            'precision_at_k':sum(i in positive for i in ids)/len(ids) if ids and all(i in judged for i in ids) else None,
            'known_positive_recall_at_k':len(positive & set(ids))/len(positive) if positive else None,
            'unjudged_results':sum(i not in judged for i in ids)}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--days',type=int,default=30)
    parser.add_argument('--limit',type=int,default=10)
    parser.add_argument('--ai-url',default='http://127.0.0.1:8077')
    parser.add_argument('--token-file',type=Path,default=Path('/opt/openvigil/data/ai_token'))
    args=parser.parse_args()
    if urlparse(args.ai_url).hostname not in {'127.0.0.1','localhost','::1'}:
        raise ValueError('serviciul de evaluare trebuie sa fie local')
    if not 1 <= args.days <= 90 or not 1 <= args.limit <= 100:raise ValueError('interval invalid')
    token=args.token_file.read_text().strip()
    manifest=json.loads(args.manifest.read_text())
    output=[]
    for case in manifest['queries']:
        t=time.perf_counter()
        response=requests.post(args.ai_url.rstrip('/')+'/semantic/search',
                               json={'token':token,'q':case['q'],'days':args.days,'limit':args.limit},timeout=120)
        response.raise_for_status()
        results=response.json()['results']
        row={'query':case['q'],'elapsed_ms':round((time.perf_counter()-t)*1000),
             'metrics':measure(case,results),
             'candidates':[{**r,'asset_id':asset_id(r)} for r in results]}
        output.append(row)
        # Salvam progresul dupa fiecare intrebare; fisierul e local, fara jeton.
        args.output.write_text(json.dumps({'days':args.days,'limit':args.limit,'queries':output},ensure_ascii=False,indent=2))
        print(json.dumps({'query':case['q'],'metrics':row['metrics'],'candidates':len(results)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
