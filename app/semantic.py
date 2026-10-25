#!/usr/bin/env python3
"""Cautare semantica in alarme — "masina alba la poarta" gaseste poza, fara etichete.

Motor: SigLIP base multilingual (google/siglip-base-patch16-256-multilingual, local, GPU
cu fallback CPU) — un singur model cu doua capete: imaginile si textul ajung in acelasi spatiu de
768 dimensiuni, iar cautarea e un produs scalar. Ales in locul CLIP-ului multilingv
distilat pentru ca separa mult mai curat interogarile in romana (masurat: potrivirea
corecta ~0.07-0.19, ne-potrivirile sub zero).

Indexul: cate un "shard" pe zi in data/semantic/ — <zi>.npy (matricea de embeddings)
+ <zi>.json (fisier, camera, eticheta per rand). Snapshot-urile noi se indexeaza pe
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

import numpy as np

import settings
import logare

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SEM_DIR = os.path.join(ROOT, "data", "semantic")
SNAP_DIR = os.path.join(ROOT, "data", "snapshots")
MODEL_NAME = "google/siglip-base-patch16-256-multilingual"

_model = None
_proc = None
_init_lock = threading.Lock()  # incarcarea modelului: un singur thread o face
_lock = threading.Lock()       # serializarea inferentelor
_shard_lock = threading.Lock() # scrierea shard-urilor
_queue = queue.Queue(maxsize=200)
_worker_started = False
backfill_state = {"running": False, "done": 0, "total": 0}


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
        _, meta = shard_load(day)
        have = {m["fn"] for m in meta}
        for fn in os.listdir(dd):
            if fn.endswith(".jpg") and fn not in have and _parse_fn(fn):
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
    """(matrice (N,768), meta [N]) sau (None, []). EOFError e in lista pentru ca un
    fisier prins la jumatatea scrierii nu trebuie sa doboare cititorul — desi scrierea
    e atomica (tmp + rename), lista ramane completa din prudenta."""
    npy, meta = _shard_paths(day)
    try:
        return np.load(npy), json.load(open(meta))
    except (FileNotFoundError, ValueError, OSError, EOFError):
        return None, []


def shard_add(day, embs, metas):
    """Adauga randuri la shard-ul unei zile. Scrierea e ATOMICA (fisier temporar +
    os.replace): backfill-ul scrie in timp ce /api/semantic/status citeste, iar un
    np.save direct l-a lasat odata pe cititor cu jumatate de fisier (EOFError)."""
    if not len(embs):
        return
    with _shard_lock:
        os.makedirs(SEM_DIR, exist_ok=True)
        old, old_meta = shard_load(day)
        mat = np.vstack([old, embs]) if old is not None else np.asarray(embs)
        npy, meta = _shard_paths(day)
        np.save(npy + ".tmp.npy", mat.astype(np.float32))
        os.replace(npy + ".tmp.npy", npy)
        with open(meta + ".tmp", "w") as f:
            json.dump(old_meta + list(metas), f, ensure_ascii=False)
        os.replace(meta + ".tmp", meta)


def indexed_days():
    if not os.path.isdir(SEM_DIR):
        return []
    return sorted(fn[:-4] for fn in os.listdir(SEM_DIR) if fn.endswith(".npy"))


# Cine gazduieste indexul (modelul SigLIP + scrierea shard-urilor). False in nvr-web;
# nvr-ai o seteaza True (#9 pas 3). In web, cat ai_service e pornit, indexarea si
# curatarea se TRIMIT acolo — fisierele sunt pe FS-ul comun, circula doar (zi, nume).
IS_HOST = False


def prune():
    """Sterge shard-urile zilelor pe care retentia le-a scos din snapshots/."""
    if not IS_HOST and settings.get("ai_service"):
        import ai_client
        ai_client.semantic_prune()
        return
    for day in indexed_days():
        if not os.path.isdir(os.path.join(SNAP_DIR, day)):
            for p in _shard_paths(day):
                try:
                    os.remove(p)
                except FileNotFoundError:
                    pass


def _parse_fn(fn):
    m = re.match(r"^(\d{6})_cam(\d+)_(.+)\.jpg$", fn)
    if not m:
        return None
    return {"fn": fn, "time": f"{m[1][:2]}:{m[1][2:4]}:{m[1][4:]}",
            "cam": int(m[2]), "label": m[3]}


# ---- indexare incrementala (snapshot-urile noi) ----
def _ensure_worker():
    global _worker_started
    if _worker_started:
        return
    _worker_started = True
    threading.Thread(target=_worker, daemon=True, name="semantic-index").start()


def _worker():
    """Ia snapshot-uri din coada si le indexeaza in loturi mici — detectia nu asteapta."""
    import io
    from PIL import Image
    lot = []
    while True:
        try:
            item = _queue.get(timeout=5)
            lot.append(item)
            if len(lot) < 8 and not _queue.empty():
                continue
        except queue.Empty:
            pass
        if not lot:
            continue
        try:
            imgs, metas, day = [], [], lot[0][0]
            for d, fn, data in lot:
                meta = _parse_fn(fn)
                if meta and d == day:
                    imgs.append(Image.open(io.BytesIO(data)).convert("RGB"))
                    metas.append(meta)
            if imgs:
                shard_add(day, _embed_masurat(imgs), metas)
        except Exception as e:
            logare.error(f"[semantic] indexare esuata: {str(e)[:80]}")
        lot = []


def enqueue(day, fn, jpeg_bytes):
    """Chemata din snaps la fiecare snapshot nou. Nu blocheaza niciodata."""
    if not enabled():
        return
    if not IS_HOST and settings.get("ai_service"):
        import ai_client
        ai_client.semantic_index(day, fn)   # fisierul e pe FS comun; modelul e acolo
        return
    _ensure_worker()
    try:
        _queue.put_nowait((day, fn, jpeg_bytes))
    except queue.Full:
        pass   # mai bine sarim un cadru decat sa incetinim detectia


# ---- backfill (istoricul existent) ----
def start_backfill():
    if backfill_state["running"]:
        return False
    backfill_state.update(running=True, done=0, total=0)
    threading.Thread(target=_backfill, daemon=True, name="semantic-backfill").start()
    return True


def _backfill():
    """Indexeaza snapshot-urile care nu sunt inca in index, zi cu zi, in ritm lent."""
    from PIL import Image
    try:
        prune()
        todo = []
        for day in sorted(os.listdir(SNAP_DIR), reverse=True) if os.path.isdir(SNAP_DIR) else []:
            dd = os.path.join(SNAP_DIR, day)
            if not os.path.isdir(dd):
                continue
            _, meta = shard_load(day)
            have = {m["fn"] for m in meta}
            files = [fn for fn in os.listdir(dd)
                     if fn.endswith(".jpg") and fn not in have and _parse_fn(fn)]
            todo += [(day, fn) for fn in sorted(files)]
        backfill_state["total"] = len(todo)
        for i in range(0, len(todo), 16):
            lot = todo[i:i + 16]
            imgs, metas, day = [], [], lot[0][0]
            for d, fn in lot:
                if d != day:      # lotul nu amesteca zile; restul revine in tura urmatoare
                    continue
                try:
                    img = Image.open(os.path.join(SNAP_DIR, d, fn)).convert("RGB")
                except OSError:
                    continue
                imgs.append(img)
                metas.append(_parse_fn(fn))
            if imgs:
                shard_add(day, _embed_masurat(imgs), metas)
            backfill_state["done"] += len(lot)
            time.sleep(0.3)   # ritm lent: serverul are treaba mai importanta decat istoricul
    except Exception as e:
        logare.error(f"[semantic] backfill esuat: {str(e)[:100]}")
    finally:
        backfill_state["running"] = False
        logare.info(f"[semantic] backfill terminat: {backfill_state['done']} imagini")


def status():
    total = 0
    for day in indexed_days():
        _, meta = shard_load(day)
        total += len(meta)
    return {"enabled": enabled(), "indexed": total, "days": len(indexed_days()),
            "backfill": dict(backfill_state)}


# ---- aparitii: "cand a fost vazut X?" ----
APPEAR_GAP = 900       # doua potriviri la sub 15 min = aceeasi aparitie (si pe camere diferite)
MIN_SCORE = 0.055      # sub atat, SigLIP nu "vede" conceptul in poza
REL_SCORE = 0.80       # membru al unei aparitii = macar 80% din cel mai bun scor


def appearances(text, days=30, visible=None, gap=APPEAR_GAP, labels=None):
    """Toate aparitiile unui concept ("masina de gunoi"), grupate in timp.

    Pragul e adaptiv: se ia cel mai bun scor din corp si se pastreaza tot ce e peste
    max(MIN_SCORE, REL_SCORE * top) — un prag absolut fix ar taia conceptele greu de
    vazut si ar inunda la cele usoare. Intoarce (aparitii, top_scor); aparitiile sunt
    cele mai noi primele: {inceput, sfarsit, camere, poze, coperta, scor}."""
    qe = embed_text(text)
    hits = []
    for day in sorted(indexed_days(), reverse=True)[:days]:
        mat, meta = shard_load(day)
        if mat is None or not len(meta):
            continue
        sims = mat @ qe
        for i in np.where(sims >= MIN_SCORE)[0]:
            m = meta[int(i)]
            if visible is not None and m["cam"] not in visible:
                continue
            # labels: "masina alba" se cauta DOAR in pozele etichetate ca vehicule —
            # fara filtru, orice poza vag alba de pe orice camera intra la numarat
            if labels is not None and m.get("label") not in labels:
                continue
            hits.append({"day": day, "time": m["time"], "cam": m["cam"],
                         "fn": m["fn"], "score": float(sims[int(i)])})
    if not hits:
        return [], 0.0
    top = max(h["score"] for h in hits)
    prag = max(MIN_SCORE, REL_SCORE * top)
    hits = [h for h in hits if h["score"] >= prag]
    for h in hits:
        h["ts"] = time.mktime(time.strptime(f"{h['day']} {h['time']}",
                                            "%Y-%m-%d %H:%M:%S"))
    hits.sort(key=lambda h: h["ts"])            # cronologic, ca sa se lege grupurile
    grupuri = []
    for h in hits:
        if grupuri and h["ts"] - grupuri[-1]["_last"] <= gap:
            g = grupuri[-1]
            g["_last"] = h["ts"]
            g["sfarsit"] = f"{h['day']} {h['time']}"
            g["camere"] = sorted(set(g["camere"]) | {h["cam"]})
            g["poze"] += 1
            if h["score"] > g["scor"]:
                g["scor"] = h["score"]
                g["coperta"] = f"/snapshots/{h['day']}/{h['fn']}"
        else:
            grupuri.append({"inceput": f"{h['day']} {h['time']}",
                            "sfarsit": f"{h['day']} {h['time']}",
                            "camere": [h["cam"]], "poze": 1,
                            "scor": h["score"],
                            "coperta": f"/snapshots/{h['day']}/{h['fn']}",
                            "_last": h["ts"]})
    for g in grupuri:
        g.pop("_last")
        g["scor"] = round(g["scor"], 3)
    grupuri.reverse()                           # cele mai noi primele
    return grupuri, round(top, 3)


# ---- cautarea ----
def search(text, days=14, limit=40, visible=None):
    """Cele mai potrivite snapshot-uri pentru o descriere libera.

    visible: None = toate camerele, sau multimea de id-uri permise (viewer restrans).
    Scorurile SigLIP sunt mici in valoare absoluta (potrivire buna ~0.05-0.2);
    le intoarcem brute, ordinea e cea care conteaza."""
    qe = embed_text(text)
    out = []
    for day in sorted(indexed_days(), reverse=True)[:days]:
        mat, meta = shard_load(day)
        if mat is None or not len(meta):
            continue
        sims = mat @ qe
        for i in np.argsort(sims)[::-1][:limit]:
            m = meta[int(i)]
            if visible is not None and m["cam"] not in visible:
                continue
            out.append({"url": f"/snapshots/{day}/{m['fn']}", "day": day,
                        "cam": m["cam"], "label": m["label"], "time": m["time"],
                        "score": round(float(sims[int(i)]), 4)})
    out.sort(key=lambda r: -r["score"])
    return out[:limit]
