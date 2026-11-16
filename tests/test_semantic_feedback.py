"""Memorie explicita izolata pe concept, camera, perioada si versiunea imaginii."""
from datetime import datetime
import io
from pathlib import Path
import tarfile

import numpy as np
import pytest

import db
import semantic
import semantic_feedback as feedback
import semantic_store as store
from test_semantic_store import persistent_index, photo, DAY, period


@pytest.fixture
def learned(persistent_index,monkeypatch):
    tmp,q=persistent_index
    feedback.init()
    with db.conn() as c:c.execute('TRUNCATE semantic_feedback')
    monkeypatch.setattr(feedback,'IMAGE_DIR',tmp/'review-images')
    store.set_backend('postgres',mirror=False)
    yield tmp,q
    with db.conn() as c:c.execute('TRUNCATE semantic_feedback')


def indexed(tmp,q,**kwargs):
    m=photo(tmp,**kwargs);m['_fingerprint']=semantic.fingerprint(DAY,m)
    store.import_rows(DAY,np.stack([q]),[m],semantic.normalize_label,'x')
    return {**m,'day':DAY}


def test_concept_normalization_and_input_validation():
    assert feedback.canonical(' Mașină  de GUNOI! ')==feedback.canonical('masina de gunoi')
    assert feedback.canonical('camion')!=feedback.canonical('masina de gunoi')
    for text in [None,'','x'*161]:
        with pytest.raises(ValueError):feedback.canonical(text)
    for asset in [{'day':'2026-01-01','cam':True,'fn':'a.jpg'},
                  {'day':'2026-01-01','cam':1,'fn':'../a.jpg'},
                  {'day':'2026-02-30','cam':1,'fn':'a.jpg'}]:
        with pytest.raises(ValueError):feedback.validate_asset(asset)


def test_positive_memory_survives_reset_and_keeps_low_scoring_evidence(learned):
    tmp,q=learned
    low=q*.07;low[1]=np.sqrt(1-.07**2)
    m=indexed(tmp,low);indexed(tmp,q,cam=2)
    assert not any(g['cover_cam']==1 for g in semantic.appearances('garbage',**period())[0])
    saved=feedback.save('garbage',m,'yes','white compactor','reviewer')
    assert feedback.image_path(saved['image_id']).read_bytes()==Path(semantic.source_path(DAY,m)).read_bytes()
    feedback._ready=False
    groups,_=semantic.appearances('garbage',**period())
    assert any(g['cover_cam']==1 and g['confirmed'] for g in groups)
    result=semantic.search('garbage',limit=1,**period())
    assert result[0]['cam']==1 and result[0]['confirmed']
    assert semantic.search('garbage',visible={2},**period())[0]['cam']==2
    assert semantic.search('garbage',visible=set(),**period())==[]
    assert semantic.search('garbage',hours=[660,720],**period())==[]
    outside=dict(since=datetime(2026,11,14).timestamp(),until=datetime(2026,11,15).timestamp())
    assert semantic.search('garbage',**outside)==[]


def test_negatives_are_excluded_before_limit_and_only_for_the_same_concept(learned):
    tmp,q=learned
    rejected=indexed(tmp,q,cam=2);indexed(tmp,q,cam=1)
    feedback.save('garbage',rejected,'no','a van','reviewer')
    result=semantic.search('garbage',limit=1,**period())
    assert len(result)==1 and result[0]['cam']==1
    assert len(semantic.search('truck',**period()))==2
    review=semantic.search('garbage',review=True,**period())
    assert any(r['cam']==2 and r['feedback']=='no' for r in review)


def test_unknown_and_forget_remove_influence_without_relabeling_the_detector(learned):
    tmp,q=learned;m=indexed(tmp,q)
    saved=feedback.save('garbage',m,'no','maybe','reviewer')
    assert semantic.search('garbage',**period())==[]
    feedback.save('garbage',m,'unknown','uncertain','reviewer')
    assert len(semantic.search('garbage',**period()))==1
    assert feedback.forget('garbage',m)
    assert feedback.stats('garbage')=={}
    assert not (feedback.IMAGE_DIR/(saved['image_id']+'.jpg')).exists()
    assert store.metadata(DAY)[0]['label']==m['label']


def test_changed_pixels_invalidate_old_memory_and_stale_reviews(learned):
    tmp,q=learned;m=indexed(tmp,q)
    feedback.save('garbage',m,'no','','reviewer')
    new={**m,'_fingerprint':m['_fingerprint']+'changed'}
    store.put(DAY,np.stack([q]),[new],semantic.normalize_label)
    assert len(semantic.search('garbage',**period()))==1
    with pytest.raises(ValueError):feedback.save('garbage',m,'yes','','reviewer')


def test_saved_examples_remain_editable_after_retention(learned):
    tmp,q=learned;m=indexed(tmp,q)
    saved=feedback.save('garbage',m,'yes','compactor','reviewer')
    original=feedback.image_path(saved['image_id']).read_bytes()
    Path(semantic.source_path(DAY,m)).unlink();store.expire('2026-11-14')
    rows,more=feedback.browse('garbage')
    assert len(rows)==1 and not more and rows[0]['feedback']=='yes'
    assert feedback.image_path(saved['image_id']).read_bytes()==original
    feedback.save('garbage',m,'unknown','revisit','reviewer')
    assert feedback.stats('garbage')=={'unknown':1}
    assert semantic.search('garbage',**period())==[]


def test_archival_browse_is_chronological_not_semantic_and_camera_filtered(learned):
    tmp,q=learned
    indexed(tmp,q,cam=1);indexed(tmp,np.roll(q,1),cam=2)
    rows,more=feedback.browse('garbage',mode='archive',day=DAY,cam=2)
    assert len(rows)==1 and rows[0]['cam']==2 and not more
    assert not semantic.search('garbage',visible={2},**period())


def test_shared_image_is_removed_only_after_both_concepts_are_forgotten(learned):
    tmp,q=learned;m=indexed(tmp,q)
    first=feedback.save('garbage',m,'no','','reviewer')
    second=feedback.save('van',m,'yes','','reviewer')
    assert first['image_id']==second['image_id']
    feedback.forget('garbage',m)
    assert feedback.image_path(second['image_id']).is_file()
    feedback.forget('van',m)
    with pytest.raises(FileNotFoundError):feedback.image_path(second['image_id'])


def test_normal_search_does_not_publish_private_review_notes(learned):
    tmp,q=learned;m=indexed(tmp,q)
    feedback.save('garbage',m,'yes','private review note','reviewer')
    public=semantic.search('garbage',**period())[0]
    assert public['confirmed'] and 'feedback_note' not in public and 'reviewer' not in public
    review=semantic.search('garbage',review=True,**period())[0]
    assert review['feedback_note']=='private review note'


def test_historical_import_does_not_replace_a_newer_ui_decision(learned):
    tmp,q=learned;m=indexed(tmp,q)
    feedback.save('garbage',m,'no','latest UI decision','reviewer')
    result=feedback.save('garbage',m,'yes','old imported label','import',only_if_missing=True)
    assert result['skipped'] and result['verdict']=='no' and result['note']=='latest UI decision'
    assert semantic.search('garbage',**period())==[]


def test_backup_includes_feedback_and_preserved_images(learned,monkeypatch):
    import backup_nocturn as backup
    tmp,q=learned;m=indexed(tmp,q)
    data=tmp/'data';data.mkdir()
    monkeypatch.setattr(feedback,'IMAGE_DIR',data/'semantic-feedback')
    monkeypatch.setattr(backup,'KEY_FILE',str(tmp/'backup-key'))
    saved=feedback.save('garbage',m,'yes','reviewed','reviewer')
    original=feedback.image_path(saved['image_id']).read_bytes()
    assert 'semantic-feedback' in backup.DIRECTOARE
    buf=io.BytesIO()
    with tarfile.open(fileobj=buf,mode='w') as archive:counts=backup._dump_pg_in_tar(archive)
    assert counts['semantic_feedback']==1
    buf.seek(0)
    with tarfile.open(fileobj=buf,mode='r') as archive:
        content=archive.extractfile('pg/semantic_feedback.csv').read()
    assert b'reviewed' in content and b'garbage' in content
    manifest=backup.creeaza(dest_dir=str(tmp/'backups'),data_dir=str(data),fete=False)
    restored=tmp/'restored'
    backup.restaureaza(str(tmp/'backups'/manifest['fisier']),backup.cheie(),str(restored),manifest)
    assert (restored/'semantic-feedback'/(saved['image_id']+'.jpg')).read_bytes()==original
    with db.conn() as c:c.execute('TRUNCATE semantic_feedback')
    backup.incarca_pg(str(restored))
    assert feedback.stats('garbage')=={'yes':1}


def test_backup_keeps_matching_image_and_db_snapshot_without_blocking_edits(learned,monkeypatch):
    import backup_nocturn as backup
    import threading
    tmp,q=learned;m=indexed(tmp,q)
    saved=feedback.save('garbage',m,'yes','before backup','reviewer')
    image=feedback.image_path(saved['image_id']).read_bytes()
    buf=io.BytesIO();edited=threading.Event();failures=[]
    with tarfile.open(fileobj=buf,mode='w') as archive:
        original_add=archive.addfile
        def add(info,fileobj=None):
            if info.name=='pg/events.csv':
                def remove_after_snapshot():
                    try:feedback.forget('garbage',m)
                    except Exception as error:failures.append(error)
                    finally:edited.set()
                worker=threading.Thread(target=remove_after_snapshot,daemon=True);worker.start()
                assert edited.wait(3),'backup blocks edits while copying unrelated database tables'
                worker.join()
            return original_add(info,fileobj)
        monkeypatch.setattr(archive,'addfile',add)
        counts=backup._dump_pg_in_tar(archive)
    assert not failures and counts['semantic_feedback']==1
    assert feedback.stats('garbage')=={}
    buf.seek(0)
    with tarfile.open(fileobj=buf,mode='r') as archive:
        assert archive.extractfile('semantic-feedback/'+saved['image_id']+'.jpg').read()==image
        assert b'before backup' in archive.extractfile('pg/semantic_feedback.csv').read()
