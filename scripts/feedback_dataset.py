#!/usr/bin/env python3
"""Verdictele omului din „Invata scena” (data/scena/feedback.jsonl) -> datasetul de fine-tune.

Fiecare verdict e pe un decupaj HD (data/scena/<cam>/<poza>), deci decupajul devine o imagine
de antrenament:
  - ✓ corect / ✕ „e de fapt X” cu X o fiinta sau un vehicul -> imagine pozitiva, cu chenarul
    obiectului gasit de YOLO-World in decupaj pentru numele ales (altfel chenarul din mijloc,
    fara marginea de context a decupajului);
  - ✕ „e de fapt ploaie / umbra / frunze” -> imagine de FUNDAL, fara chenare: exact negativele
    de care modelul avea nevoie (curte-v1 vedea „person” in 15% din fundaluri);
  - ? nu se vede clar -> nu intra.
Clasele: cele 11 ale datasetului (teacher) + numele din vocabular care au destule exemple
(MIN_POZITIVE), in forma engleza (scena_clasa.engleza); ordinea claselor e persistata in
data/finetune/clase.json ca id-urile sa nu se schimbe intre rulari. Impartirea train/val e
fixa (hash pe numele pozei, 15% val), fisierele poarta prefixul fb- si se refac la fiecare
rulare (verdictul ultim conteaza). dataset.yaml se rescrie cu lista completa de clase.

Rulare (in nvr-ai, din app/antrenare.py sau de mana):  venv/bin/python scripts/feedback_dataset.py
"""
import collections
import hashlib
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "app"))

OUT = os.path.join(ROOT, "data", "finetune")
SCENA = os.path.join(ROOT, "data", "scena")
CLASE_FN = os.path.join(OUT, "clase.json")
CLASSES = ["person", "car", "truck", "bus", "motorcycle",
           "cat", "dog", "horse", "bird", "chicken", "pigeon"]     # ca in finetune_dataset.py
ALIAS = {"child": "person", "kid": "person", "man": "person", "woman": "person",
         "hen": "chicken", "rooster": "chicken", "dove": "pigeon", "van": "truck", "lorry": "truck"}
NEUTRE_RO = {"ploaie", "umbră", "frunze mișcate de vânt", "copac", "nimic", "fundal"}   # + scena_clasa.NEUTRE la rulare
MIN_POZITIVE = 10        # un nume nou devine clasa doar cu atatea exemple (altfel modelul nu invata nimic)
VAL_FRACTION = 0.15
MARGINE = 0.25           # marginea decupajului (scena.decupaj) -> chenarul din mijloc, ca rezerva
CONF_CHENAR = 0.10


def verdicte(cale=None):
    """{id: ultimul rand} din feedback.jsonl."""
    out = {}
    try:
        with open(cale or os.path.join(SCENA, "feedback.jsonl"), encoding="utf-8") as f:
            for ln in f:
                try:
                    r = json.loads(ln)
                except ValueError:
                    continue
                if r.get("id"):
                    out[r["id"]] = r
    except OSError:
        pass
    return out


def eticheta_finala(r):
    """Numele (romana) pe care il spune verdictul, sau None daca nu stim / nu intra.
    ok -> eticheta initiala a candidatului (sau cea aleasa); gresit -> cea aleasa."""
    v = r.get("verdict")
    if v == "necunoscut":
        return None
    if v == "ok":
        return r.get("eticheta") or r.get("eticheta_initiala")
    if v == "gresit":
        return r.get("eticheta")
    return None


def clasa_pentru(nume_ro, engleza):
    """(clasa engleza, e_fundal). Numele neutre -> fundal; restul -> clasa (cu aliasuri)."""
    if not nume_ro:
        return None, False
    neutre = set(NEUTRE_RO)
    try:
        import scena_clasa
        neutre |= scena_clasa.NEUTRE - {"necunoscut"}
    except Exception:
        pass
    if nume_ro in neutre:
        return None, True
    en = (engleza(nume_ro) or nume_ro).strip().lower()
    en = ALIAS.get(en, en)
    if en in ("rain", "shadow", "leaves moving in the wind", "nothing", "background"):
        return None, True
    return en, False


def split_pentru(poza):
    """train/val fix, dupa hash-ul numelui pozei."""
    h = int(hashlib.sha1(poza.encode()).hexdigest()[:8], 16)
    return "val" if (h % 1000) < VAL_FRACTION * 1000 else "train"


def chenar_implicit(w, h):
    """Chenarul din mijlocul decupajului, fara marginea de context (normalizat cx, cy, bw, bh)."""
    f = 1 / (1 + 2 * MARGINE)
    return (0.5, 0.5, round(f, 4), round(f, 4))


def clase_persistente(noi):
    """Lista de clase cu id-uri stabile: cele vechi (clase.json sau CLASSES) + noile, la coada."""
    try:
        with open(CLASE_FN, encoding="utf-8") as f:
            clase = [str(x) for x in json.load(f)]
    except (OSError, ValueError):
        clase = list(CLASSES)
    for c in CLASSES:
        if c not in clase:
            clase.append(c)
    for c in noi:
        if c not in clase:
            clase.append(c)
    return clase


def _chenar_yolo_world(crop, en):
    """Chenarul (normalizat) al obiectului `en` in decupaj, prin YOLO-World; None daca nu-l gaseste."""
    import scena_lume
    b = scena_lume.detecteaza_nume(crop, en, imgsz=640, conf=CONF_CHENAR)
    if not b:
        return None
    h, w = crop.shape[:2]
    x1, y1, x2, y2 = b["xyxy"]
    return ((x1 + x2) / 2 / w, (y1 + y2) / 2 / h, (x2 - x1) / w, (y2 - y1) / h)


def main(folosi_yolo_world=True):
    import cv2
    import scena_clasa
    import i18n
    i18n.incarca()
    vd = verdicte()
    print(f"[dataset] {len(vd)} verdicte din feedback", flush=True)
    randuri = []          # (poza_cale, nume_fisier, clasa_en | None, e_fundal, r)
    numar = collections.Counter()
    for r in vd.values():
        poza, cam = r.get("poza"), r.get("cam")
        if not poza or cam is None:
            continue
        cale = os.path.join(SCENA, str(cam), poza)
        if not os.path.isfile(cale):
            continue
        nume = eticheta_finala(r)
        clasa, fundal = clasa_pentru(nume, scena_clasa.engleza)
        if clasa is None and not fundal:
            continue
        randuri.append((cale, f"fb-{cam}-{poza}", clasa, fundal, r))
        numar[clasa or "(fundal)"] += 1
    noi = sorted(c for c, n in numar.items() if c != "(fundal)" and c not in CLASSES and n >= MIN_POZITIVE)
    rare = sorted(c for c, n in numar.items() if c != "(fundal)" and c not in CLASSES and n < MIN_POZITIVE)
    clase = clase_persistente(noi)
    if rare:
        print(f"[dataset] nume cu prea putine exemple (sub {MIN_POZITIVE}), lasate deoparte: {', '.join(rare)}", flush=True)

    # fisierele fb- vechi se refac de la zero (verdictul ultim conteaza)
    for sp in ("train", "val"):
        for sub in ("images", "labels"):
            d = os.path.join(OUT, sub, sp)
            os.makedirs(d, exist_ok=True)
            for fn in os.listdir(d):
                if fn.startswith("fb-"):
                    os.remove(os.path.join(d, fn))

    scrise = collections.Counter()
    cu_chenar_model = 0
    for i, (cale, fn, clasa, fundal, r) in enumerate(randuri):
        if clasa is not None and clasa not in clase:
            continue                       # nume rar: nu intra nici ca pozitiv, nici ca fundal
        img = cv2.imread(cale)
        if img is None:
            continue
        sp = split_pentru(fn)
        shutil.copyfile(cale, os.path.join(OUT, "images", sp, fn))
        linii = []
        if not fundal:
            chenar = None
            if folosi_yolo_world:
                try:
                    chenar = _chenar_yolo_world(img, clasa)
                except Exception as e:
                    print(f"[dataset] yolo-world pe {fn}: {str(e)[:80]}", flush=True)
                    folosi_yolo_world = False
            if chenar:
                cu_chenar_model += 1
            else:
                chenar = chenar_implicit(*img.shape[1::-1])
            linii.append(f"{clase.index(clasa)} " + " ".join(f"{v:.6f}" for v in chenar))
        with open(os.path.join(OUT, "labels", sp, fn[:-4] + ".txt"), "w") as f:
            f.write("\n".join(linii) + ("\n" if linii else ""))
        scrise[(sp, "fundal" if fundal else clasa)] += 1
        if (i + 1) % 50 == 0:
            print(f"[dataset] {i + 1}/{len(randuri)}", flush=True)

    with open(CLASE_FN, "w", encoding="utf-8") as f:
        json.dump(clase, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT, "dataset.yaml"), "w") as f:
        f.write(f"path: {OUT}\ntrain: images/train\nval: images/val\n"
                f"names:\n" + "".join(f"  {i}: {n}\n" for i, n in enumerate(clase)))
    total = sum(scrise.values())
    print(f"\n[dataset] gata: {total} decupaje din feedback in dataset "
          f"({cu_chenar_model} cu chenar de la YOLO-World), {len(clase)} clase"
          + (f", noi: {', '.join(noi)}" if noi else ""), flush=True)
    for (sp, c), n in sorted(scrise.items()):
        print(f"   {sp:5s} {c:28s} {n}", flush=True)
    return total


if __name__ == "__main__":
    main()
