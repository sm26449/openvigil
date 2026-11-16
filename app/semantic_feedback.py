"""Exemple umane pentru un concept: memorie explicita, imagini pastrate si export PG.

Un verdict descrie potrivirea cu acel concept, nu clasa detectorului. Exemplele
confirmate trec de pragul semantic; cele respinse se exclud inainte de top-k.
Nu se modifica ponderile SigLIP si nu se declara corecte imagini noi prin analogie.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unicodedata

import db
import semantic_store as store

IMAGE_DIR = Path(__file__).resolve().parents[1] / 'data' / 'semantic-feedback'
_ready = False
_lock = threading.Lock()
SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_feedback (
    model TEXT NOT NULL,
    concept TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('detectie','scena')),
    cam INTEGER NOT NULL,
    day TEXT NOT NULL,
    fn TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('yes','no','unknown')),
    note TEXT NOT NULL DEFAULT '',
    reviewer TEXT NOT NULL,
    metadata JSONB NOT NULL,
    embedding vector(768) NOT NULL,
    image_id TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY(model,concept,source,cam,day,fn)
);
CREATE INDEX IF NOT EXISTS semantic_feedback_image ON semantic_feedback(image_id);
"""


def init():
    global _ready
    if _ready:
        return
    with _lock:
        if not _ready:
            db.aplica('semantic_feedback', SCHEMA)
            _ready = True


def canonical(text):
    if not isinstance(text,str) or len(text)>512:
        raise ValueError('concept invalid')
    value=''.join(c for c in unicodedata.normalize('NFKD',text.lower()) if not unicodedata.combining(c))
    value=' '.join(re.sub(r'[^\w\s-]',' ',value).split())
    if not value or len(value)>160:
        raise ValueError('concept invalid')
    return value


def query_concept(text):
    try:
        return canonical(text)
    except ValueError:
        return None


def asset_key(row):
    return (row.get('sursa') or row.get('source') or 'detectie',int(row['cam']),row['day'],row['fn'])


def validate_asset(asset):
    if not isinstance(asset,dict):
        raise ValueError('imagine invalida')
    source=asset.get('sursa') or asset.get('source') or 'detectie'
    day=asset.get('day');fn=asset.get('fn');cam=asset.get('cam')
    if source not in ('detectie','scena') or not isinstance(day,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day):
        raise ValueError('imagine invalida')
    from datetime import date
    date.fromisoformat(day)
    if not isinstance(fn,str) or not re.fullmatch(r'[\w.-]+\.jpg',fn) or len(fn)>240:
        raise ValueError('imagine invalida')
    if isinstance(cam,bool) or not isinstance(cam,int) or cam<=0:
        raise ValueError('camera invalida')
    return source,cam,day,fn


@contextmanager
def write_lock():
    IMAGE_DIR.mkdir(parents=True,exist_ok=True)
    with (IMAGE_DIR/'.write.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        try:
            yield lock
        finally:
            fcntl.flock(lock,fcntl.LOCK_UN)


def _save_image(image_id,data):
    fd,tmp=tempfile.mkstemp(prefix='.review-',dir=IMAGE_DIR)
    try:
        with os.fdopen(fd,'wb') as f:
            f.write(data);f.flush();os.fsync(f.fileno())
        os.replace(tmp,IMAGE_DIR/(image_id+'.jpg'))
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def _collect_image(image_id):
    with db.conn() as c:
        used=c.execute('SELECT 1 FROM semantic_feedback WHERE image_id=%s LIMIT 1',(image_id,)).fetchone()
    if not used:
        try:(IMAGE_DIR/(image_id+'.jpg')).unlink()
        except FileNotFoundError:pass


def save(text,asset,verdict,note,reviewer,only_if_missing=False):
    """Perechea imagine/vector e verificata sub aceleasi lock-uri ca indexarea."""
    import semantic
    concept=canonical(text);source,cam,day,fn=validate_asset(asset)
    if verdict not in ('yes','no','unknown') or not isinstance(note,str) or len(note)>500:
        raise ValueError('verdict sau observatie invalida')
    init();store.control()
    with semantic.storage_lock(),semantic._write_lock(day),write_lock():
        if only_if_missing:
            with db.conn() as c:
                existing=c.execute('SELECT verdict,note,image_id FROM semantic_feedback WHERE '
                    '(model,concept,source,cam,day,fn)=(%s,%s,%s,%s,%s,%s)',
                    (store.MODEL,concept,source,cam,day,fn)).fetchone()
            if existing:
                return {'concept':concept,'verdict':existing[0],'note':existing[1],'image_id':existing[2],'skipped':True}
        with db.conn() as c,c.cursor(binary=True) as cursor:
            row=cursor.execute('SELECT metadata,embedding FROM semantic_vectors WHERE '
                '(model,source,cam,day,fn)=(%s,%s,%s,%s,%s) AND NOT deleted AND embedding IS NOT NULL',
                (store.MODEL,source,cam,day,fn)).fetchone()
        if row is None:
            with db.conn() as c,c.cursor(binary=True) as cursor:
                cached=cursor.execute('SELECT metadata,embedding,fingerprint,image_id FROM semantic_feedback WHERE '
                    '(model,source,cam,day,fn)=(%s,%s,%s,%s,%s) ORDER BY updated_at DESC LIMIT 1',
                    (store.MODEL,source,cam,day,fn)).fetchone()
            if cached is None:raise FileNotFoundError('imaginea nu mai este in index')
            meta,embedding,version,cached_id=cached
            content=(IMAGE_DIR/(cached_id+'.jpg')).read_bytes()
        else:
            meta,embedding=row
            version=semantic.fingerprint(day,meta)
            if version!=meta.get('_fingerprint'):
                raise ValueError('imagine schimbata; reindexeaza inainte de evaluare')
            path=Path(semantic.source_path(day,meta))
            content=path.read_bytes()
            if not content or semantic.fingerprint(day,meta)!=version:
                raise ValueError('imagine schimbata sau goala')
        if asset.get('_fingerprint') is not None and asset['_fingerprint']!=version:
            raise ValueError('imagine schimbata; reincarca selectia')
        image_id=hashlib.sha256(json.dumps([store.MODEL,source,cam,day,fn,version]).encode()+content).hexdigest()
        _save_image(image_id,content)
        old_image=None
        with db.tx() as c:
            previous=c.execute('SELECT image_id FROM semantic_feedback WHERE '
                '(model,concept,source,cam,day,fn)=(%s,%s,%s,%s,%s,%s)',
                (store.MODEL,concept,source,cam,day,fn)).fetchone()
            if previous:old_image=previous[0]
            c.execute('INSERT INTO semantic_feedback(model,concept,title,source,cam,day,fn,fingerprint,verdict,note,reviewer,metadata,embedding,image_id) '
                'VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::vector,%s) '
                'ON CONFLICT(model,concept,source,cam,day,fn) DO UPDATE SET title=EXCLUDED.title,fingerprint=EXCLUDED.fingerprint, '
                'verdict=EXCLUDED.verdict,note=EXCLUDED.note,reviewer=EXCLUDED.reviewer,metadata=EXCLUDED.metadata, '
                'embedding=EXCLUDED.embedding,image_id=EXCLUDED.image_id,updated_at=clock_timestamp()',
                (store.MODEL,concept,text.strip(),source,cam,day,fn,version,verdict,note,reviewer,
                 json.dumps(meta,ensure_ascii=False),store.vector(embedding),image_id))
        if old_image and old_image!=image_id:_collect_image(old_image)
    return {'concept':concept,'verdict':verdict,'note':note,'image_id':image_id}


def forget(text,asset):
    concept=canonical(text);source,cam,day,fn=validate_asset(asset);init()
    with write_lock():
        with db.conn() as c:
            row=c.execute('DELETE FROM semantic_feedback WHERE (model,concept,source,cam,day,fn)=(%s,%s,%s,%s,%s,%s) RETURNING image_id',
                          (store.MODEL,concept,source,cam,day,fn)).fetchone()
        if row:_collect_image(row[0])
    return bool(row)


def image_path(image_id):
    if not re.fullmatch(r'[a-f0-9]{64}',image_id):raise FileNotFoundError
    init()
    with db.conn() as c:
        exists=c.execute('SELECT 1 FROM semantic_feedback WHERE image_id=%s LIMIT 1',(image_id,)).fetchone()
    path=IMAGE_DIR/(image_id+'.jpg')
    if not exists or not path.is_file():raise FileNotFoundError
    return path


def decisions(text):
    concept=query_concept(text)
    if concept is None:return {}
    init()
    with db.conn() as c:
        rows=c.execute('SELECT source,cam,day,fn,fingerprint,verdict,note FROM semantic_feedback WHERE model=%s AND concept=%s',
                       (store.MODEL,concept)).fetchall()
    return {(s,cam,day,fn):{'fingerprint':fp,'verdict':v,'note':n} for s,cam,day,fn,fp,v,n in rows}


def annotate(text,rows):
    saved=decisions(text)
    for row in rows:
        record=saved.get(asset_key(row))
        if record and record['fingerprint']==row.get('_fingerprint'):
            row.update(feedback=record['verdict'],feedback_note=record['note'],confirmed=record['verdict']=='yes')
    return rows


def positive_hits(text,value,since,until,visible=None,hours=None):
    concept=query_concept(text)
    if concept is None:return []
    init()
    where,args=store._where(since,until,visible,None,hours,())
    # Outer fields stay unambiguous; the EXISTS only names f.* explicitly.
    where+=" AND EXISTS(SELECT 1 FROM semantic_feedback f WHERE f.model=semantic_vectors.model AND f.concept=%s " \
           "AND (f.source,f.cam,f.day,f.fn)=(semantic_vectors.source,semantic_vectors.cam,semantic_vectors.day,semantic_vectors.fn) " \
           "AND f.fingerprint=semantic_vectors.metadata->>'_fingerprint' AND f.verdict='yes')"
    with db.conn() as c:
        rows=c.execute('SELECT day,metadata,-(embedding <#> %s::vector) FROM semantic_vectors WHERE '+where,
                       (store.vector(value),*args,concept)).fetchall()
    return [{**m,'day':d,'sursa':m.get('sursa') or 'detectie','score':float(score),'confirmed':True,'feedback':'yes'} for d,m,score in rows]


def stats(text):
    init();concept=canonical(text)
    with db.conn() as c:
        return dict(c.execute('SELECT verdict,count(*) FROM semantic_feedback WHERE model=%s AND concept=%s GROUP BY verdict',
                              (store.MODEL,concept)).fetchall())


def concepts():
    init()
    with db.conn() as c:
        rows=c.execute('SELECT concept,max(title),count(*) FROM semantic_feedback WHERE model=%s GROUP BY concept ORDER BY max(updated_at) DESC LIMIT 100',
                       (store.MODEL,)).fetchall()
    return [{'concept':key,'title':title,'examples':n} for key,title,n in rows]


def browse(text,mode='saved',day=None,cam=None,offset=0,limit=100):
    """Exemple pastrate sau arhiva cronologica, fara selectie dupa scor semantic."""
    init();concept=canonical(text)
    if mode=='saved':
        where='model=%s AND concept=%s';args=[store.MODEL,concept]
        if cam is not None:where+=' AND cam=%s';args.append(cam)
        query='SELECT day,metadata,verdict,note,image_id FROM semantic_feedback WHERE '+where+' ORDER BY updated_at DESC,source,cam,fn LIMIT %s OFFSET %s'
        with db.conn() as c:rows=c.execute(query,(*args,limit+1,offset)).fetchall()
        result=[{**m,'day':d,'sursa':m.get('sursa') or 'detectie','feedback':verdict,'feedback_note':note,
                 'confirmed':verdict=='yes','url':'/api/semantic/review/image/'+image_id} for d,m,verdict,note,image_id in rows[:limit]]
    elif mode=='archive':
        from datetime import date
        date.fromisoformat(day or '')
        where='model=%s AND day=%s AND NOT deleted AND embedding IS NOT NULL';args=[store.MODEL,day]
        if cam is not None:where+=' AND cam=%s';args.append(cam)
        with db.conn() as c:
            rows=c.execute('SELECT day,metadata FROM semantic_vectors WHERE '+where+' ORDER BY ts,source,cam,fn LIMIT %s OFFSET %s',
                           (*args,limit+1,offset)).fetchall()
        result=annotate(text,[{**m,'day':d,'sursa':m.get('sursa') or 'detectie'} for d,m in rows[:limit]])
    else:raise ValueError('mod invalid')
    return result,len(rows)>limit
