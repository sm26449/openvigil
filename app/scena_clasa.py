#!/usr/bin/env python3
"""Clasificarea unui decupaj „ceva nou” (din scena.py): ce e obiectul din regiunea schimbata.

Doua pareri, pe decupaj (obiectul umple imaginea, deci si un om de 20 px din cadru devine
clasificabil):
  1. YOLO (modelul activ al instalatiei) pe decupaj, la 320 px — eticheta si increderea;
  2. vocabularul deschis: SigLIP (modelul cautarii semantice, deja incarcat) compara decupajul
     cu lista de nume a instalatiei („om”, „om pe bicicletă”, „cal cu căruță”, „tractor”…),
     editabila din Setari — un nume nou nu cere niciun antrenament.
Decizia: YOLO cand e sigur (>= PRAG_YOLO); altfel numele din vocabular daca iese clar in fata
celorlalte; altfel „necunoscut” — si exact necunoscutele sunt cele pe care le aratam omului
in fisa camerei, ca sa ne spuna ce sunt (feedback-ul intra la antrenament).
Textele vocabularului sunt in romana (SigLIP e multilingv); interfata le traduce la afisare.
"""
import json
import os
import re
import threading

import numpy as np

import logare
import settings

PRAG_YOLO = 0.5          # increderea YOLO de la care nu mai intrebam vocabularul
PRAG_LUME = 0.3          # increderea YOLO-World (detector pe vocabular) de la care o credem
PRAG_VOCAB = 0.05        # scorul SigLIP (cosinus) sub care nu credem niciun nume
MARJA_VOCAB = 0.01       # cat trebuie sa fie peste al doilea nume ca sa fie „clar”

VOCABULAR_IMPLICIT = [
    "om", "om pe bicicletă", "om cu cărucior", "copil", "mașină", "camion", "tractor",
    "autobuz", "motocicletă", "cal", "cal cu căruță", "câine", "pisică", "găină", "porumbel",
    "pasăre", "vulpe", "mistreț", "oaie", "vacă", "frunze mișcate de vânt", "umbră", "ploaie",
]

_lock = threading.Lock()
_vocab_cache = {"chei": None, "emb": None}


def _linii():
    v = settings.get("scena_vocabular")
    return [str(x).strip() for x in (v or []) if str(x).strip()] or list(VOCABULAR_IMPLICIT)


def _desparte(linie):
    """„cal cu căruță = horse cart” -> („cal cu căruță”, „horse cart”); fara „=” -> (nume, None)."""
    ro, _, en = linie.partition("=")
    return ro.strip(), (en.strip() or None)


def vocabular():
    """Numele (romana) din vocabular — ce vede omul si ce compara SigLIP."""
    return [ro for ro, _ in (_desparte(x) for x in _linii()) if ro]


_EN_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "scena", "vocab_en.json")


def engleza(nume):
    """Forma engleza a unui nume din vocabular, pentru CLIP (YOLO-World): cea scrisa explicit
    („nume = english”), altfel dictionarul interfetei (en.json), altfel o traducere prin
    Qwen memorata in data/scena/vocab_en.json; in ultima instanta numele ca atare."""
    for ro, en in (_desparte(x) for x in _linii()):
        if ro == nume and en:
            return en
    import i18n
    if nume in i18n.dictionar("en"):          # stiut de interfata (chiar daca e identic: „tractor”)
        return i18n.t(nume, "en")
    cache = {}
    try:
        with open(_EN_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        pass
    if nume in cache:
        return cache[nume]
    try:
        import describe
        tr = describe.text(f"Translate to English, answer with the translation only, 1-4 words: {nume}")
        tr = tr.strip(" .\n\"'").lower()
        if 0 < len(tr) <= 40:
            cache[nume] = tr
            os.makedirs(os.path.dirname(_EN_CACHE), exist_ok=True)
            with open(_EN_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=1)
            logare.info(f"[scena] tradus pentru YOLO-World: {nume} -> {tr}")
            return tr
    except Exception as e:
        logare.info(f"[scena] traducere {nume}: {str(e)[:80]}")
    return nume


def _embeddings_vocab(nume):
    """Embedding-urile SigLIP ale numelor, memorate pana se schimba lista."""
    import semantic
    chei = tuple(nume)
    with _lock:
        if _vocab_cache["chei"] != chei:
            _vocab_cache["emb"] = np.stack([semantic.embed_text(n) for n in nume])
            _vocab_cache["chei"] = chei
        return _vocab_cache["emb"]


def _yolo(crop_bgr, model, clase, quantize=None, imgsz=320):
    try:
        res = model.predict(crop_bgr, imgsz=imgsz, conf=0.25, verbose=False, quantize=quantize)[0]
    except TypeError:                      # ultralytics fara `quantize`
        res = model.predict(crop_bgr, imgsz=imgsz, conf=0.25, verbose=False)[0]
    best = None
    for b in res.boxes:
        lbl = clase.get(int(b.cls)) if clase else None
        if lbl is None:
            continue
        conf = float(b.conf)
        if best is None or conf > best["incredere"]:
            best = {"eticheta": lbl, "incredere": round(conf, 3)}
    return best


def _vocab(crop_bgr, n=3):
    import cv2
    import semantic
    from PIL import Image
    if not semantic.enabled():
        return None
    nume = vocabular()
    emb = _embeddings_vocab(nume)
    pil = Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB))
    e = semantic.embed_images([pil])[0]
    scoruri = emb @ e
    ordine = np.argsort(-scoruri)[:n]
    return [{"nume": nume[i], "scor": round(float(scoruri[i]), 4)} for i in ordine]


def _lume(crop_bgr, imgsz=320, quantize=None):
    import scena_lume
    return scena_lume.detecteaza(crop_bgr, imgsz=imgsz, quantize=quantize)


def clasifica(crop_bgr, model=None, clase=None, quantize=None):
    """{eticheta, incredere, sursa, yolo, lume, vocab} pentru un decupaj BGR.
    Trei pareri: YOLO-ul instalatiei (clasele antrenate), YOLO-World (detector pe vocabular)
    si SigLIP (tot decupajul fata de vocabular); decizia in _decide."""
    rez = {"eticheta": "necunoscut", "incredere": 0.0, "sursa": "-", "yolo": None, "lume": None, "vocab": None}
    if crop_bgr is None or crop_bgr.size == 0:
        return rez
    if model is not None:
        try:
            rez["yolo"] = _yolo(crop_bgr, model, clase or {}, quantize)
        except Exception as e:
            logare.info(f"[scena] yolo pe decupaj: {str(e)[:80]}")
    try:
        rez["lume"] = _lume(crop_bgr, quantize=quantize)
    except Exception as e:
        logare.info(f"[scena] yolo-world: {str(e)[:80]}")
    try:
        rez["vocab"] = _vocab(crop_bgr)
    except Exception as e:
        logare.info(f"[scena] vocabular: {str(e)[:80]}")
    return _decide(rez)


def _decide(rez):
    """YOLO-ul antrenat cand e sigur; altfel YOLO-World (vede obiectul, nu fundalul);
    altfel SigLIP daca un nume iese clar in fata; altfel YOLO nesigur; altfel necunoscut."""
    y, w, v = rez.get("yolo"), rez.get("lume"), rez.get("vocab")
    if y and y["incredere"] >= PRAG_YOLO:
        rez.update(eticheta=y["eticheta"], incredere=y["incredere"], sursa="yolo")
    elif w and w["incredere"] >= PRAG_LUME:
        rez.update(eticheta=w["eticheta"], incredere=w["incredere"], sursa="yolo-world")
    elif v and v[0]["scor"] >= PRAG_VOCAB and (len(v) < 2 or v[0]["scor"] - v[1]["scor"] >= MARJA_VOCAB):
        rez.update(eticheta=v[0]["nume"], incredere=v[0]["scor"], sursa="vocabular")
    elif y:
        rez.update(eticheta=y["eticheta"], incredere=y["incredere"], sursa="yolo-nesigur")
    return rez


# ---- analiza adanca, la cerere (butonul 🔍 din fisa camerei / Telegram) ----
PROMPT_VLM = ("Imagine de la o cameră de supraveghere. Ce ființă sau obiect principal se vede în ea? "
              "Răspunde în română, doar cu numele, în 1-5 cuvinte (exemple: om, om pe bicicletă, "
              "pisică, găină, tractor, mașină albă, cal cu căruță). Dacă nu e nicio ființă și niciun "
              "vehicul, spune ce se vede (de exemplu: frunze, umbră, ploaie, drum gol, perete).")
# raspunsuri care inseamna „nu e nimic de etichetat”: raman la vocabular/YOLO
VLM_NIMIC = ("nimic", "nu se vede", "gol", "goală", "perete", "drum", "iarbă", "neclar")


def _din_vocabular(text, nume):
    """Numele din vocabular pe care il contine raspunsul VLM — ca sa ramana antrenabil.
    Potrivire pe cuvinte intregi („om” nu se ascunde in „camion”); un nume compus se
    potriveste si prin primul lui cuvant, daca are macar 4 litere („frunze” -> „frunze
    mișcate de vânt”). Castiga numele intreg cel mai lung."""
    jos = (text or "").lower()

    def are(cuv):
        return re.search(r"(?<!\w)" + re.escape(cuv.lower()) + r"(?!\w)", jos) is not None

    intregi = [n for n in nume if are(n)]
    if intregi:
        return max(intregi, key=len)
    for n in nume:
        primul = n.split()[0]
        if len(primul) >= 4 and " " in n and are(primul):
            return n
    return None


def _vlm_e_nimic(text):
    """Raspunsul VLM nu e o eticheta: gol, „nimic/drum gol/perete…”, sau o insiruire
    (modelul mic o ia uneori razna pe fundaluri: „pământ, copaci, vânt, vânt, vânt…”)."""
    jos = (text or "").lower()
    if not jos or len(jos.split()) > 6 or jos.count(",") >= 3:
        return True
    return any(re.search(r"(?<!\w)" + c + r"(?!\w)", jos) for c in VLM_NIMIC)


def analiza_adanca(crop_bgr, model=None, clase=None, quantize=None, lock=None):
    """Parerea „scumpa” despre un decupaj, ceruta de om: YOLO la 640, tot vocabularul (top 5)
    si VLM-ul (Qwen2.5-VL, incarcat la nevoie, cateva secunde). `lock` se tine DOAR pe YOLO
    (modelul partajat cu urmarirea); VLM-ul are lacatul lui si nu blocheaza camerele.
    Decizia: VLM-ul primeaza (vede contextul, nu doar forma); daca raspunsul contine un nume
    din vocabular, eticheta e acel nume (ramane antrenabil); altfel textul lui, scurtat."""
    rez = {"eticheta": "necunoscut", "incredere": 0.0, "sursa": "-", "yolo": None, "lume": None,
           "vocab": None, "vlm": None}
    if crop_bgr is None or crop_bgr.size == 0:
        return rez
    if model is not None:
        try:
            if lock is not None:
                with lock:
                    rez["yolo"] = _yolo(crop_bgr, model, clase or {}, quantize, imgsz=640)
            else:
                rez["yolo"] = _yolo(crop_bgr, model, clase or {}, quantize, imgsz=640)
        except Exception as e:
            logare.info(f"[scena] analiza yolo: {str(e)[:80]}")
    try:
        rez["lume"] = _lume(crop_bgr, imgsz=640, quantize=quantize)
    except Exception as e:
        logare.info(f"[scena] analiza yolo-world: {str(e)[:80]}")
    try:
        rez["vocab"] = _vocab(crop_bgr, n=5)
    except Exception as e:
        logare.info(f"[scena] analiza vocabular: {str(e)[:80]}")
    try:
        import cv2
        import describe
        from PIL import Image
        pil = Image.fromarray(cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB))
        txt = describe.describe_crop(pil, PROMPT_VLM)
        rez["vlm"] = " ".join(txt.split()).strip(" .").lower()[:80] or None
    except Exception as e:
        logare.info(f"[scena] analiza vlm: {str(e)[:80]}")
    return _decide_adanc(rez)


_DIACRITICE = str.maketrans("șşțţăâî", "sstta ai".replace(" ", ""))


def _simplu(text):
    """Litere mici, fara diacritice — pentru comparat nume („Masina” ~ „mașină”)."""
    return (text or "").lower().translate(_DIACRITICE).strip()


def _acelasi(a, b):
    """Doua nume vorbesc despre acelasi lucru: egale, sau cu acelasi prim cuvant
    („om pe bicicletă” ~ „om”)."""
    a, b = _simplu(a), _simplu(b)
    if not a or not b:
        return False
    return a == b or a.split()[0] == b.split()[0]


def _confirmat(nume, rez):
    """Parerea VLM-ului e sustinuta de altcineva? YOLO-World (orice detectie), SigLIP (in
    primele 3) sau YOLO-ul antrenat (clasa lui, tradusa). VLM-ul vede context, dar pe
    fundaluri de noapte inventeaza („pisică albă” pe flori) — singur nu-l credem."""
    w = rez.get("lume")
    if w and _acelasi(w.get("eticheta"), nume):
        return "yolo-world"
    if any(_acelasi(x.get("nume"), nume) for x in (rez.get("vocab") or [])[:3]):
        return "vocabular"
    y = rez.get("yolo")
    if y:
        try:
            import notify
            ro = notify.RO.get(y.get("eticheta"), y.get("eticheta"))
        except Exception:
            ro = y.get("eticheta")
        if _acelasi(ro, nume):
            return "yolo"
    return None


def _decide_adanc(rez):
    """VLM-ul primeaza DOAR confirmat de un alt model; altfel decizia obisnuita (YOLO /
    YOLO-World / SigLIP), iar parerea VLM-ului ramane in `vlm` ca sa o vada omul."""
    vlm = rez.get("vlm")
    nume = _din_vocabular(vlm, vocabular()) if vlm else None
    if vlm and (nume or not _vlm_e_nimic(vlm)):
        martor = _confirmat(nume or vlm, rez)
        if martor:
            rez.update(eticheta=nume or vlm, incredere=1.0, sursa="vlm", confirmat=martor)
            return rez
        rez["vlm_neconfirmat"] = True
    return _decide(rez)
