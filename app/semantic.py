#!/usr/bin/env python3
"""Cautare semantica in alarme — "masina alba la poarta" gaseste poza, fara etichete.

Motor: SigLIP base multilingual (google/siglip-base-patch16-256-multilingual, local, GPU
cu fallback CPU) — un singur model cu doua capete: imaginile si textul ajung in acelasi spatiu de
768 dimensiuni, iar cautarea e un produs scalar. Ales in locul CLIP-ului multilingv
distilat pentru ca separa mult mai curat interogarile in romana (masurat: potrivirea
corecta ~0.07-0.19, ne-potrivirile sub zero).

Indexul: cate un shard NPZ atomic pe zi in data/semantic/ (vectori + metadate).
Formatul vechi <zi>.npy + <zi>.json ramane citibil; zilele atinse se convertesc.
Snapshot-urile noi se indexeaza pe
masura ce apar (coada + thread, nu blocheaza detectia); istoricul existent se
indexeaza printr-un backfill pornit din Setari. Shard-urile zilelor sterse de
retentie se sterg si ele — indexul urmareste exact ce exista pe disc.
"""
import json
import os
import queue
import re
import threading
import time
import tempfile
import fcntl
import unicodedata
from contextlib import contextmanager
from functools import lru_cache
from collections import OrderedDict

import numpy as np

import settings
import logare
import search_time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEM_DIR = os.path.join(ROOT, "data", "semantic")
SNAP_DIR = os.path.join(ROOT, "data", "snapshots")
SCENA_DIR = os.path.join(ROOT, "data", "scena")
MODEL_NAME = "google/siglip-base-patch16-256-multilingual"

_model = None
_proc = None
_init_lock = threading.Lock()  # incarcarea modelului: un singur thread o face
_lock = threading.Lock()       # serializarea inferentelor
_shard_lock = threading.RLock() # scrierea shard-urilor
_queue = queue.Queue(maxsize=200)
_worker_started = False
backfill_state = {"running": False, "done": 0, "total": 0, "failed": 0, "error": None}
_backfill_lock = threading.Lock()
_dropped = 0
WORKER_READY = True
_hints = OrderedDict()
_hints_lock = threading.Lock()


def persistent():
    active=bool(settings.get('semantic_persistent'))
    if active:
        import semantic_store
        if semantic_store.CHECKPOINT!=MODEL_NAME:
            raise RuntimeError('modelul semantic s-a schimbat; este necesara migrarea colectiei')
    return active


def pg_backend():
    if not persistent():
        return False
    import semantic_store
    return semantic_store.control()['backend'] == 'postgres'


@contextmanager
def storage_lock(exclusive=False):
    """Bariera intre mutatiile live si comutare/export. Ordine: storage -> zi -> PG."""
    os.makedirs(SEM_DIR,exist_ok=True)
    with open(os.path.join(SEM_DIR,'.storage.lock'),'a') as f:
        fcntl.flock(f,fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(f,fcntl.LOCK_UN)


def enabled():
    return bool(settings.get("semantic_enabled"))


_device = "cpu"


def _get_model():
    import ai_client
    ai_client.local_permis("SigLIP")
    # Fara lock-ul de initializare, warmup-ul, workerul de coada si backfill-ul ar
    # importa transformers simultan din thread-uri diferite — s-a intamplat: unul din
    # ele a vazut modulul incarcat pe jumatate ("cannot import name AutoModel").
    global _model, _proc, _device
    with _init_lock:
        if _model is None:
            import torch
            from transformers import AutoModel, AutoProcessor
            _device = "cuda" if torch.cuda.is_available() else "cpu"
            if _device == "cpu":
                torch.set_num_threads(8)  # backfill-ul nu sufoca 56 de nuclee
            # SigLIP TREBUIE sa fie float32. In transformers 5.15, dtype=float32 din
            # from_pretrained NU mai e respectat cand dtype-ul implicit global e temporar
            # bfloat16 (describe/Qwen se incarca in paralel) -> modelul iesea bf16 si toate
            # cautarile picau cu "expected Float but found BFloat16". Fortam .float() dupa
            # incarcare: castuieste TOTI parametrii/buffer-ele la float32, indiferent de
            # starea globala. (+ _match_dtype pe intrari, ca plasa a doua.)
            _model = AutoModel.from_pretrained(
                MODEL_NAME, dtype=torch.float32).float().to(_device)
            _proc = AutoProcessor.from_pretrained(MODEL_NAME)
            _model.eval()
            logare.info(f"[semantic] model siglip incarcat ({_device})")
    if persistent():
        import semantic_store
        semantic_store.assert_revision(getattr(_model.config,'_commit_hash',None))
    return _model, _proc


def warmup_async():
    if not enabled():
        return
    _ensure_worker()

    def _pornire():
        _get_model()
        # backfill-ul intrerupt de un restart se reia singur — altfel indexul ramane
        # vesnic la jumatate si nimeni nu observa (s-a intamplat: 19% dupa o zi)
        try:
            if _are_de_indexat():
                logare.info("[semantic] index incomplet — reiau backfill-ul")
                start_backfill()
        except Exception as e:
            logare.info(f"[semantic] verificare index: {str(e)[:80]}")

    threading.Thread(target=_pornire, daemon=True).start()


def _are_de_indexat():
    """Exista poze pe disc care nu sunt inca in index?"""
    for day in (os.listdir(SNAP_DIR) if os.path.isdir(SNAP_DIR) else []):
        dd = os.path.join(SNAP_DIR, day)
        if not os.path.isdir(dd):
            continue
        meta = shard_meta(day)
        have = {m["fn"] for m in meta}
        for fn in os.listdir(dd):
            if fn.endswith(".jpg") and fn not in have and _parse_fn(fn):
                return True
    seen = {}
    for day, meta, _ in _scene_assets():
        if day not in seen:
            seen[day] = {asset_key(m) for m in shard_meta(day)}
        if asset_key(meta) not in seen[day]:
            return True
    return False


def _match_dtype(ins, model):
    """Forteaza intrarile float la dtype-ul modelului (float32). Defensiv fata de cursa
    cu describe: cand Qwen (bf16) se incarca in paralel, dtype-ul implicit global e
    temporar bfloat16, iar procesorul scotea pixel_values bf16 -> "expected Float but
    found BFloat16" pe modelul float32. Legam dtype-ul de model, nu de starea globala."""
    import torch
    for k, v in list(ins.items()):
        if torch.is_floating_point(v):
            ins[k] = v.to(model.dtype)
    return ins


def _embed_masurat(imgs):
    import metrici
    with metrici.cronometru("semantic"):
        return embed_images(imgs)


def embed_images(pil_images):
    """Embeddings normalizate pentru o lista de imagini PIL: (N, 768)."""
    import torch
    model, proc = _get_model()
    with _lock, torch.no_grad():
        ins = _match_dtype(proc(images=pil_images, return_tensors="pt").to(_device), model)
        iv = model.vision_model(**ins).pooler_output
        iv = iv / iv.norm(dim=-1, keepdim=True)
    return iv.cpu().numpy().astype(np.float32)


@lru_cache(maxsize=128)
def embed_text(text):
    """Embedding normalizat pentru o interogare text: (768,)."""
    import torch
    model, proc = _get_model()
    with _lock, torch.no_grad():
        ins = _match_dtype(proc(text=[text], padding="max_length", truncation=True,
                                return_tensors="pt").to(_device), model)
        tv = model.text_model(**ins).pooler_output
        tv = tv / tv.norm(dim=-1, keepdim=True)
    return tv.cpu().numpy().astype(np.float32)[0]


# ---- shard-uri pe zile ----
def _shard_paths(day):
    return (os.path.join(SEM_DIR, f"{day}.npy"), os.path.join(SEM_DIR, f"{day}.json"))


def shard_load(day):
    """Un singur fisier NPZ publica atomic vectorii SI metadatele; citeste si formatul vechi."""
    packed = os.path.join(SEM_DIR, f'{day}.npz')
    if os.path.isfile(packed):
        with np.load(packed, allow_pickle=False) as z:
            mat, meta = z['vectors'], json.loads(z['meta'].tobytes())
        if len(mat) != len(meta):
            raise ValueError(f'index semantic inconsistent: {day}')
        return mat, meta
    npy, meta = _shard_paths(day)
    try:
        with open(meta) as f:
            rows = json.load(f)
        mat = np.load(npy, allow_pickle=False)
    except FileNotFoundError:
        return None, []
    if len(mat) != len(rows):
        raise ValueError(f'index semantic inconsistent: {day}')
    return mat, rows


def shard_meta(day):
    """Numararea si reconcilierea nu citesc matricea vectorilor."""
    packed = os.path.join(SEM_DIR, f'{day}.npz')
    if os.path.isfile(packed):
        with np.load(packed, allow_pickle=False) as z:
            return json.loads(z['meta'].tobytes())
    try:
        with open(_shard_paths(day)[1]) as f:
            return json.load(f)
    except FileNotFoundError:
        return []


def asset_key(m):
    return (m.get('sursa') or 'detectie', int(m['cam']), m['fn'])


@contextmanager
def _write_lock(day):
    os.makedirs(SEM_DIR, exist_ok=True)
    with _shard_lock, open(os.path.join(SEM_DIR, f'{day}.lock'), 'a') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)


def shard_add(day, embs, metas):
    if persistent():
        import semantic_store
        with storage_lock(),_write_lock(day):
            semantic_store.put(day,embs,metas,normalize_label)
            cfg=semantic_store.control()
            if cfg['backend']=='files' or cfg['mirror']:
                _file_add(day,embs,metas,locked=True)
        return
    return _file_add(day,embs,metas)


def _file_add(day, embs, metas, locked=False):
    """Adauga randuri la shard-ul unei zile. Scrierea e ATOMICA (fisier temporar +
    os.replace): backfill-ul scrie in timp ce /api/semantic/status citeste, iar un
    np.save direct l-a lasat odata pe cititor cu jumatate de fisier (EOFError)."""
    if not len(embs):
        return
    if len(embs) != len(metas):
        raise ValueError('vectori si metadate cu lungimi diferite')
    from contextlib import nullcontext
    with (nullcontext() if locked else _write_lock(day)):
        old, old_meta = shard_load(day)
        mat = np.vstack([old, embs]) if old is not None else np.asarray(embs)
        all_meta = old_meta + list(metas)
        # Ultima scriere castiga; acelasi asset nu dubleaza rezultatele la retry/backfill.
        unique = {asset_key(m): i for i, m in enumerate(all_meta)}
        indices = sorted(unique.values())
        _shard_write(day, mat[indices], [all_meta[i] for i in indices])


def _shard_write(day, mat, metas):
    """Commit atomic al perechii. Formatul vechi ramane disponibil pentru rollback."""
    os.makedirs(SEM_DIR, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f'.{day}-', suffix='.npz', dir=SEM_DIR)
    try:
        with os.fdopen(fd, 'wb') as f:
            np.savez(f, vectors=np.asarray(mat, np.float32),
                     meta=np.frombuffer(json.dumps(list(metas), ensure_ascii=False).encode(), dtype=np.uint8))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, os.path.join(SEM_DIR, f'{day}.npz'))
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def compact():
    """Elimina dublurile istorice din indexul activ; copia NPY/JSON ramane pentru rollback."""
    days = removed = 0
    for day in indexed_days():
        with _write_lock(day):
            meta = shard_meta(day)
            unique = {asset_key(m): i for i,m in enumerate(meta)}
            if len(unique) == len(meta):
                continue
            mat, meta = shard_load(day)
            indices = sorted(unique.values())
            _shard_write(day, mat[indices], [meta[i] for i in indices])
            days += 1
            removed += len(meta)-len(indices)
    return {'days': days, 'duplicates_removed': removed}


def url_poza(day, m):
    """URL-ul unei intrari din index: snapshot-ul de detectie (implicit) sau, pentru decupajele
    scenei, fisierul lor din data/scena/<cam>/ (meta poarta `url` si `sursa`)."""
    return m.get("url") or f"/snapshots/{day}/{m['fn']}"


_queue_scena = queue.Queue(maxsize=512)


def adauga_scena(cam, fn, eticheta, emb):
    """Un decupaj al scenei („ceva nou” clasificat) intra in indexul zilei lui, cu vectorul
    SigLIP deja calculat la compararea cu vocabularul (zero GPU in plus). Doar in nvr-ai
    (gazda indexului); nu blocheaza bucla scenei — scrie lucratorul, in loturi."""
    if not enabled() or not IS_HOST or emb is None:
        return False
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-\d+\.jpg$", fn)
    if not m:
        return False
    day = f"{m[1]}-{m[2]}-{m[3]}"
    meta = {"fn": fn, "time": f"{m[4]}:{m[5]}:{m[6]}", "cam": int(cam), "label": eticheta,
            "sursa": "scena", "url": f"/api/cam/{int(cam)}/scena/fisier/{fn}"}
    if persistent():
        _catalog(day,meta,emb)
        _ensure_worker()
        return True
    _ensure_worker()
    try:
        _queue_scena.put_nowait((day, meta, np.asarray(emb, np.float32)))
    except queue.Full:
        return False
    return True


def actualizeaza_scena(cam, fn, eticheta):
    """Verdictul omului pe un decupaj al scenei: eticheta noua in index sau, daca e „nimic” /
    ploaie / umbra / frunze, randul iese din index. Intoarce True daca a schimbat ceva."""
    import scena_clasa
    m = re.match(r"^(\d{4})(\d{2})(\d{2})-", fn or "")
    if not m:
        return False
    day = f"{m[1]}-{m[2]}-{m[3]}"
    if persistent():
        import semantic_store
        neutral=eticheta in scena_clasa.NEUTRE or eticheta=='nimic'
        meta={'sursa':'scena','cam':int(cam),'fn':fn,'label':eticheta}
        with storage_lock(),_write_lock(day):
            changed=semantic_store.remove(day,meta) if neutral else semantic_store.relabel(day,meta,normalize_label)
            cfg=semantic_store.control()
            if cfg['backend']=='files' or cfg['mirror']:
                _file_scene_label(day,cam,fn,eticheta,neutral)
        if not neutral:
            _catalog(day,_scene_meta(cam,fn,eticheta))
        return bool(changed) or not neutral
    with _write_lock(day):
        mat, meta = shard_load(day)
        if mat is None:
            return False
        idx = [i for i, x in enumerate(meta) if x.get("fn") == fn and x.get("cam") == int(cam) and x.get("sursa") == "scena"]
        if not idx:
            return False
        if eticheta in scena_clasa.NEUTRE or eticheta == "nimic":
            ramase = [i for i in range(len(meta)) if i not in idx]
            mat, meta = mat[ramase], [meta[i] for i in ramase]
        else:
            for i in idx:
                meta[i]["label"] = eticheta
        _shard_write(day, mat, meta)
    return True


def _file_scene_label(day,cam,fn,label,neutral):
    mat,rows=shard_load(day)
    if mat is None:
        return
    keep=[]
    for i,m in enumerate(rows):
        if m.get('sursa')=='scena' and m['cam']==int(cam) and m['fn']==fn:
            if neutral:
                continue
            m['label']=label
        keep.append(i)
    _shard_write(day,mat[keep],[rows[i] for i in keep])


def remove_asset(day,meta):
    """Stergerea din interfata nu asteapta reconcilierea periodica."""
    with storage_lock(),_write_lock(day):
        if persistent():
            import semantic_store
            semantic_store.remove(day,meta)
        if not persistent() or semantic_store.control()['backend']=='files' or semantic_store.control()['mirror']:
            mat,rows=shard_load(day)
            if mat is not None:
                keep=[i for i,m in enumerate(rows) if asset_key(m)!=asset_key(meta)]
                _shard_write(day,mat[keep],[rows[i] for i in keep])


def _scrie_scena():
    """Goleste coada decupajelor scenei in shard-urile zilelor lor (chemata de lucrator)."""
    pe_zi = {}
    verdicts = None
    while True:
        try:
            day, meta, emb = _queue_scena.get_nowait()
        except queue.Empty:
            break
        if persistent():
            _catalog(day,meta,emb)
            continue
        if meta.get('sursa') == 'scena':
            import scena_web
            import scena_clasa
            if verdicts is None:
                verdicts = {(r['cam'], r.get('poza')): r for r in scena_web.feedback_citeste().values()}
            verdict = verdicts.get((meta['cam'], meta['fn']), {})
            if verdict.get('verdict') == 'gresit' and verdict.get('eticheta'):
                meta = {**meta, 'label': verdict['eticheta']}
            if meta['label'] in scena_clasa.NEUTRE - {'necunoscut'} or meta['label'] == 'nimic':
                continue
        pe_zi.setdefault(day, ([], []))
        pe_zi[day][0].append(emb)
        pe_zi[day][1].append(meta)
    for day, (embs, metas) in list(pe_zi.items()):
        try:
            shard_add(day, np.stack(embs), metas)
            del pe_zi[day]
        except Exception:
            for pending_day, (pending_embs, pending_metas) in pe_zi.items():
                for emb, meta in zip(pending_embs, pending_metas):
                    try:
                        _queue_scena.put_nowait((pending_day, meta, emb))
                    except queue.Full:
                        logare.error('[semantic] coada scenei plina; necesita reconciliere')
            raise


def indexed_days():
    if not os.path.isdir(SEM_DIR):
        return []
    return sorted({fn[:-4] for fn in os.listdir(SEM_DIR)
                   if re.fullmatch(r'\d{4}-\d{2}-\d{2}\.(npy|npz)', fn)})


# Cine gazduieste indexul (modelul SigLIP + scrierea shard-urilor). False in nvr-web;
# nvr-ai o seteaza True (#9 pas 3). In web, cat ai_service e pornit, indexarea si
# curatarea se TRIMIT acolo — fisierele sunt pe FS-ul comun, circula doar (zi, nume).
IS_HOST = False


def prune():
    """Sterge shard-urile zilelor pe care retentia le-a scos din snapshots/ (decupajele
    scenei au aceeasi retentie, deci pleaca odata cu ziua lor)."""
    if not IS_HOST and settings.get("ai_service"):
        import ai_client
        ai_client.semantic_prune()
        return
    if persistent():
        import semantic_store
        keep=int(settings.get('retention_snapshots_days') or 0)
        with storage_lock(exclusive=True):
            cfg=semantic_store.control()
            if keep:
                cutoff,_=search_time.bounds(keep)
                cutoff_day=time.strftime('%Y-%m-%d',time.localtime(cutoff))
                semantic_store.expire(cutoff_day)
                for day in indexed_days():
                    if day<cutoff_day:
                        with _write_lock(day):
                            for path in (*_shard_paths(day),os.path.join(SEM_DIR,day+'.npz')):
                                try:os.unlink(path)
                                except FileNotFoundError:pass
            if cfg['backend']=='files' or cfg['mirror']:
                for day in indexed_days():
                    with _write_lock(day):
                        mat,rows=shard_load(day)
                        if mat is None:continue
                        keep_rows=[]
                        for i,m in enumerate(rows):
                            if os.path.isfile(source_path(day,m)):
                                keep_rows.append(i)
                            else:
                                semantic_store.remove(day,m)
                        if len(keep_rows)!=len(rows):
                            _shard_write(day,mat[keep_rows],[rows[i] for i in keep_rows])
        _reconcile_persistent()
        return
    keep = int(settings.get("retention_snapshots_days") or 0)
    for day in indexed_days():
        try:
            veche = keep and (time.time() - time.mktime(time.strptime(day, "%Y-%m-%d"))) / 86400 > keep
        except ValueError:
            veche = False
        with _write_lock(day):
            mat, meta = shard_load(day)
            if mat is None:
                continue
            keep_rows = [] if veche else [i for i, m in enumerate(meta) if os.path.isfile(
                os.path.join(SCENA_DIR, str(m['cam']), m['fn']) if m.get('sursa') == 'scena'
                else os.path.join(SNAP_DIR, day, m['fn']))]
            if not keep_rows:
                for p in (*_shard_paths(day), os.path.join(SEM_DIR, f'{day}.npz')):
                    try:
                        os.remove(p)
                    except FileNotFoundError:
                        pass
            elif len(keep_rows) != len(meta):
                _shard_write(day, mat[keep_rows], [meta[i] for i in keep_rows])


def _parse_fn(fn):
    m = re.match(r"^(\d{6})_cam(\d+)_(.+)\.jpg$", fn)
    if not m:
        return None
    return {"fn": fn, "time": f"{m[1][:2]}:{m[1][2:4]}:{m[1][4:]}",
            "cam": int(m[2]), "label": m[3]}


# ---- indexare incrementala (snapshot-urile noi) ----
def _ensure_worker():
    global _worker_started
    if _worker_started or (persistent() and (not IS_HOST or not WORKER_READY)):
        return
    _worker_started = True
    threading.Thread(target=_worker, daemon=True, name="semantic-index").start()


def _worker():
    """Ia snapshot-uri din coada si le indexeaza in loturi mici — detectia nu asteapta."""
    import io
    from PIL import Image
    lot, incercari = [], 0
    reconcile_at = time.monotonic() + 300
    persistent_seen = False
    while True:
        if persistent():
            try:
                if not enabled():
                    time.sleep(1)
                    continue
                if not persistent_seen or time.monotonic()>=reconcile_at:
                    start_backfill()
                    reconcile_at=time.monotonic()+300
                    persistent_seen=True
                # Livreaza si elementele primite in RAM inainte de activarea persistentei.
                while True:
                    try:
                        d,fn,_data=_queue.get_nowait()
                    except queue.Empty:
                        break
                    m=_parse_fn(fn)
                    if m:
                        _catalog(d,m)
                _scrie_scena()
                if not _process_persistent():
                    time.sleep(1)
            except Exception as e:
                logare.error(f'[semantic] coada persistenta: {str(e)[:100]}')
                time.sleep(5)
            continue
        try:
            item = _queue.get(timeout=5)
            lot.append(item)
            if len(lot) < 8 and not _queue.empty():
                continue
        except queue.Empty:
            pass
        if time.monotonic() >= reconcile_at:
            if enabled():
                start_backfill()
            reconcile_at = time.monotonic() + 300
        try:
            _scrie_scena()
        except Exception as e:
            logare.error(f"[semantic] indexarea scenei a esuat: {str(e)[:80]}")
        if not lot:
            continue
        try:
            _index_snapshot_batch(lot)
        except Exception as e:
            # la pornirea nvr-ai, SigLIP se incarca in timp ce alt fir mai importa din
            # transformers („cannot import name AutoModel”): lotul se reia, nu se pierde
            incercari += 1
            if incercari < 4:
                logare.info(f"[semantic] indexare amanata ({incercari}): {str(e)[:80]}")
                time.sleep(10)
                continue
            logare.error(f"[semantic] indexare esuata: {str(e)[:80]}")
        lot, incercari = [], 0


def _index_snapshot_batch(lot):
    import io
    from PIL import Image
    by_day = {}
    for day, fn, data in lot:
        meta = _parse_fn(fn)
        if meta:
            imgs, metas = by_day.setdefault(day, ([], []))
            with Image.open(io.BytesIO(data)) as img:
                imgs.append(img.convert('RGB'))
            metas.append(meta)
    for day, (imgs, metas) in by_day.items():
        shard_add(day, _embed_masurat(imgs), metas)


def enqueue(day, fn, jpeg_bytes):
    """Chemata din snaps la fiecare snapshot nou. Nu blocheaza niciodata."""
    global _dropped
    if not enabled():
        return
    if persistent():
        m=_parse_fn(fn)
        if m:
            _catalog(day,m)
        _ensure_worker()
        return
    if not IS_HOST and settings.get("ai_service"):
        import ai_client
        ai_client.semantic_index(day, fn)   # fisierul e pe FS comun; modelul e acolo
        return
    _ensure_worker()
    try:
        _queue.put_nowait((day, fn, jpeg_bytes))
    except queue.Full:
        _dropped += 1   # reconcilierea periodica recupereaza fisierul de pe disc


# ---- backfill (istoricul existent) ----
def start_backfill():
    with _backfill_lock:
        if backfill_state["running"]:
            return False
        backfill_state.update(running=True, done=0, total=0, failed=0, error=None)
    threading.Thread(target=_backfill, daemon=True, name="semantic-backfill").start()
    return True


def _scene_meta(cam,fn,label):
    match=re.fullmatch(r'(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})-\d+\.jpg',fn)
    if not match:
        raise ValueError('nume de decupaj invalid')
    return {'sursa':'scena','cam':int(cam),'fn':fn,'label':label,
            'time':f'{match[4]}:{match[5]}:{match[6]}','url':f'/api/cam/{int(cam)}/scena/fisier/{fn}'}


def source_path(day,m):
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}',day) or os.path.basename(m['fn'])!=m['fn']:
        raise ValueError('cale semantica invalida')
    return (os.path.join(SCENA_DIR,str(int(m['cam'])),m['fn']) if m.get('sursa')=='scena'
            else os.path.join(SNAP_DIR,day,m['fn']))


def fingerprint(day,m):
    st=os.stat(source_path(day,m))
    return f'{st.st_mtime_ns}:{st.st_size}'


def _feedback():
    import scena_web
    return {(r['cam'],r.get('poza')):r for r in scena_web.feedback_citeste().values()}


def _current_meta(meta,feedback):
    m=dict(meta)
    if m.get('sursa')=='scena':
        import scena_clasa
        verdict=feedback.get((m['cam'],m['fn']),{})
        if verdict.get('verdict')=='gresit' and verdict.get('eticheta'):
            m['label']=verdict['eticheta']
        if m['label'] in scena_clasa.NEUTRE-{'necunoscut'} or m['label']=='nimic':
            return None
    return m


def _catalog(day,meta,embedding=None,current=False):
    import semantic_store
    m=dict(meta) if current else _current_meta(meta,_feedback() if meta.get('sursa')=='scena' else {})
    if m is None:
        remove_asset(day,meta)
        return False
    try:
        version=fingerprint(day,m)
    except FileNotFoundError:
        remove_asset(day,m)
        return False
    m['_fingerprint']=version
    semantic_store.enqueue(day,m,version)
    if embedding is not None:
        hint=(day,asset_key(m),version)
        with _hints_lock:
            _hints[hint]=np.asarray(embedding,np.float32)
            _hints.move_to_end(hint)
            while len(_hints)>512:
                _hints.popitem(last=False)
    return True


def _process_persistent():
    """Un lot cu confirmare tranzactionala, versiune verificata inainte SI dupa GPU."""
    import semantic_store
    from PIL import Image
    tasks=semantic_store.claim()
    if not tasks:
        return False
    feedback=_feedback()
    ready=[];images=[];to_embed=[]
    for task in tasks:
        day,m=task['day'],task['meta']
        try:
            current=_current_meta(m,feedback)
            if current is None:
                remove_asset(day,m);continue
            if current!=m or fingerprint(day,m)!=task['fingerprint']:
                _catalog(day,current,current=True);continue
            with _hints_lock:
                value=_hints.pop((day,asset_key(m),task['fingerprint']),None)
            if value is None:
                value=semantic_store.existing(task)
            if value is not None:
                ready.append((task,value));continue
            with Image.open(source_path(day,m)) as image:
                images.append(image.convert('RGB'))
            to_embed.append(task)
        except FileNotFoundError:
            remove_asset(day,m)
        except (OSError,ValueError,Image.DecompressionBombError) as e:
            semantic_store.fail(task,e,permanent=True)
    if images:
        try:
            ready.extend(zip(to_embed,_embed_masurat(images)))
        except Exception as e:
            for task in to_embed:
                semantic_store.fail(task,e)
    for task,value in ready:
        day,m=task['day'],task['meta']
        try:
            # Feedback-ul se verifica iar sub bariera; generatia PG inchide si cursa cu web-ul.
            with storage_lock(),_write_lock(day):
                cfg=semantic_store.control()
                current=_current_meta(m,_feedback() if m.get('sursa')=='scena' else {})
                if current is None:
                    semantic_store.remove(day,m)
                    if cfg['backend']=='files' or cfg['mirror']:
                        _file_scene_label(day,m['cam'],m['fn'],'nimic',True)
                    continue
                version=fingerprint(day,m)
                if current!=m or version!=task['fingerprint']:
                    semantic_store.enqueue(day,{**current,'_fingerprint':version},version)
                    continue
                mirror=(lambda: _file_add(day,[value],[m],locked=True)) if cfg['backend']=='files' or cfg['mirror'] else None
                semantic_store.finish(task,value,normalize_label,before_commit=mirror)
        except FileNotFoundError:
            remove_asset(day,m)
        except Exception as e:
            semantic_store.fail(task,e)
    return True


def _reconcile_persistent():
    """Fisierul e dovada recuperabila si cand PG/serviciul era indisponibil la captura."""
    import semantic_store
    cfg=semantic_store.control()
    keep=int(settings.get('retention_snapshots_days') or 90)
    cutoff,_=search_time.bounds(keep)
    first=time.strftime('%Y-%m-%d',time.localtime(cutoff))
    by_day={};records={};queued=0

    def load(day):
        if day not in by_day:
            metas=semantic_store.metadata(day) if cfg['backend']=='postgres' else shard_meta(day)
            by_day[day]={asset_key(m):m for m in metas}
            records[day]={asset_key(m):(finger,state,m) for m,finger,state in semantic_store.task_records(day)}

    def consider(day,m):
        nonlocal queued
        load(day)
        k=asset_key(m)
        old=by_day[day].get(k)
        if old and cfg['backend']=='files' and '_fingerprint' not in old:
            return
        try:
            f=fingerprint(day,m)
        except FileNotFoundError:
            return
        m={**m,'_fingerprint':f}
        if old==m or records[day].get(k)==(f,'failed',m):
            return
        if _catalog(day,m,current=True):
            queued+=1

    for day in sorted(os.listdir(SNAP_DIR)) if os.path.isdir(SNAP_DIR) else []:
        dd=os.path.join(SNAP_DIR,day)
        if day<first or not os.path.isdir(dd):
            continue
        for fn in os.listdir(dd):
            m=_parse_fn(fn)
            if m:
                consider(day,m)
    for day,m,_ in _scene_assets():
        if day>=first:
            consider(day,m)
    feedback=_feedback()
    # Tombstone-uri pentru stergeri / verdicte pierdute la o intrerupere de transport.
    days=[d for d,n in semantic_store.metadata()] if cfg['backend']=='postgres' else indexed_days()
    for day in days:
        # Retentia sterge zilele expirate in bloc prin prune(); nu rescriem un NPZ
        # istoric pentru fiecare fotografie care a disparut deja de pe disc.
        if day<first:
            continue
        load(day)
        for m in by_day[day].values():
            current=_current_meta(m,feedback)
            if current is None or not os.path.isfile(source_path(day,m)):
                remove_asset(day,m)
            elif current!=m:
                actualizeaza_scena(m['cam'],m['fn'],current['label'])
    backfill_state.update(done=queued,total=queued)
    return queued


def _scene_assets():
    """Recuperare din jurnal si verdictul curent; nu indexam orb pozele de ploaie/fundal."""
    import db
    import scena_web
    import scena_clasa
    feedback = {(r['cam'], r['poza']): r for r in scena_web.feedback_citeste().values() if r.get('poza')}
    with db.conn() as c:
        # Cursor server-side: jurnalul nu se incarca integral in RAM.
        with c.transaction(), c.cursor(name='semantic_scene_reconcile') as cursor:
            cursor.execute("SELECT t, cam, date FROM events WHERE tip = 'scena_candidat' ORDER BY ts")
            for stamp, cam, raw in cursor:
                e = json.loads(raw)
                fn = e.get('poza')
                if not fn or not re.fullmatch(r'\d{8}-\d{6}-\d+\.jpg', fn):
                    continue
                label = e.get('eticheta') or 'necunoscut'
                verdict = feedback.get((cam, fn), {})
                if verdict.get('verdict') == 'gresit' and verdict.get('eticheta'):
                    label = verdict['eticheta']
                if label in scena_clasa.NEUTRE - {'necunoscut'} or label == 'nimic':
                    continue
                path = os.path.join(SCENA_DIR, str(cam), fn)
                if not os.path.isfile(path):
                    continue
                day = f'{fn[:4]}-{fn[4:6]}-{fn[6:8]}'
                yield day, {'fn': fn, 'time': f'{fn[9:11]}:{fn[11:13]}:{fn[13:15]}',
                            'cam': cam, 'label': label, 'sursa': 'scena',
                            'url': f'/api/cam/{cam}/scena/fisier/{fn}'}, path


def _backfill():
    """Indexeaza snapshot-urile care nu sunt inca in index, zi cu zi, in ritm lent."""
    from PIL import Image
    try:
        if persistent():
            _reconcile_persistent()
            return
        # Retentia e apelata zilnic de snaps.prune(); reconcilierea la 5 minute
        # nu reciteste toate matricile si nu face stat pe intreaga arhiva.
        todo = []
        for day in sorted(os.listdir(SNAP_DIR), reverse=True) if os.path.isdir(SNAP_DIR) else []:
            dd = os.path.join(SNAP_DIR, day)
            if not os.path.isdir(dd):
                continue
            meta = shard_meta(day)
            have = {m["fn"] for m in meta}
            files = [fn for fn in os.listdir(dd)
                     if fn.endswith(".jpg") and fn not in have and _parse_fn(fn)]
            todo += [(day, _parse_fn(fn), os.path.join(dd, fn)) for fn in sorted(files)]
        seen = {}
        cutoff, _ = search_time.bounds(int(settings.get('retention_snapshots_days') or 90))
        cutoff_day = time.strftime('%Y-%m-%d', time.localtime(cutoff))
        for day, meta, path in _scene_assets():
            if day < cutoff_day:
                continue
            if day not in seen:
                seen[day] = {asset_key(m) for m in shard_meta(day)}
            if asset_key(meta) not in seen[day]:
                todo.append((day, meta, path))
                seen[day].add(asset_key(meta))
        backfill_state["total"] = len(todo)
        pending = {}

        def flush(day):
            vectors, metas = pending.pop(day)
            shard_add(day, np.vstack(vectors), metas)
            backfill_state['done'] += len(metas)

        for i in range(0, len(todo), 16):
            lot = todo[i:i + 16]
            by_day = {}
            for day, meta, path in lot:
                try:
                    with Image.open(path) as image:
                        img = image.convert('RGB')
                except OSError:
                    backfill_state['failed'] += 1
                    continue
                imgs, metas = by_day.setdefault(day, ([], []))
                imgs.append(img)
                metas.append(meta)
            for day, (imgs, metas) in by_day.items():
                vectors, rows = pending.setdefault(day, ([], []))
                vectors.append(_embed_masurat(imgs))
                rows.extend(metas)
                if len(rows) >= 256:
                    flush(day)
            time.sleep(0.3)   # ritm lent: serverul are treaba mai importanta decat istoricul
        for day in list(pending):
            flush(day)
    except Exception as e:
        backfill_state['error'] = str(e)[:100]
        logare.error(f"[semantic] backfill esuat: {str(e)[:100]}")
    finally:
        backfill_state["running"] = False
        logare.info(f"[semantic] backfill terminat: {backfill_state['done']} imagini")


def status():
    if persistent():
        import semantic_store
        s=semantic_store.stats()
        counts=s['tasks']
        pending=counts.get('pending',0)+counts.get('running',0)
        total=sum(n for state,n in counts.items() if state!='gone')
        indexed=s['indexed'] if s['backend']=='postgres' else sum(len(shard_meta(d)) for d in indexed_days())
        return {'enabled':enabled(),'indexed':indexed,'days':s['days'] if s['backend']=='postgres' else len(indexed_days()),
                'backend':s['backend'],'persistent':True,'model':s['model'],'tasks':counts,
                'backfill':{**backfill_state,'running':backfill_state['running'] or pending>0,
                            'done':counts.get('done',0),'total':total,'failed':counts.get('failed',0)},
                'queued':pending,'queued_scene':0,'deferred':0}
    total = 0
    for day in indexed_days():
        meta = shard_meta(day)
        total += len(meta)
    return {"enabled": enabled(), "indexed": total, "days": len(indexed_days()),
            "backfill": dict(backfill_state), 'queued': _queue.qsize(), 'deferred': _dropped,
            'queued_scene': _queue_scena.qsize()}


# ---- aparitii: "cand a fost vazut X?" ----
APPEAR_GAP = 900       # doua potriviri la sub 15 min = aceeasi aparitie (si pe camere diferite)
MIN_SCORE = 0.055      # sub atat, SigLIP nu "vede" conceptul in poza
REL_SCORE = 0.80       # membru al unei aparitii = macar 80% din cel mai bun scor


def normalize_label(label):
    value = ''.join(c for c in unicodedata.normalize('NFKD', label.lower()) if not unicodedata.combining(c))
    return {'masina': 'car', 'camion': 'truck', 'autobuz': 'bus', 'motocicleta': 'motorcycle',
            'om': 'person', 'persoana': 'person', 'caine': 'dog', 'pisica': 'cat',
            'pasare': 'bird', 'gaina': 'chicken', 'porumbel': 'pigeon'}.get(value, value)


GENERIC_LABELS = {'vmd', 'fielddetection', 'linedetection', 'duration', 'intrus',
                  'scenechangedetection', 'necunoscut', 'unknown', 'motion', 'attendedbaggage'}


def _hits(text, days=30, visible=None, labels=None, since=None, until=None, hours=None):
    a, b = search_time.bounds(days)
    since, until = a if since is None else since, b if until is None else until
    lo, hi = (time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(x)) for x in (since, until))
    if since >= until or visible == set():
        return
    qe = embed_text(text)
    if pg_backend():
        import semantic_store
        for r in semantic_store.hits(qe,since,until,visible,labels,hours,generic=GENERIC_LABELS):
            yield {**r,'url':url_poza(r['day'],r)}
        return
    for day in reversed(indexed_days()):
        if day < lo[:10] or day > hi[:10]:
            continue
        mat, meta = shard_load(day)
        if mat is None or not meta:
            continue
        unique = {asset_key(m): i for i, m in enumerate(meta)}
        indices = [i for i in sorted(unique.values()) for m in [meta[i]]
                   if (visible is None or m['cam'] in visible)
                   and lo <= f"{day} {m['time']}" < hi
                   and search_time.in_hours(m['time'], hours)
                   # Un snapshot poate contine si alte obiecte decat eticheta declansatoare.
                   and (labels is None or m.get('sursa') != 'scena'
                        or (label := normalize_label(m['label'])) in labels
                        or label in GENERIC_LABELS)]
        if not indices:
            continue
        sims = mat[indices] @ qe
        for i, score in zip(indices, sims):
            if score < MIN_SCORE:
                continue
            m = meta[i]
            yield {**m, 'day': day, 'score': float(score), 'url': url_poza(day, m),
                   'sursa': m.get('sursa') or 'detectie'}


def appearances(text, days=30, visible=None, gap=APPEAR_GAP, labels=None,
                since=None, until=None, hours=None):
    """Toate aparitiile unui concept ("masina de gunoi"), grupate in timp.

    Pragul e adaptiv: se ia cel mai bun scor din corp si se pastreaza tot ce e peste
    max(MIN_SCORE, REL_SCORE * top) — un prag absolut fix ar taia conceptele greu de
    vazut si ar inunda la cele usoare. Intoarce (aparitii, top_scor); aparitiile sunt
    cele mai noi primele: {inceput, sfarsit, camere, poze, coperta, scor}."""
    hits = list(_hits(text, days, visible, labels, since, until, hours))
    if not hits:
        return [], 0.0
    top = max(h["score"] for h in hits)
    prag = max(MIN_SCORE, REL_SCORE * top)
    hits = [h for h in hits if h["score"] >= prag]
    for h in hits:
        h["ts"] = time.mktime(time.strptime(f"{h['day']} {h['time']}",
                                            "%Y-%m-%d %H:%M:%S"))
    hits.sort(key=lambda h: h["ts"])            # cronologic, ca sa se lege grupurile
    grupuri, active = [], {}
    for h in hits:
        g = active.get(h['cam'])
        if g and h['ts'] - g['_last'] <= gap and h['ts'] - g['_start'] < 1800:
            g["_last"] = h["ts"]
            g["sfarsit"] = f"{h['day']} {h['time']}"
            g["camere"] = sorted(set(g["camere"]) | {h["cam"]})
            g["poze"] += 1
            if h["score"] > g["scor"]:
                g["scor"] = h["score"]
                g["coperta"] = h["url"]
                g['cover_cam'], g['cover_t'] = h['cam'], f"{h['day']} {h['time']}"
        else:
            g = {"inceput": f"{h['day']} {h['time']}",
                            "sfarsit": f"{h['day']} {h['time']}",
                            "camere": [h["cam"]], "poze": 1,
                            "scor": h["score"],
                            "coperta": h["url"],
                            'cover_cam': h['cam'], 'cover_t': f"{h['day']} {h['time']}",
                            '_start': h['ts'], "_last": h["ts"]}
            active[h['cam']] = g
            grupuri.append(g)
    for g in grupuri:
        g.pop("_last")
        g.pop('_start')
        g['weak'] = g['scor'] < .08
        g["scor"] = round(g["scor"], 3)
    grupuri.sort(key=lambda g: g['inceput'], reverse=True)
    return grupuri, round(top, 3)


# ---- cautarea ----
def search(text, days=14, limit=40, visible=None, since=None, until=None, hours=None):
    """Cele mai potrivite snapshot-uri pentru o descriere libera.

    visible: None = toate camerele, sau multimea de id-uri permise (viewer restrans).
    Scorurile SigLIP sunt mici in valoare absoluta (potrivire buna ~0.05-0.2);
    le intoarcem brute, ordinea e cea care conteaza."""
    import heapq
    if pg_backend():
        import semantic_store
        if visible is not None and not visible:
            return []
        a,b=search_time.bounds(days)
        results=semantic_store.hits(embed_text(text),a if since is None else since,b if until is None else until,
                                   visible=visible,hours=hours,limit=limit)
        return [{**r,'url':url_poza(r['day'],r),'weak':r['score']<.08,'score':round(r['score'],4)} for r in results]
    results = heapq.nlargest(max(0, limit), _hits(text, days, visible, None, since, until, hours),
                            key=lambda r: (r['score'], r['day'], r['time'], r['cam'], r['fn']))
    return [{**r, 'weak': r['score'] < .08, 'score': round(r['score'], 4)} for r in results]


def coverage(days=30, visible=None, since=None, until=None, hours=None):
    """Acoperirea snapshot-urilor salvate; nu afirma ca a fost analizat tot video-ul."""
    a, b = search_time.bounds(days)
    lo, hi = (time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(x))
              for x in (a if since is None else since, b if until is None else until))
    saved = indexed = 0
    for day in sorted(os.listdir(SNAP_DIR)) if os.path.isdir(SNAP_DIR) else []:
        dd = os.path.join(SNAP_DIR, day)
        if not os.path.isdir(dd) or not lo[:10] <= day <= hi[:10]:
            continue
        names = set()
        for fn in os.listdir(dd):
            m = _parse_fn(fn)
            if (m and (visible is None or m['cam'] in visible)
                    and lo <= f"{day} {m['time']}" < hi and search_time.in_hours(m['time'], hours)):
                names.add(fn)
        if pg_backend():
            import semantic_store
            metas=semantic_store.metadata(day)
        else:
            metas=shard_meta(day)
        have = {m['fn'] for m in metas if m.get('sursa', 'detectie') == 'detectie'
                and (visible is None or m['cam'] in visible)}
        saved += len(names)
        indexed += len(names & have)
    return {'scope': 'snapshots', 'saved': saved, 'indexed': indexed,
            'missing': saved-indexed, 'video_complete': False}
