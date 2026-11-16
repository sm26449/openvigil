"""Regresiile auditului semantic: timp, drepturi, persistenta si acoperire."""
import io
import json
import queue
from datetime import datetime

import numpy as np
import pytest
from PIL import Image

import ask
import semantic
import search_journal
import search_time


@pytest.fixture
def index(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic, 'SEM_DIR', str(tmp_path/'semantic'))
    monkeypatch.setattr(semantic, 'SNAP_DIR', str(tmp_path/'snapshots'))
    monkeypatch.setattr(semantic, 'SCENA_DIR', str(tmp_path/'scena'))
    monkeypatch.setattr(semantic, '_scene_assets', lambda: iter(()))
    monkeypatch.setattr(semantic, '_queue_scena', queue.Queue(maxsize=512))
    monkeypatch.setattr(semantic, 'backfill_state', {'running': False, 'done': 0, 'total': 0, 'failed': 0, 'error': None})
    monkeypatch.setattr(semantic, 'embed_text', lambda _: np.array([1., 0.], np.float32))
    monkeypatch.setattr(semantic, '_embed_masurat', lambda imgs: np.array([[1., 0.]]*len(imgs), np.float32))
    return tmp_path


def meta(fn='a.jpg', cam=1, label='car', hour='10:00:00'):
    return {'fn': fn, 'cam': cam, 'label': label, 'time': hour}


def window():
    return {'since': datetime(2026,11,12).timestamp(), 'until': datetime(2026,11,14).timestamp()}


def test_permissions_before_limit(index):
    semantic.shard_add('2026-11-13', [[1.,0.], [.9,.1]], [meta(), meta('b.jpg',2)])
    hits = semantic.search('x', limit=1, visible={2}, **window())
    assert len(hits) == 1 and hits[0]['cam'] == 2
    assert semantic.search('x', visible=set(), **window()) == []


def test_old_shard_not_returned_for_today(index):
    semantic.shard_add('2000-01-01', [[1.,0.]], [meta()])
    assert semantic.search('x', days=1) == []


def test_vehicle_filter_accepts_translated_and_generic_labels(index):
    semantic.shard_add('2026-11-13', [[1.,0.]]*4,
                       [meta('a.jpg',label='mașină'), meta('b.jpg',label='VMD'),
                        meta('c.jpg',label='truck'),{**meta('d.jpg',label='chicken'),'sursa':'scena'}])
    groups, _ = semantic.appearances('x', labels=ask.VEHICLE_LABELS, **window())
    assert sum(g['poze'] for g in groups) == 3


def test_whole_frame_label_does_not_hide_other_objects(index):
    semantic.shard_add('2026-11-13', [[1.,0.]], [meta(label='person')])
    groups,_=semantic.appearances('masina de gunoi',labels=ask.VEHICLE_LABELS,**window())
    assert len(groups)==1 and groups[0]['poze']==1


def test_upsert_deduplicates_and_updates(index):
    semantic.shard_add('2026-11-13', [[1.,0.]], [meta()])
    semantic.shard_add('2026-11-13', [[0.,1.]], [meta(label='truck')])
    mat, rows = semantic.shard_load('2026-11-13')
    assert len(rows) == 1 and rows[0]['label'] == 'truck'
    np.testing.assert_array_equal(mat, [[0.,1.]])


def test_atomic_commit_survives_interrupted_write(index, monkeypatch):
    semantic.shard_add('2026-11-13', [[1.,0.]], [meta()])
    original = semantic.os.replace
    observed = []
    def replace(src,dst):
        mat, rows = semantic.shard_load('2026-11-13')
        observed.append((len(mat),len(rows)))
        raise OSError('simulated failure before commit')
    monkeypatch.setattr(semantic.os,'replace',replace)
    with pytest.raises(OSError):
        semantic.shard_add('2026-11-13', [[1.,0.]], [meta('b.jpg')])
    assert observed == [(1,1)] and len(semantic.shard_load('2026-11-13')[1]) == 1
    monkeypatch.setattr(semantic.os,'replace',original)


def test_legacy_shard_is_read_and_upgraded(index):
    d = index/'semantic'; d.mkdir()
    np.save(d/'2026-11-13.npy', np.array([[1.,0.]], np.float32))
    (d/'2026-11-13.json').write_text(json.dumps([meta()]))
    semantic.shard_add('2026-11-13', [[1.,0.]], [meta('b.jpg')])
    assert len(semantic.shard_load('2026-11-13')[1]) == 2
    assert semantic.indexed_days() == ['2026-11-13']


def test_live_batch_keeps_both_days(index):
    buf=io.BytesIO(); Image.new('RGB',(2,2)).save(buf,format='JPEG')
    semantic._index_snapshot_batch([(d,'100000_cam1_car.jpg',buf.getvalue())
                                    for d in ('2026-11-12','2026-11-13')])
    assert len(semantic.indexed_days()) == 2


def test_backfill_keeps_both_days_and_counts_success(index, monkeypatch):
    for day in ('2026-11-12','2026-11-13'):
        d=index/'snapshots'/day;d.mkdir(parents=True)
        Image.new('RGB',(2,2)).save(d/'100000_cam1_car.jpg')
    monkeypatch.setattr(semantic,'prune',lambda:None)
    monkeypatch.setattr(semantic.time,'sleep',lambda _:None)
    semantic._backfill()
    assert semantic.backfill_state['done'] == 2
    assert sum(len(semantic.shard_meta(d)) for d in semantic.indexed_days()) == 2
    semantic._backfill()
    assert sum(len(semantic.shard_meta(d)) for d in semantic.indexed_days()) == 2


def test_scene_queue_restored_on_failure(index, monkeypatch):
    semantic._queue_scena.put(('2026-11-13',meta(),np.array([1.,0.])))
    monkeypatch.setattr(semantic,'shard_add',lambda *a: (_ for _ in ()).throw(OSError('disk')))
    with pytest.raises(OSError): semantic._scrie_scena()
    assert semantic._queue_scena.qsize() == 1


def test_status_does_not_load_vectors(index,monkeypatch):
    semantic.shard_add('2026-11-13', [[1.,0.]], [meta()])
    monkeypatch.setattr(semantic,'shard_load',lambda _: (_ for _ in ()).throw(AssertionError('vectors loaded')))
    assert semantic.status()['indexed'] == 1


def test_coverage_is_scoped_to_allowed_camera_and_period(index):
    day=index/'snapshots'/'2026-11-13';day.mkdir(parents=True)
    for fn in ('100000_cam1_car.jpg','110000_cam1_car.jpg','100000_cam2_car.jpg'):
        (day/fn).touch()
    semantic.shard_add('2026-11-13',[[1.,0.]], [meta('100000_cam1_car.jpg')])
    c=semantic.coverage(visible={1},**window())
    assert c == {'scope':'snapshots','saved':2,'indexed':1,'missing':1,'video_complete':False}


def test_grouping_does_not_join_cameras_or_chain_forever(index):
    semantic.shard_add('2026-11-13', [[1.,0.]]*34,
                       [meta(f'{i}.jpg',cam=1,hour=f'10:{i:02d}:00') for i in range(33)]
                       + [meta('other.jpg',cam=2)])
    groups,_=semantic.appearances('x',gap=120,**window())
    assert len(groups) == 3
    assert all(len(g['camere']) == 1 and g['cover_cam'] == g['camere'][0] for g in groups)


@pytest.mark.parametrize('query,start,end',[
    ('ieri',(2026,11,12),(2026,11,13)),('alaltaieri',(2026,11,11),(2026,11,12)),
    ('saptamana asta',(2026,11,9),(2026,11,14)),('luna trecuta',(2026,10,1),(2026,11,1))])
def test_calendar_periods(query,start,end):
    text,p=search_time.parse_period('masina de gunoi '+query,now=datetime(2026,11,13,12).timestamp())
    assert text.strip() == 'masina de gunoi'
    assert p['since'] == datetime(*start).timestamp() and p['until'] == datetime(*end).timestamp()


def test_dst_calendar_day():
    # In TZ=Europe/Bucharest, aceasta zi are 25h, nu 24h.
    _,p=search_time.parse_period('ieri',now=datetime(2026,10,26,12).timestamp())
    assert p['since'] == datetime(2026,10,25).timestamp()
    assert p['until'] == datetime(2026,10,26).timestamp()


def test_overnight_range_belongs_to_day_it_starts():
    _,p=search_time.parse_period('masina ieri intre 22 si 2',now=datetime(2026,11,13,12).timestamp())
    assert p['since']==datetime(2026,11,12,22).timestamp()
    assert p['until']==datetime(2026,11,13,2).timestamp()


def test_first_count_and_hours_parser():
    assert ask.parse('cati oameni au trecut azi')['mod'] == 'cate'
    p=ask.parse('cand a trecut prima data masina de gunoi ieri intre 8 si 10?')
    assert p['first'] and p['concept'] == 'masina de gunoi' and p['hours'] == [480,600]
    assert search_time.in_hours('23:15:00',[22*60,2*60])
    assert not search_time.in_hours('10:00:00',[480,600])
    with pytest.raises(ValueError):ask.parse('cand a trecut masina intre 25 si 26')


def test_camera_alias_and_earliest_result(index,monkeypatch):
    captured={}
    def appearances(*a,**kw):
        captured.update(kw)
        return [{'inceput':f'2026-11-13 {h}:00:00','sfarsit':f'2026-11-13 {h}:00:00',
                 'camere':[2],'poze':1,'scor':.12,'coperta':'/x'} for h in ('12','09')],.12
    monkeypatch.setattr(semantic,'appearances',appearances)
    r=ask.answer('cand a trecut prima data masina de gunoi ieri la poarta?',visible={2},cam_names={2:'Poartă'})
    assert captured['visible'] == {2} and r['concept'] == 'masina de gunoi'
    assert r['aparitii'][0]['inceput'].endswith('09:00:00') and 'Prima apariție' in r['raspuns']
    assert ask.answer('cand a trecut masina la poarta?',visible={1,2},cam_names={2:'Poarta'},cam=1).get('error')


def test_journal_filters_before_grouping_and_reports_both_sources():
    import jurnal
    import db
    for cam,kind,label in [(1,'detectie','car'),(2,'detectie','car'),(2,'obiect','car'),(2,'detectie','dog')]:
        jurnal.insert({'t':'2026-11-13 10:00:00','cam':cam,'tip':kind,'eticheta':label})
    found=search_journal.find('car',visible={2},**window())
    assert found['tracked'] == 1 and found['episodes'] == 1 and not found['complete']
    assert all(g['camere'] == [2] for g in found['groups'])
    assert search_journal.find('car',visible=set(),**window())['groups'] == []


def test_scene_only_day_survives_prune(index,monkeypatch):
    import time
    day=time.strftime('%Y-%m-%d')
    crop=index/'scena'/'1'/'20261113-100000-1.jpg';crop.parent.mkdir(parents=True);crop.touch()
    row={**meta(crop.name),'sursa':'scena'}
    semantic.shard_add(day,[[1.,0.]],[row])
    monkeypatch.setattr(semantic,'IS_HOST',True)
    monkeypatch.setattr(semantic.settings,'get',lambda k:30 if k=='retention_snapshots_days' else None)
    semantic.prune()
    assert len(semantic.shard_meta(day)) == 1
    crop.unlink();semantic.prune()
    assert semantic.indexed_days() == []


def test_pending_scene_respects_feedback_before_first_write(index,monkeypatch):
    import scena_web
    row={**meta('20261113-100000-1.jpg'),'sursa':'scena'}
    monkeypatch.setattr(scena_web,'feedback_citeste',lambda: {'x':{'cam':1,'poza':row['fn'],'verdict':'gresit','eticheta':'nimic'}})
    semantic._queue_scena.put(('2026-11-13',row,np.array([1.,0.])))
    semantic._scrie_scena()
    assert semantic.indexed_days() == []


def test_backfill_counts_bad_images(index,monkeypatch):
    d=index/'snapshots'/'2026-11-13';d.mkdir(parents=True)
    (d/'100000_cam1_car.jpg').write_bytes(b'broken')
    monkeypatch.setattr(semantic.time,'sleep',lambda _:None)
    semantic._backfill()
    assert semantic.backfill_state['failed'] == 1 and semantic.backfill_state['done'] == 0


def test_corrupt_legacy_pair_is_not_overwritten(index):
    d=index/'semantic';d.mkdir()
    np.save(d/'2026-11-13.npy',np.ones((2,2),np.float32))
    (d/'2026-11-13.json').write_text(json.dumps([meta()]))
    with pytest.raises(ValueError):semantic.shard_add('2026-11-13',[[1.,0.]],[meta('new.jpg')])
    assert not (d/'2026-11-13.npz').exists()


def test_compact_legacy_duplicates_preserves_rollback_files(index):
    d=index/'semantic';d.mkdir()
    np.save(d/'2026-11-13.npy',np.array([[1.,0.],[0.,1.]],np.float32))
    (d/'2026-11-13.json').write_text(json.dumps([meta(),meta(label='truck')]))
    assert semantic.compact() == {'days':1,'duplicates_removed':1}
    mat,rows=semantic.shard_load('2026-11-13')
    assert rows[0]['label']=='truck' and mat.shape==(1,2)
    assert (d/'2026-11-13.npy').exists() and semantic.compact()['duplicates_removed']==0


def test_backfill_batches_disk_writes_without_large_gpu_batches(index,monkeypatch):
    d=index/'snapshots'/'2026-11-13';d.mkdir(parents=True)
    buf=io.BytesIO();Image.new('RGB',(2,2)).save(buf,format='JPEG')
    for i in range(257):(d/f'10{i//60:02d}{i%60:02d}_cam1_car.jpg').write_bytes(buf.getvalue())
    writes=[];batches=[]
    original=semantic._shard_write
    def write(day,mat,rows):writes.append(len(rows));return original(day,mat,rows)
    def embed(imgs):batches.append(len(imgs));return np.array([[1.,0.]]*len(imgs),np.float32)
    monkeypatch.setattr(semantic,'_shard_write',write)
    monkeypatch.setattr(semantic,'_embed_masurat',embed)
    monkeypatch.setattr(semantic.time,'sleep',lambda _:None)
    semantic._backfill()
    assert writes==[256,257] and max(batches)==16
    assert semantic.backfill_state['done']==257
