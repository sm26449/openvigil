"""Indexul semantic persistent si coada de indexare, PostgreSQL + pgvector.

Citirea se comuta explicit dupa import/validare. Identitatea legacy declara lipsa
reviziei istorice; o alta familie de model nu poate intra accidental in colectie.
Sarcinile au generatie si lease: rezultatele unui worker vechi nu suprascriu o
corectie sau o captura mai noua. Numai imaginile si modelele raman pe disc.
"""
import json
import threading
import time
import uuid

import numpy as np

import db

CHECKPOINT = 'google/siglip-base-patch16-256-multilingual'
MODEL = 'legacy/' + CHECKPOINT
_ready = False
_lock = threading.Lock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_control (
    id BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK (id),
    backend TEXT NOT NULL DEFAULT 'files' CHECK (backend IN ('files','postgres')),
    model TEXT NOT NULL,
    checkpoint TEXT NOT NULL,
    revision TEXT,
    mirror BOOLEAN NOT NULL DEFAULT TRUE,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS semantic_vectors (
    model TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('detectie','scena')),
    cam INTEGER NOT NULL,
    day TEXT NOT NULL,
    fn TEXT NOT NULL,
    ts BIGINT NOT NULL,
    label TEXT NOT NULL,
    metadata JSONB NOT NULL,
    embedding vector(768),
    deleted BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(model,source,cam,day,fn)
);
CREATE INDEX IF NOT EXISTS semantic_vector_time ON semantic_vectors(model,ts) WHERE NOT deleted;
CREATE INDEX IF NOT EXISTS semantic_vector_cam ON semantic_vectors(model,cam,ts) WHERE NOT deleted;
CREATE INDEX IF NOT EXISTS semantic_vector_day ON semantic_vectors(model,day) WHERE NOT deleted AND embedding IS NOT NULL;
CREATE TABLE IF NOT EXISTS semantic_tasks (
    model TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('detectie','scena')),
    cam INTEGER NOT NULL,
    day TEXT NOT NULL,
    fn TEXT NOT NULL,
    metadata JSONB NOT NULL,
    fingerprint TEXT NOT NULL,
    generation BIGINT NOT NULL DEFAULT 1,
    state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','running','done','failed','gone')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    lease_until TIMESTAMPTZ,
    lease_token TEXT,
    error TEXT,
    permanent BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(model,source,cam,day,fn)
);
CREATE INDEX IF NOT EXISTS semantic_task_claim ON semantic_tasks(model,state,available_at);
CREATE INDEX IF NOT EXISTS semantic_task_day ON semantic_tasks(model,day);
CREATE TABLE IF NOT EXISTS semantic_imports (
    model TEXT NOT NULL,
    day TEXT NOT NULL,
    digest TEXT NOT NULL,
    rows BIGINT NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(model,day)
);
"""
MIGRARI = {
    2: 'ALTER TABLE semantic_tasks ADD COLUMN IF NOT EXISTS permanent BOOLEAN NOT NULL DEFAULT FALSE',
    3: 'CREATE INDEX IF NOT EXISTS semantic_vector_day ON semantic_vectors(model,day) WHERE NOT deleted AND embedding IS NOT NULL; '
       'CREATE INDEX IF NOT EXISTS semantic_task_day ON semantic_tasks(model,day)',
}


def init():
    global _ready
    if _ready:
        return
    with _lock:
        if not _ready:
            db.aplica('semantic_storage', SCHEMA, MIGRARI)
            with db.conn() as c:
                c.execute('INSERT INTO semantic_control(id,model,checkpoint) VALUES(TRUE,%s,%s) '
                          'ON CONFLICT(id) DO NOTHING', (MODEL,CHECKPOINT))
            _ready = True


def reset():
    global _ready
    _ready = False


def control():
    init()
    with db.conn() as c:
        backend,model,checkpoint,mirror = c.execute(
            'SELECT backend,model,checkpoint,mirror FROM semantic_control WHERE id').fetchone()
    if model != MODEL or checkpoint != CHECKPOINT:
        raise RuntimeError('colectia semantica foloseste alt model; este necesara o migrare explicita')
    return {'backend':backend,'model':model,'checkpoint':checkpoint,'mirror':mirror}


def set_backend(backend, mirror=True):
    if backend not in {'files','postgres'}:
        raise ValueError('backend semantic invalid')
    control()
    with db.conn() as c:
        c.execute('UPDATE semantic_control SET backend=%s,mirror=%s,changed_at=clock_timestamp() WHERE id',
                  (backend,bool(mirror)))


def assert_revision(revision):
    if not revision:
        return
    control()
    with db.tx() as c:
        stored=c.execute('SELECT revision FROM semantic_control WHERE id FOR UPDATE').fetchone()[0]
        if stored and stored!=revision:
            raise RuntimeError('revizia SigLIP s-a schimbat; indexul nu poate amesteca vectori incompatibili')
        if not stored:
            c.execute('UPDATE semantic_control SET revision=%s WHERE id',(revision,))


def key(day, meta):
    source = meta.get('sursa') or 'detectie'
    if source not in {'detectie','scena'}:
        raise ValueError('sursa semantica invalida')
    return MODEL,source,int(meta['cam']),day,meta['fn']


def vector(value):
    value = np.asarray(value,dtype=np.float32)
    if value.shape != (768,) or not np.isfinite(value).all() or not np.isclose(np.linalg.norm(value),1.,atol=.01):
        raise ValueError('vector semantic invalid sau nenormalizat')
    return '['+','.join(str(float(x)) for x in value)+']'


def row(day, meta, value, label):
    return (*key(day,meta),int(time.mktime(time.strptime(f"{day} {meta['time']}",'%Y-%m-%d %H:%M:%S'))),
            label,json.dumps(meta,ensure_ascii=False),vector(value))


def import_rows(day, vectors, metas, normalize, digest):
    """Import idempotent sub lock-ul fisierului; tombstone-urile nu sunt reinviate."""
    control()
    vectors=np.asarray(vectors)
    if vectors.shape != (len(metas),768):
        raise ValueError('dimensiune incompatibila a shard-ului')
    if not np.isfinite(vectors).all() or (len(vectors) and not np.allclose(np.linalg.norm(vectors,axis=1),1.,atol=.01)):
        raise ValueError('shard cu vectori invalidi sau nenormalizati')
    unique={key(day,m):i for i,m in enumerate(metas)}
    with db.tx() as c:
        c.execute('CREATE TEMP TABLE semantic_import_stage '
                  '(model text, source text, cam int, day text, fn text, ts bigint, label text, metadata jsonb, embedding vector(768)) ON COMMIT DROP')
        from pgvector.psycopg import register_vector
        from psycopg.types.json import Jsonb
        register_vector(c)
        with c.cursor().copy('COPY semantic_import_stage FROM STDIN (FORMAT BINARY)') as cp:
            cp.set_types(['text','text','int4','text','text','int8','text','jsonb','vector'])
            for i in unique.values():
                m=metas[i]
                ts=int(time.mktime(time.strptime(f"{day} {m['time']}",'%Y-%m-%d %H:%M:%S')))
                cp.write_row((*key(day,m),ts,normalize(m['label']),Jsonb(m),vectors[i]))
        c.execute('INSERT INTO semantic_vectors(model,source,cam,day,fn,ts,label,metadata,embedding) '
                  'SELECT * FROM semantic_import_stage '
                  'ON CONFLICT(model,source,cam,day,fn) DO UPDATE SET ts=EXCLUDED.ts,label=EXCLUDED.label, '
                  'metadata=EXCLUDED.metadata,embedding=EXCLUDED.embedding,updated_at=clock_timestamp() '
                  'WHERE NOT semantic_vectors.deleted')
        c.execute('INSERT INTO semantic_imports(model,day,digest,rows) VALUES(%s,%s,%s,%s) '
                  'ON CONFLICT(model,day) DO UPDATE SET digest=EXCLUDED.digest,rows=EXCLUDED.rows,completed_at=clock_timestamp()',
                  (MODEL,day,digest,len(unique)))
    return len(unique)


def import_checkpoint(day):
    control()
    with db.conn() as c:
        r=c.execute('SELECT digest,rows FROM semantic_imports WHERE model=%s AND day=%s',(MODEL,day)).fetchone()
        n=c.execute('SELECT count(*) FROM semantic_vectors WHERE model=%s AND day=%s AND NOT deleted AND embedding IS NOT NULL',
                    (MODEL,day)).fetchone()[0]
    return (r[0],r[1],n) if r else (None,0,n)


def put(day, vectors, metas, normalize):
    if len(vectors)!=len(metas):
        raise ValueError('vectori si metadate cu lungimi diferite')
    control()
    with db.tx() as c:
        for m,v in zip(metas,vectors):
            c.execute('INSERT INTO semantic_vectors(model,source,cam,day,fn,ts,label,metadata,embedding) '
                      'VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::vector) '
                      'ON CONFLICT(model,source,cam,day,fn) DO UPDATE SET ts=EXCLUDED.ts,label=EXCLUDED.label, '
                      'metadata=EXCLUDED.metadata,embedding=EXCLUDED.embedding,deleted=FALSE,updated_at=clock_timestamp()',
                      row(day,m,v,normalize(m['label'])))


def enqueue(day, meta, fingerprint, force=False):
    """Un retry identic nu reseteaza un esec permanent; un fisier/verdict nou il reseteaza."""
    control()
    k=key(day,meta)
    with db.tx() as c:
        c.execute('INSERT INTO semantic_tasks(model,source,cam,day,fn,metadata,fingerprint,state) '
                  'VALUES(%s,%s,%s,%s,%s,%s::jsonb,%s, '
                  "CASE WHEN EXISTS(SELECT 1 FROM semantic_vectors WHERE (model,source,cam,day,fn)=(%s,%s,%s,%s,%s) "
                  "AND NOT deleted AND embedding IS NOT NULL AND metadata=%s::jsonb) THEN 'done' ELSE 'pending' END) "
                  'ON CONFLICT(model,source,cam,day,fn) DO UPDATE SET '
                  'metadata=EXCLUDED.metadata,fingerprint=EXCLUDED.fingerprint,generation=semantic_tasks.generation+1, '
                  "state='pending',attempts=0,error=NULL,permanent=FALSE,lease_token=NULL,lease_until=NULL,available_at=clock_timestamp(),updated_at=clock_timestamp() "
                  'WHERE semantic_tasks.fingerprint<>EXCLUDED.fingerprint OR semantic_tasks.metadata<>EXCLUDED.metadata '
                  "OR %s OR semantic_tasks.state='gone'", (*k,json.dumps(meta,ensure_ascii=False),fingerprint,
                                                         *k,json.dumps(meta,ensure_ascii=False),bool(force)))


def claim(limit=16, lease_seconds=300):
    control()
    token=uuid.uuid4().hex
    with db.tx() as c:
        rows=c.execute("WITH picked AS (SELECT model,source,cam,day,fn FROM semantic_tasks WHERE model=%s AND "
                       "((state='pending' AND available_at<=clock_timestamp()) OR (state='running' AND lease_until<clock_timestamp())) "
                       'ORDER BY available_at,day,cam,fn FOR UPDATE SKIP LOCKED LIMIT %s) '
                       "UPDATE semantic_tasks t SET state='running',attempts=t.attempts+1,lease_token=%s, "
                       "lease_until=clock_timestamp()+(%s * interval '1 second'),updated_at=clock_timestamp() "
                       'FROM picked p WHERE (t.model,t.source,t.cam,t.day,t.fn)=(p.model,p.source,p.cam,p.day,p.fn) '
                       'RETURNING t.day,t.metadata,t.fingerprint,t.generation,t.lease_token,t.attempts',
                       (MODEL,int(limit),token,int(lease_seconds))).fetchall()
    return [dict(day=d,meta=m,fingerprint=f,generation=g,token=t,attempts=a) for d,m,f,g,t,a in rows]


def _owned(c, task):
    return c.execute('SELECT 1 FROM semantic_tasks WHERE (model,source,cam,day,fn)=(%s,%s,%s,%s,%s) '
                     "AND generation=%s AND lease_token=%s AND state='running' FOR UPDATE",
                     (*key(task['day'],task['meta']),task['generation'],task['token'])).fetchone()


def finish(task, value, normalize, before_commit=None):
    """Vectorul si confirmarea sarcinii intra in ACEEASI tranzactie."""
    with db.tx() as c:
        if not _owned(c,task):
            return False
        if before_commit is not None:
            before_commit()
        m=task['meta'];day=task['day']
        c.execute('INSERT INTO semantic_vectors(model,source,cam,day,fn,ts,label,metadata,embedding) '
                  'VALUES(%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::vector) '
                  'ON CONFLICT(model,source,cam,day,fn) DO UPDATE SET ts=EXCLUDED.ts,label=EXCLUDED.label,metadata=EXCLUDED.metadata, '
                  'embedding=EXCLUDED.embedding,deleted=FALSE,updated_at=clock_timestamp()',row(day,m,value,normalize(m['label'])))
        c.execute("UPDATE semantic_tasks SET state='done',error=NULL,lease_until=NULL,lease_token=NULL,updated_at=clock_timestamp() "
                  'WHERE (model,source,cam,day,fn)=(%s,%s,%s,%s,%s)',key(day,m))
    return True


def fail(task, reason, permanent=False):
    with db.tx() as c:
        if not _owned(c,task):
            return False
        failed=permanent or task['attempts'] >= 5
        c.execute('UPDATE semantic_tasks SET state=%s,error=%s,permanent=%s,lease_until=NULL,lease_token=NULL, '
                  "available_at=clock_timestamp()+(%s * interval '1 second'),updated_at=clock_timestamp() "
                  'WHERE (model,source,cam,day,fn)=(%s,%s,%s,%s,%s)',
                  ('failed' if failed else 'pending',str(reason)[:300],bool(permanent),min(300,2**task['attempts']),*key(task['day'],task['meta'])))
    return True


def retry_failed(include_permanent=False):
    control()
    with db.conn() as c:
        return c.execute("UPDATE semantic_tasks SET state='pending',attempts=0,error=NULL,permanent=FALSE,generation=generation+1, "
                         'lease_token=NULL,lease_until=NULL,available_at=clock_timestamp() '
                         "WHERE model=%s AND state='failed' AND (%s OR NOT permanent)",
                         (MODEL,bool(include_permanent))).rowcount


def remove(day, meta):
    """Tombstone-ul impiedica importul unui shard vechi sa reintroduca o imagine stearsa."""
    control()
    k=key(day,meta)
    with db.tx() as c:
        c.execute('INSERT INTO semantic_vectors(model,source,cam,day,fn,ts,label,metadata,deleted) '
                  'VALUES(%s,%s,%s,%s,%s,0,%s,%s::jsonb,TRUE) '
                  'ON CONFLICT(model,source,cam,day,fn) DO UPDATE SET deleted=TRUE,embedding=NULL,updated_at=clock_timestamp()',
                  (*k,meta.get('label',''),json.dumps(meta,ensure_ascii=False)))
        c.execute("UPDATE semantic_tasks SET state='gone',generation=generation+1,lease_token=NULL,lease_until=NULL, "
                  "error=NULL,updated_at=clock_timestamp() WHERE (model,source,cam,day,fn)=(%s,%s,%s,%s,%s)",k)
    return True


def relabel(day, meta, normalize):
    control()
    with db.tx() as c:
        result=c.execute("UPDATE semantic_vectors SET label=%s,metadata=jsonb_set(metadata,'{label}',%s::jsonb),updated_at=clock_timestamp() "
                         'WHERE (model,source,cam,day,fn)=(%s,%s,%s,%s,%s) AND NOT deleted RETURNING fn',
                         (normalize(meta['label']),json.dumps(meta['label'],ensure_ascii=False),*key(day,meta))).fetchone()
        c.execute("UPDATE semantic_tasks SET metadata=jsonb_set(metadata,'{label}',%s::jsonb),generation=generation+1,state='pending',attempts=0, "
                  'lease_token=NULL,lease_until=NULL,error=NULL,available_at=clock_timestamp(),updated_at=clock_timestamp() '
                  "WHERE (model,source,cam,day,fn)=(%s,%s,%s,%s,%s) AND metadata->>'label'<>%s",
                  (json.dumps(meta['label'],ensure_ascii=False),*key(day,meta),meta['label']))
    return bool(result)


def metadata(day=None):
    control()
    with db.conn() as c:
        if day is not None:
            return [r[0] for r in c.execute('SELECT metadata FROM semantic_vectors WHERE model=%s AND day=%s AND NOT deleted AND embedding IS NOT NULL',
                                            (MODEL,day)).fetchall()]
        return c.execute('SELECT day,count(*) FROM semantic_vectors WHERE model=%s AND NOT deleted AND embedding IS NOT NULL GROUP BY day ORDER BY day',
                         (MODEL,)).fetchall()


def task_records(day):
    control()
    with db.conn() as c:
        return c.execute('SELECT metadata,fingerprint,state FROM semantic_tasks WHERE model=%s AND day=%s',(MODEL,day)).fetchall()


def existing(task):
    """Feedback-ul schimba eticheta, nu pixelii; refolosim vectorul aceleiasi versiuni."""
    from pgvector.psycopg import register_vector
    with db.conn() as c:
        register_vector(c)
        with c.cursor(binary=True) as cursor:
            r=cursor.execute('SELECT embedding FROM semantic_vectors WHERE (model,source,cam,day,fn)=(%s,%s,%s,%s,%s) '
                             "AND NOT deleted AND embedding IS NOT NULL AND metadata->>'_fingerprint'=%s",
                             (*key(task['day'],task['meta']),task['fingerprint'])).fetchone()
    return r[0] if r else None


def stats():
    cfg=control()
    with db.conn() as c:
        total,days=c.execute('SELECT count(*),count(DISTINCT day) FROM semantic_vectors WHERE model=%s AND NOT deleted AND embedding IS NOT NULL',(MODEL,)).fetchone()
        counts=dict(c.execute('SELECT state,count(*) FROM semantic_tasks WHERE model=%s GROUP BY state',(MODEL,)).fetchall())
        errors=c.execute("SELECT error,count(*) FROM semantic_tasks WHERE model=%s AND state='failed' GROUP BY error ORDER BY count(*) DESC LIMIT 5",(MODEL,)).fetchall()
    return {**cfg,'indexed':total,'days':days,'tasks':counts,'errors':[{'error':e,'count':n} for e,n in errors]}


def _where(since,until,visible,labels,hours,generic):
    cond=['model=%s','NOT deleted','embedding IS NOT NULL','ts>=%s','ts<%s'];args=[MODEL,since,until]
    if visible is not None:
        cond.append('cam=ANY(%s)');args.append(sorted(visible))
    if labels is not None:
        cond.append("(source<>'scena' OR label=ANY(%s))");args.append(sorted(set(labels)|set(generic)))
    if hours is not None:
        minute="(substring(metadata->>'time',1,2)::int*60+substring(metadata->>'time',4,2)::int)"
        a,b=hours
        cond.append(f'({minute}>=%s AND {minute}<%s)' if a<b else f'({minute}>=%s OR {minute}<%s)');args.extend([a,b])
    return ' AND '.join(cond),args


def hits(value,since,until,visible=None,labels=None,hours=None,limit=None,min_score=.055,generic=()):
    control()
    where,args=_where(since,until,visible,labels,hours,generic)
    literal=vector(value)
    query='SELECT day,metadata,-(embedding <#> %s::vector) AS score FROM semantic_vectors WHERE '+where+' AND embedding <#> %s::vector<=%s'
    params=[literal,*args,literal,-min_score]
    if limit is not None:
        query+=' ORDER BY score DESC,ts DESC,cam DESC,fn DESC LIMIT %s';params.append(max(0,int(limit)))
    with db.conn() as c:
        with c.transaction(),c.cursor(name='semantic_hits_'+uuid.uuid4().hex[:8]) as cursor:
            cursor.execute(query,params)
            for day,meta,score in cursor:
                yield {**meta,'day':day,'score':float(score),'sursa':meta.get('sursa') or 'detectie'}


def export_day(day):
    control()
    with db.conn() as c:
        from pgvector.psycopg import register_vector
        register_vector(c)
        with c.cursor(binary=True) as cursor:
            rows=cursor.execute('SELECT embedding,metadata FROM semantic_vectors WHERE model=%s AND day=%s AND NOT deleted '
                                'AND embedding IS NOT NULL ORDER BY source COLLATE "C",cam,fn COLLATE "C"',(MODEL,day)).fetchall()
    return (np.stack([v for v,m in rows])
            if rows else np.empty((0,768),np.float32)),[m for v,m in rows]


def expire(cutoff_day):
    control()
    with db.tx() as c:
        a=c.execute('DELETE FROM semantic_vectors WHERE model=%s AND day<%s',(MODEL,cutoff_day)).rowcount
        c.execute('DELETE FROM semantic_tasks WHERE model=%s AND day<%s',(MODEL,cutoff_day))
        c.execute('DELETE FROM semantic_imports WHERE model=%s AND day<%s',(MODEL,cutoff_day))
    return a


def retire_day(day):
    control()
    with db.tx() as c:
        n=c.execute('UPDATE semantic_vectors SET deleted=TRUE,embedding=NULL,updated_at=clock_timestamp() '
                    'WHERE model=%s AND day=%s AND NOT deleted',(MODEL,day)).rowcount
        c.execute("UPDATE semantic_tasks SET state='gone',generation=generation+1,lease_token=NULL,lease_until=NULL "
                  'WHERE model=%s AND day=%s',(MODEL,day))
    return n
