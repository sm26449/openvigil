"""Contracte reale PG: lease, generatie, commit atomic, import si cautare fara fisiere."""
from datetime import datetime
import os

import numpy as np
import pytest
from PIL import Image

import db
import semantic
import semantic_store as store
import importlib.util
from pathlib import Path

spec=importlib.util.spec_from_file_location('semantic_migrate',Path(__file__).parents[1]/'scripts/semantic_migrate.py')
migrate=importlib.util.module_from_spec(spec);spec.loader.exec_module(migrate)

DAY='2026-11-13'


@pytest.fixture
def persistent_index(tmp_path,monkeypatch):
    store.reset();store.init()
    with db.conn() as c:
        c.execute('TRUNCATE semantic_vectors,semantic_tasks,semantic_imports,semantic_control')
    store.reset();store.init()
    monkeypatch.setattr(semantic,'SEM_DIR',str(tmp_path/'index'))
    monkeypatch.setattr(semantic,'SNAP_DIR',str(tmp_path/'snapshots'))
    monkeypatch.setattr(semantic,'SCENA_DIR',str(tmp_path/'scene'))
    monkeypatch.setattr(semantic,'persistent',lambda:True)
    monkeypatch.setattr(semantic,'IS_HOST',True)
    monkeypatch.setattr(semantic,'WORKER_READY',False)
    monkeypatch.setattr(semantic,'_scene_assets',lambda:iter(()))
    monkeypatch.setattr(semantic,'_feedback',lambda:{})
    q=np.zeros(768,np.float32);q[0]=1
    monkeypatch.setattr(semantic,'embed_text',lambda _:q)
    monkeypatch.setattr(semantic,'_embed_masurat',lambda imgs:np.stack([q]*len(imgs)))
    with semantic._hints_lock:semantic._hints.clear()
    yield tmp_path,q
    with db.conn() as c:c.execute('TRUNCATE semantic_vectors,semantic_tasks,semantic_imports,semantic_control')
    store.reset()


def photo(tmp_path,cam=1,label='car',fn=None):
    fn=fn or f'100000_cam{cam}_{label}.jpg'
    path=tmp_path/'snapshots'/DAY/fn;path.parent.mkdir(parents=True,exist_ok=True)
    Image.new('RGB',(4,4),'white').save(path)
    return {'fn':fn,'cam':cam,'label':label,'time':'10:00:00'}


def period():return dict(since=datetime(2026,11,13).timestamp(),until=datetime(2026,11,14).timestamp())


def test_queue_survives_restart_and_expired_lease_is_fenced(persistent_index):
    tmp,q=persistent_index;m=photo(tmp)
    semantic._catalog(DAY,m)
    old=store.claim()[0]
    store.reset();store.init()
    assert store.claim()==[]
    with db.conn() as c:c.execute("UPDATE semantic_tasks SET lease_until=clock_timestamp()-interval '1 second'")
    new=store.claim()[0]
    assert new['token']!=old['token']
    assert store.finish(old,q,semantic.normalize_label) is False
    assert store.finish(new,q,semantic.normalize_label) is True
    assert store.stats()['tasks']=={'done':1}


def test_new_file_generation_rejects_old_result(persistent_index):
    tmp,q=persistent_index;m=photo(tmp)
    semantic._catalog(DAY,m);old=store.claim()[0]
    path=tmp/'snapshots'/DAY/m['fn'];st=path.stat();os.utime(path,ns=(st.st_atime_ns,st.st_mtime_ns+1000))
    semantic._catalog(DAY,m)
    assert store.finish(old,q,semantic.normalize_label) is False
    current=store.claim()[0]
    assert current['generation']==old['generation']+1
    assert store.finish(current,q,semantic.normalize_label)


def test_sql_commit_failure_does_not_ack_task(persistent_index):
    tmp,q=persistent_index;m=photo(tmp);semantic._catalog(DAY,m);task=store.claim()[0]
    def fail():raise OSError('mirror unavailable')
    with pytest.raises(OSError):store.finish(task,q,semantic.normalize_label,before_commit=fail)
    assert store.stats()['indexed']==0 and store.stats()['tasks']=={'running':1}
    assert store.fail(task,'retry')
    assert store.stats()['tasks']=={'pending':1}


def test_worker_writes_file_and_pg_then_primary_pg_needs_no_index_files(persistent_index,monkeypatch):
    tmp,q=persistent_index;m=photo(tmp)
    semantic._catalog(DAY,m)
    assert semantic._process_persistent()
    assert len(semantic.shard_meta(DAY))==1 and store.stats()['indexed']==1
    store.set_backend('postgres',mirror=False)
    monkeypatch.setattr(semantic,'shard_load',lambda _:pytest.fail('PG search read a shard'))
    r=semantic.search('x',**period())
    assert len(r)==1 and r[0]['cam']==1 and r[0]['fn']==m['fn']
    assert semantic.coverage(**period())['indexed']==1
    assert semantic.status()['backend']=='postgres'
    assert semantic.search('x',visible=set(),**period())==[]


def test_worker_does_not_recompute_precalculated_vector(persistent_index,monkeypatch):
    tmp,q=persistent_index;m=photo(tmp)
    semantic._catalog(DAY,m,q)
    monkeypatch.setattr(semantic,'_embed_masurat',lambda _:pytest.fail('recomputed ready embedding'))
    semantic._process_persistent()
    assert store.stats()['tasks']=={'done':1}


def test_failed_image_is_not_retried_until_source_changes(persistent_index):
    tmp,q=persistent_index;m=photo(tmp);path=tmp/'snapshots'/DAY/m['fn'];path.write_bytes(b'')
    semantic._catalog(DAY,m);semantic._process_persistent()
    assert store.stats()['tasks']=={'failed':1}
    assert store.retry_failed()==0
    semantic._catalog(DAY,m)
    assert store.claim()==[]
    Image.new('RGB',(4,4),'blue').save(path)
    semantic._catalog(DAY,m);semantic._process_persistent()
    assert store.stats()['tasks']=={'done':1}


def test_binary_import_is_idempotent_and_deleted_rows_stay_deleted(persistent_index):
    tmp,q=persistent_index;m=photo(tmp)
    store.import_rows(DAY,np.stack([q]),[m],semantic.normalize_label,'digest')
    store.import_rows(DAY,np.stack([q]),[m],semantic.normalize_label,'digest')
    assert store.stats()['indexed']==1
    vectors,rows=store.export_day(DAY)
    np.testing.assert_array_equal(vectors,np.stack([q]))
    assert rows==[m]
    store.remove(DAY,m)
    store.import_rows(DAY,np.stack([q]),[m],semantic.normalize_label,'digest')
    assert store.stats()['indexed']==0


def test_feedback_cancels_inflight_scene_and_prevents_resurrection(persistent_index):
    tmp,q=persistent_index
    crop=tmp/'scene'/'1'/'20261113-100000-1.jpg';crop.parent.mkdir(parents=True);Image.new('RGB',(4,4)).save(crop)
    m=semantic._scene_meta(1,crop.name,'car')
    semantic._catalog(DAY,m);task=store.claim()[0]
    semantic.actualizeaza_scena(1,crop.name,'nimic')
    assert store.finish(task,q,semantic.normalize_label) is False
    assert store.stats()['indexed']==0 and store.stats()['tasks']=={'gone':1}


def test_camera_and_time_filter_before_limit(persistent_index):
    tmp,q=persistent_index
    rows=[photo(tmp,cam=i) for i in (1,2)]
    store.import_rows(DAY,np.stack([q,q]),rows,semantic.normalize_label,'x')
    store.set_backend('postgres',mirror=False)
    result=semantic.search('x',visible={2},limit=1,**period())
    assert len(result)==1 and result[0]['cam']==2
    assert semantic.search('x',visible={2},hours=[660,720],**period())==[]


def test_checkpoint_and_revision_are_explicit(persistent_index):
    store.assert_revision('commit-a')
    with pytest.raises(RuntimeError):store.assert_revision('commit-b')
    with db.conn() as c:c.execute("UPDATE semantic_control SET checkpoint='other'")
    with pytest.raises(RuntimeError):store.control()


def test_migrate_activate_export_and_rollback_without_gpu(persistent_index,monkeypatch):
    tmp,q=persistent_index;m=photo(tmp)
    semantic._file_add(DAY,np.stack([q]),[m])
    monkeypatch.setattr(migrate.settings,'get',lambda _:True)
    assert migrate.import_all()['rows_imported']==1
    with monkeypatch.context() as patch:
        patch.setattr(store,'import_rows',lambda *a:pytest.fail('reimported unchanged day'))
        assert migrate.import_all()['rows_imported']==1
    assert migrate.verify()['rows_verified']==1
    assert migrate.activate()['backend']=='postgres'
    assert len(semantic.search('x',**period()))==1
    exported=migrate.export(tmp/'export')
    assert exported['rows']==1
    migrate.export(rollback=True)
    assert store.control()['backend']=='files'
    np.testing.assert_array_equal(semantic.shard_load(DAY)[0],np.stack([q]))


def test_activation_refuses_incomplete_import(persistent_index,monkeypatch):
    tmp,q=persistent_index;m=photo(tmp)
    semantic._file_add(DAY,np.stack([q]),[m])
    monkeypatch.setattr(migrate.settings,'get',lambda _:True)
    with pytest.raises(RuntimeError):migrate.activate()
    assert store.control()['backend']=='files'


def test_migration_order_matches_python_with_mixed_case_filenames(persistent_index,monkeypatch):
    tmp,q=persistent_index
    second=np.roll(q,1)
    rows=[photo(tmp,label='car'),photo(tmp,label='VMD')]
    semantic._file_add(DAY,np.stack([q,second]),rows)
    monkeypatch.setattr(migrate.settings,'get',lambda _:True)
    migrate.import_all()
    expected,metadata,_=migrate.expected(DAY,{})
    actual,stored=store.export_day(DAY)
    assert [semantic.asset_key(m) for m in stored]==sorted(semantic.asset_key(m) for m in rows)
    assert metadata==stored
    np.testing.assert_array_equal(expected,actual)
    assert migrate.verify()['rows_verified']==2


def test_reconciliation_leaves_expired_shards_to_bulk_retention(persistent_index,monkeypatch):
    monkeypatch.setattr(semantic,'indexed_days',lambda:['2000-01-01'])
    monkeypatch.setattr(semantic,'shard_meta',lambda _:pytest.fail('expired day scanned per asset'))
    monkeypatch.setattr(semantic.settings,'get',lambda _:90)
    assert semantic._reconcile_persistent()==0


def test_binary_backup_restore_and_failed_restore_are_atomic(persistent_index,monkeypatch):
    import backup_nocturn as bn
    tmp,q=persistent_index;m=photo(tmp)
    semantic._catalog(DAY,m);semantic._process_persistent()
    second=photo(tmp,cam=2);semantic._catalog(DAY,second);store.claim()
    monkeypatch.setattr(bn,'KEY_FILE',str(tmp/'backup_key'))
    data=tmp/'data';data.mkdir()
    backup=bn.creeaza(dest_dir=str(tmp/'backup'),data_dir=str(data),fete=False)
    assert 'pg/semantic_vectors.bin' in backup['continut']
    archive=tmp/'backup'/backup['fisier']
    restored=tmp/'restored'
    bn.restaureaza(str(archive),bn.cheie(),str(restored),backup)
    with db.conn() as c:c.execute('TRUNCATE semantic_vectors,semantic_tasks')
    bn.incarca_pg(str(restored))
    np.testing.assert_array_equal(store.export_day(DAY)[0],np.stack([q]))
    assert store.stats()['tasks']=={'done':1,'pending':1}
    binary=restored/'pg'/'semantic_vectors.bin'
    binary.write_bytes(binary.read_bytes()[:-10])
    with pytest.raises(Exception):bn.incarca_pg(str(restored))
    assert store.stats()['indexed']==1


def test_retention_removes_vectors_and_tasks(persistent_index,monkeypatch):
    tmp,q=persistent_index;m=photo(tmp)
    semantic._catalog(DAY,m);semantic._process_persistent()
    assert store.expire('2026-11-14')==1
    assert store.stats()['indexed']==0 and store.stats()['tasks']=={}


def test_transient_failure_can_be_explicitly_retried(persistent_index):
    tmp,q=persistent_index;m=photo(tmp);semantic._catalog(DAY,m)
    for i in range(5):
        task=store.claim()[0];assert store.fail(task,'GPU busy')
        with db.conn() as c:c.execute('UPDATE semantic_tasks SET available_at=clock_timestamp()')
    assert store.stats()['tasks']=={'failed':1}
    assert store.retry_failed()==1
    assert not store.finish(task,q,semantic.normalize_label)
    semantic._process_persistent()
    assert store.stats()['tasks']=={'done':1}


def test_bad_image_does_not_block_other_tasks(persistent_index,monkeypatch):
    tmp,q=persistent_index
    a=photo(tmp,cam=1);b=photo(tmp,cam=2)
    semantic._catalog(DAY,a);semantic._catalog(DAY,b)
    original=Image.open
    def open_image(path,*args,**kw):
        if str(path).endswith(a['fn']):raise Image.DecompressionBombError('oversized image')
        return original(path,*args,**kw)
    monkeypatch.setattr(Image,'open',open_image)
    semantic._process_persistent()
    assert store.stats()['tasks']=={'done':1,'failed':1}
