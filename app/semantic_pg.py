"""Prototip pgvector izolat: import fara GPU si cautare exacta, in baza de TEST.

Nu este backendul activ. Importul cere OPENVIGIL_TEST_DB=1; fisierele raman sursa live.
Modelul indexului vechi este declarat explicit, fiindca shard-urile nu au revizie istorica.
"""
import json
import os
import time

import numpy as np

import db
import semantic

SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_probe_vectors (
    model TEXT NOT NULL,
    source TEXT NOT NULL,
    cam INTEGER NOT NULL,
    day TEXT NOT NULL,
    fn TEXT NOT NULL,
    ts BIGINT NOT NULL,
    label TEXT NOT NULL,
    metadata JSONB NOT NULL,
    embedding vector(768) NOT NULL,
    PRIMARY KEY(model, source, cam, day, fn)
);
CREATE INDEX IF NOT EXISTS semantic_probe_period ON semantic_probe_vectors(model, ts);
CREATE INDEX IF NOT EXISTS semantic_probe_camera ON semantic_probe_vectors(model, cam, ts);
"""
MIGRARI = {}


def init():
    if os.environ.get('OPENVIGIL_TEST_DB') != '1':
        raise RuntimeError('prototipul semantic PG cere baza de TEST (OPENVIGIL_TEST_DB=1)')
    db.aplica('semantic_probe', SCHEMA, MIGRARI)


def _vector(v):
    v = np.asarray(v, dtype=np.float32)
    if v.shape != (768,) or not np.isfinite(v).all():
        raise ValueError('vector SigLIP invalid')
    return '[' + ','.join(str(float(x)) for x in v) + ']'


def import_shard(day, model):
    if not model:
        raise ValueError('identitatea modelului trebuie declarata explicit')
    init()
    vectors, rows = semantic.shard_load(day)
    if vectors is None:
        return 0
    if vectors.shape != (len(rows), 768) or not np.isfinite(vectors).all():
        raise ValueError('shard incompatibil cu SigLIP 768')
    if len(rows) and not np.allclose(np.linalg.norm(vectors,axis=1),1.,atol=.01):
        raise ValueError('vectori nenormalizati; import refuzat')
    unique = {semantic.asset_key(m): i for i,m in enumerate(rows)}
    with db.tx() as c:
        c.execute('CREATE TEMP TABLE semantic_probe_import (LIKE semantic_probe_vectors) ON COMMIT DROP')
        with c.cursor().copy('COPY semantic_probe_import FROM STDIN') as copy:
            for key,i in unique.items():
                m=rows[i]
                stamp=time.mktime(time.strptime(f"{day} {m['time']}",'%Y-%m-%d %H:%M:%S'))
                copy.write_row((model,key[0],key[1],day,key[2],int(stamp),semantic.normalize_label(m['label']),
                                json.dumps(m,ensure_ascii=False),_vector(vectors[i])))
        c.execute('INSERT INTO semantic_probe_vectors SELECT * FROM semantic_probe_import '
                  'ON CONFLICT (model,source,cam,day,fn) DO UPDATE SET ts=EXCLUDED.ts, '
                  'label=EXCLUDED.label, metadata=EXCLUDED.metadata, embedding=EXCLUDED.embedding')
    return len(unique)


def _where(model, since, until, visible, labels, hours):
    cond=['model=%s','ts >= %s','ts < %s']
    args=[model,since,until]
    if visible is not None:
        cond.append('cam=ANY(%s)');args.append(sorted(visible))
    if labels is not None:
        cond.append('label=ANY(%s)');args.append(sorted(set(labels)|semantic.GENERIC_LABELS))
    if hours is not None:
        # Ora locala originala; nu folosim timezone-ul configurat pe serverul PostgreSQL.
        minute="(substring(metadata->>'time',1,2)::int*60+substring(metadata->>'time',4,2)::int)"
        a,b=hours
        cond.append(f'({minute} >= %s AND {minute} < %s)' if a < b
                    else f'({minute} >= %s OR {minute} < %s)')
        args.extend([a,b])
    return ' AND '.join(cond),args


def search(vector, model, since, until, visible=None, labels=None, hours=None, limit=40, min_score=None):
    where,args=_where(model,since,until,visible,labels,hours)
    vector=_vector(vector)
    threshold=semantic.MIN_SCORE if min_score is None else min_score
    with db.conn() as c:
        rows=c.execute('SELECT day,metadata,-(embedding <#> %s::vector) AS score '
                       'FROM semantic_probe_vectors WHERE '+where+
                       ' AND (embedding <#> %s::vector) <= %s '
                       'ORDER BY score DESC,ts DESC,cam DESC,fn DESC LIMIT %s',
                       [vector,*args,vector,-threshold,max(0,int(limit))]).fetchall()
    return [{**m,'day':day,'score':float(score),'url':semantic.url_poza(day,m),
             'sursa':m.get('sursa') or 'detectie'} for day,m,score in rows]


def count(vector, model, since, until, visible=None, labels=None, hours=None, min_score=None):
    """Numara toate imaginile peste prag, fara aproximare/top-k; nu numara treceri."""
    where,args=_where(model,since,until,visible,labels,hours)
    threshold=semantic.MIN_SCORE if min_score is None else min_score
    with db.conn() as c:
        return c.execute('SELECT count(*) FROM semantic_probe_vectors WHERE '+where+
                         ' AND (embedding <#> %s::vector) <= %s',
                         [*args,_vector(vector),-threshold]).fetchone()[0]
