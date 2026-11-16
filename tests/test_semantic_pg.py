"""Contractul prototipului PG: import idempotent, izolare model, filtre inainte de LIMIT."""
from datetime import datetime

import numpy as np
import pytest

import db
import semantic
import semantic_pg


@pytest.fixture
def pg_index(tmp_path,monkeypatch):
    semantic_pg.init()
    with db.conn() as c:c.execute('TRUNCATE semantic_probe_vectors')
    monkeypatch.setattr(semantic,'SEM_DIR',str(tmp_path/'semantic'))
    q=np.zeros(768,np.float32);q[0]=1
    other=np.zeros(768,np.float32);other[1]=1
    rows=[{'fn':f'{i}.jpg','cam':cam,'time':'10:00:00','label':label}
          for i,(cam,label) in enumerate([(1,'car'),(2,'mașină'),(2,'VMD'),(2,'chicken')])]
    semantic.shard_add('2026-11-13',np.stack([q,q,q,other]),rows)
    yield q,datetime(2026,11,13).timestamp(),datetime(2026,11,14).timestamp()
    with db.conn() as c:c.execute('TRUNCATE semantic_probe_vectors')


def test_import_and_filter_before_limit(pg_index):
    q,a,b=pg_index
    assert semantic_pg.import_shard('2026-11-13','test/model') == 4
    semantic_pg.import_shard('2026-11-13','test/model')
    assert semantic_pg.count(q,'test/model',a,b) == 3
    found=semantic_pg.search(q,'test/model',a,b,visible={2},labels={'car'},limit=1)
    assert len(found) == 1 and found[0]['cam'] == 2
    assert semantic_pg.count(q,'test/model',a,b,visible={2},labels={'car'}) == 2
    assert semantic_pg.search(q,'other/model',a,b) == []
    assert semantic_pg.search(q,'test/model',a,b,visible=set()) == []
    assert semantic_pg.count(q,'test/model',a,b,hours=[660,720]) == 0


def test_reject_bad_vectors_without_partial_import(pg_index):
    q,a,b=pg_index
    with pytest.raises(ValueError):semantic_pg.import_shard('2026-11-13','')
    semantic._shard_write('2026-11-13',np.zeros((1,768)),[{'fn':'bad.jpg','cam':1,'time':'10:00:00','label':'car'}])
    with pytest.raises(ValueError):semantic_pg.import_shard('2026-11-13','test/model')
    assert semantic_pg.count(q,'test/model',a,b) == 0


def test_prototype_refuses_production(monkeypatch):
    monkeypatch.delenv('OPENVIGIL_TEST_DB',raising=False)
    with pytest.raises(RuntimeError):semantic_pg.init()
