#!/usr/bin/env python3
"""Intrebari in limbaj natural peste indexul semantic.

"Cand a venit masina de gunoi ultima data?" se desface in trei bucati:
  conceptul   — "masina de gunoi" (ce cautam vizual, prin SigLIP)
  modul       — "cand" (ultima aparitie) sau "cate" (numarare)
  fereastra   — azi / ieri / saptamana / luna (implicit 30 de zile)
apoi semantic.appearances() gaseste si grupeaza aparitiile, iar answer() formuleaza
raspunsul (in limba interfetei, prin t()), cu pozele-dovada alaturi. Intrebarile merg in
romana si in engleza: aceleasi liste, cu expresiile ambelor limbi.

Parserul e intentionat simplu: liste de expresii de scos si de ferestre de timp,
fara modele de limbaj — intrebarile despre camere au o structura foarte previzibila,
si un parser previzibil poate fi TESTAT.
"""
import re
import time
import unicodedata

from i18n import t

ZILE_RO = ["luni", "marti", "miercuri", "joi", "vineri", "sambata", "duminica"]

# expresii care fac parte din intrebare, nu din concept (fara diacritice, lowercase)
_STOP = [
    "cand a venit", "cand au venit", "cand a fost", "cand au fost", "cand a aparut",
    "cand a trecut", "cand am vazut", "cand s-a vazut", "ultima data", "ultima oara",
    "prima data", "de cate ori", "cate ori", "de cand", "a venit", "au venit",
    "a aparut", "au aparut", "a trecut", "au trecut", "a fost", "au fost",
    "pe la noi", "la noi", "pe aici", "pe acasa", "pe strada", "prin fata",
    "cand", "cine", "cate", "cati", "oare", "vazut", "vazuta",
    # engleza
    "when did", "when was", "when were", "when has", "when have", "what time did",
    "the last time", "last time", "last seen", "how many times", "how many", "how often",
    "show up", "showed up", "come by", "came by", "come", "came", "arrive", "arrived",
    "appear", "appeared", "pass by", "passed by", "pass", "passed", "visit", "visited",
    "been here", "at our place", "around here", "here", "did", "does", "was", "were",
    "has", "have", "when", "who", "seen", "see", "last", "first", "ever", "again", "lately",
]

# Conceptele care sunt chiar etichetele detectiei YOLO: pentru ele raspunsul vine
# din JURNALUL de evenimente (numarare exacta), nu din potrivirea vizuala SigLIP —
# "cate masini au trecut" e o intrebare de contorizare, nu de cautare.
_LABELS = {}
for _forme, _lbl in [
    (("masina", "masini", "masinile", "automobil", "automobile"), "car"),
    (("camion", "camioane", "duba", "dube", "tir"), "truck"),
    (("motocicleta", "motociclete", "scuter"), "motorcycle"),
    (("autobuz", "autobuze"), "bus"),
    (("om", "oameni", "persoana", "persoane"), "person"),
    (("caine", "caini", "cainele"), "dog"),
    (("pisica", "pisici"), "cat"),
    (("pasare", "pasari"), "bird"),
    (("cal", "cai"), "horse"), (("oaie", "oi"), "sheep"),
    (("vaca", "vaci"), "cow"), (("urs", "ursi"), "bear"),
    (("gaina", "gaini", "gainile"), "chicken"),
    (("porumbel", "porumbei", "porumbeii"), "pigeon"),
    # engleza
    (("car", "cars", "vehicle", "vehicles"), "car"),
    (("truck", "trucks", "lorry", "lorries", "van", "vans"), "truck"),
    (("motorcycle", "motorcycles", "motorbike", "motorbikes", "scooter", "scooters"), "motorcycle"),
    (("bus", "buses"), "bus"),
    (("person", "people", "someone", "somebody", "man", "woman", "anyone"), "person"),
    (("dog", "dogs"), "dog"), (("cat", "cats"), "cat"), (("bird", "birds"), "bird"),
    (("horse", "horses"), "horse"), (("sheep",), "sheep"), (("cow", "cows"), "cow"),
    (("bear", "bears"), "bear"), (("chicken", "chickens", "hen", "hens"), "chicken"),
    (("pigeon", "pigeons"), "pigeon"),
]:
    for _f in _forme:
        _LABELS[_f] = _lbl
_ARTICOLE = ("o ", "un ", "niste ", "vreo ", "vreun ", "the ", "a ", "an ", "some ", "any ")
_FERESTRE = [("azi", 1), ("astazi", 1), ("ieri", 2), ("alaltaieri", 3),
             ("saptamana asta", 7), ("saptamana trecuta", 14), ("saptamana", 7),
             ("luna asta", 31), ("luna trecuta", 62), ("luna", 31),
             ("today", 1), ("yesterday", 2), ("this week", 7), ("last week", 14),
             ("past week", 7), ("this month", 31), ("last month", 62), ("past month", 31),
             ("week", 7), ("month", 31)]


def _plat(s):
    """lowercase + fara diacritice — parserul nu depinde de cum tastezi."""
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.lower().replace("?", " ").replace("!", " ")).strip()


def is_question(q):
    """Arata textul a intrebare, nu a cautare simpla?"""
    p = _plat(q)
    if not p:
        return False
    if str(q).strip().endswith("?"):
        return True
    return bool(re.match(r"^(cand|cine|cate|de cate|cati|when|who|how many|how often|what time|did|has|have|was|were)\b", p)
                or "ultima data" in p or "ultima oara" in p or "de cate ori" in p
                or "last time" in p or "last seen" in p)


def parse(q):
    """{"concept", "mod": "cand"|"cate", "days": int} dintr-o intrebare libera."""
    p = _plat(q)
    mod = "cate" if ("de cate ori" in p or re.match(r"^cate\b", p)
                     or "how many" in p or "how often" in p) else "cand"
    days = 30
    for expr, d in _FERESTRE:
        if expr in p:
            days = d
            p = p.replace(expr, " ")
            break
    for expr in _STOP:
        p = re.sub(r"\b" + re.escape(expr) + r"\b", " ", p)
    concept = re.sub(r"\s+", " ", p).strip(" .,")
    for art in _ARTICOLE:          # articolul nu face parte din concept („the garbage truck”)
        if concept.startswith(art):
            concept = concept[len(art):]
            break
    gol = concept
    return {"concept": concept, "mod": mod, "days": days,
            "label": _LABELS.get(gol)}


def _fmt(cand):
    """'2026-08-15 09:12:33' -> 'azi 09:12' / 'ieri 09:12' / 'vineri 15.08 09:12'."""
    st = time.strptime(cand, "%Y-%m-%d %H:%M:%S")
    azi = time.strftime("%Y-%m-%d")
    ieri = time.strftime("%Y-%m-%d", time.localtime(time.time() - 86400))
    hhmm = time.strftime("%H:%M", st)
    if cand[:10] == azi:
        return t("azi {ora}", ora=hhmm)
    if cand[:10] == ieri:
        return t("ieri {ora}", ora=hhmm)
    return f"{t(ZILE_RO[st.tm_wday])} {st.tm_mday:02d}.{st.tm_mon:02d} {hhmm}"


VEHICLE_LABELS = {"car", "truck", "bus", "motorcycle"}


def _epoch(cand):
    return time.mktime(time.strptime(cand, "%Y-%m-%d %H:%M:%S"))


def answer(q, visible=None, cam_names=None, cam=None):
    """Raspunsul complet la o intrebare: text in romana + aparitiile-dovada.

    Trei cai, dupa ce intreaba omul:
      1. eticheta pura ("cate masini") -> JURNALUL de detectii, numarare exacta
         a episoadelor de prezenta
      2. eticheta + atribut ("masini albe") -> SigLIP, dar DOAR peste pozele
         etichetate ca vehicule, cu grupare fina (2 min, nu 15)
      3. concept liber ("masina de gunoi") -> SigLIP peste tot
    cam = filtrul de camera ales in interfata; se intersecteaza cu drepturile."""
    import semantic
    p = parse(q)
    nume = cam_names or {}
    if cam:
        visible = {cam} if visible is None else (visible & {cam})
    if not p["concept"]:
        return {**p, "raspuns": t("Nu am inteles ce cauti — incearca de exemplu: „cand a venit masina de gunoi?”"),
                "aparitii": []}
    fereastra = (t("azi") if p["days"] == 1 else
                 t("in ultimele {n} zile", n=p["days"]) if p["days"] > 2 else t("de ieri incoace"))
    if cam:
        fereastra += ", " + t("pe {cam}", cam=nume.get(cam) or t("camera {id}", id=cam))

    def unde(g):
        return ", ".join(str(nume.get(c) or f"cam {c}") for c in g["camere"])

    # calea 1: eticheta pura -> intai TRACK-urile (numarare bucata cu bucata),
    # apoi jurnalul de detectii (episoade de prezenta) daca urmarirea nu acopera
    if p["label"]:
        import rules
        cutoff = time.time() - p["days"] * 86400
        urmarite = [e for e in rules.journal(limit=20000, tip="obiect")
                    if e.get("eticheta") == p["label"] and _epoch(e["t"]) >= cutoff
                    and (visible is None or e.get("cam") in visible)]
        if urmarite:
            ultima = urmarite[0]
            dirs = {}
            for e in urmarite:
                d = e.get("directie") or "?"
                dirs[d] = dirs.get(d, 0) + 1
            dir_txt = ", ".join(f"{n} {d}" for d, n in
                                sorted(dirs.items(), key=lambda x: -x[1]) if d != "?")
            text = (t("„{concept}”: exact {n} {fereastra}, numarate bucata cu bucata prin urmarire",
                      concept=p["concept"], n=len(urmarite), fereastra=fereastra)
                    + (f" ({dir_txt})" if dir_txt else "") + ". "
                    + t("Ultima: {cand} ({unde}).", cand=_fmt(ultima["t"]),
                        unde=nume.get(ultima.get("cam")) or "cam " + str(ultima.get("cam"))))
            grupuri = [{"inceput": e["t"], "sfarsit": e["t"],
                        "camere": [e.get("cam")], "poze": e.get("cadre", 1),
                        "scor": 1.0, "coperta": None, "durata": e.get("durata", 0)}
                       for e in urmarite[:20]]
            return {**p, "raspuns": text, "aparitii": grupuri, "top": 1.0}
        eps = [e for e in rules.episodes(limit=1000, tip="detectie")
               if e["detaliu"] == p["label"] and _epoch(e["inceput"]) >= cutoff
               and (visible is None or e.get("cam") in visible)]
        if not eps:
            return {**p, "raspuns": t("Nicio trecere de „{concept}” {fereastra}.", concept=p["concept"], fereastra=fereastra),
                    "aparitii": [], "top": 1.0}
        grupuri = [{"inceput": e["inceput"], "sfarsit": e["sfarsit"],
                    "camere": [e["cam"]], "poze": e["evenimente"],
                    "scor": 1.0, "coperta": None, "durata": e["durata"]}
                   for e in eps]
        ultima = grupuri[0]
        text = (t("„{concept}”: {n} treceri {fereastra} (din jurnalul de detectii).", concept=p["concept"], n=len(eps), fereastra=fereastra)
                + " " + t("Ultima: {cand} ({unde}).", cand=_fmt(ultima["inceput"]), unde=unde(ultima))
                + " " + t("Nota: masinile care trec una dupa alta la sub cateva minute se vad ca o singura trecere — pentru numaratoarea bucata cu bucata, porneste urmarirea pe camera respectiva (AI → Masini)."))
        return {**p, "raspuns": text, "aparitii": grupuri[:20], "top": 1.0}

    # calea 2/3: SigLIP — cu filtru de eticheta si grupare fina daca apare un vehicul
    tokens = [t for t in p["concept"].split() if t not in ("de", "cu", "culoare")]
    lbl_hibrid = next((_LABELS[t] for t in tokens if t in _LABELS), None)
    labels = None
    gap = semantic.APPEAR_GAP
    if lbl_hibrid:
        labels = VEHICLE_LABELS if lbl_hibrid in VEHICLE_LABELS else {lbl_hibrid}
        gap = 120
    grupuri, top = semantic.appearances(p["concept"], days=p["days"],
                                        visible=visible, gap=gap, labels=labels)
    if not grupuri:
        text = (t("Nu am gasit „{concept}” {fereastra}.", concept=p["concept"], fereastra=fereastra) + " "
                + t("Conceptul se cauta in pozele salvate la detectii — daca nu declanseaza detectia, nu are cum sa apara aici."))
        return {**p, "raspuns": text, "aparitii": [], "top": top}
    ultima = grupuri[0]
    if p["mod"] == "cate":
        text = (t("„{concept}”: aproximativ {n} aparitii distincte {fereastra}.", concept=p["concept"], n=len(grupuri), fereastra=fereastra)
                + " " + t("Ultima: {cand} ({unde}).", cand=_fmt(ultima["inceput"]), unde=unde(ultima)))
    else:
        text = t("Ultima data: {cand} ({unde}).", cand=_fmt(ultima["inceput"]), unde=unde(ultima))
        if len(grupuri) > 1:
            text += " " + t("Inainte de asta: {cand}.", cand=_fmt(grupuri[1]["inceput"]))
        text += " " + t("In total {n} aparitii {fereastra}.", n=len(grupuri), fereastra=fereastra)
    # Onestitate: sub pragul asta, SigLIP potriveste si "girafa cu elicopterul" pe
    # cate ceva (masurat: nonsensul da ~0.075, potrivirile bune 0.08+). Mai bine
    # spunem ca nu suntem siguri decat sa raspundem cu aplomb la orice.
    if top < 0.08:
        text += " " + t("⚠ Potrivirea e slaba — verifica pozele, s-ar putea sa ma insel.")
    return {**p, "raspuns": text, "aparitii": grupuri[:20], "top": top}
