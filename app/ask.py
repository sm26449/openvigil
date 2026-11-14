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
import search_time

from i18n import t

ZILE_RO = ["luni", "marți", "miercuri", "joi", "vineri", "sâmbătă", "duminică"]

# expresii care fac parte din intrebare, nu din concept (fara diacritice, lowercase)
_STOP = [
    "cand a venit", "cand au venit", "cand a fost", "cand au fost", "cand a aparut",
    "cand a trecut", "cand am vazut", "cand s-a vazut", "ultima data", "ultima oara",
    "prima data", "prima oara", "de cate ori", "cate ori", "de cand", "a venit", "au venit",
    "a aparut", "au aparut", "a trecut", "au trecut", "a fost", "au fost",
    "pe la noi", "la noi", "pe aici", "pe acasa", "pe strada", "prin fata",
    "cand", "cine", "cate", "cati", "oare", "vazut", "vazuta",
    # engleza
    "when did", "when was", "when were", "when has", "when have", "what time did",
    "the last time", "last time", "first time", "last seen", "how many times", "how many", "how often",
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
                or "last time" in p or "last seen" in p or 'prima data' in p or 'prima oara' in p or 'first time' in p)


def parse(q, days=30):
    """{"concept", "mod": "cand"|"cate", "days": int} dintr-o intrebare libera."""
    p = _plat(q)
    mod = "cate" if ("de cate ori" in p or re.match(r"^(cate|cati)\b", p)
                     or "how many" in p or "how often" in p) else "cand"
    first = bool(re.search(r'\b(prima data|prima oara|first time|first)\b', p))
    p, period = search_time.parse_period(p, days)
    for expr in _STOP:
        p = re.sub(r"\b" + re.escape(expr) + r"\b", " ", p)
    concept = re.sub(r"\s+", " ", p).strip(" .,")
    for art in _ARTICOLE:          # articolul nu face parte din concept („the garbage truck”)
        if concept.startswith(art):
            concept = concept[len(art):]
            break
    gol = concept
    return {"concept": concept, "mod": mod, **period, 'first': first,
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


def answer(q, visible=None, cam_names=None, cam=None, days=30):
    """Raspunsul complet la o intrebare: text in romana + aparitiile-dovada.

    Trei cai, dupa ce intreaba omul:
      1. eticheta pura ("cate masini") -> JURNALUL de detectii, numarare exacta
         a episoadelor de prezenta
      2. eticheta + atribut ("masini albe") -> SigLIP, dar DOAR peste pozele
         etichetate ca vehicule, cu grupare fina (2 min, nu 15)
      3. concept liber ("masina de gunoi") -> SigLIP peste tot
    cam = filtrul de camera ales in interfata; se intersecteaza cu drepturile."""
    import semantic
    nume = cam_names or {}
    text = _plat(q)
    # Aliasurile sunt doar numele camerelor deja vizibile, nu locatii ghicite de model.
    aliases = {}
    for id_, name in nume.items():
        if visible is None or id_ in visible:
            aliases.setdefault(_plat(name), []).append(id_)
    for name in sorted(aliases, key=len, reverse=True):
        if not name:
            continue
        pattern = r'\b(?:la|pe|camera|at|on)\s+(?:camera\s+)?' + re.escape(name) + r'\b'
        if re.search(pattern, text):
            if len(aliases[name]) != 1:
                return {'error': t('Numele camerei este ambiguu; selectează camera din filtru.'), 'aparitii': []}
            alias = aliases[name][0]
            if cam and int(cam) != alias:
                return {'error': t('Camera din întrebare diferă de camera selectată.'), 'aparitii': []}
            cam = alias
            text = re.sub(pattern, ' ', text)
            break
    try:
        p = parse(text, days)
    except ValueError:
        return {'error': t('Nu am înțeles perioada sau orele; încearcă „ieri între 8 și 10”.'), 'aparitii': []}
    if cam:
        cam = int(cam)
        visible = {cam} if visible is None else (visible & {cam})
    if not p["concept"]:
        return {**p, "raspuns": t("Nu am înțeles ce cauți — încearcă de exemplu: „când a venit mașina de gunoi?”"),
                "aparitii": []}
    fereastra = t('între {start} și {end}',
                  start=time.strftime('%Y-%m-%d %H:%M', time.localtime(p['since'])),
                  end=time.strftime('%Y-%m-%d %H:%M', time.localtime(p['until'])))
    if p['hours']:
        a, b = p['hours']
        fereastra += ' · ' + t('orele {start}–{end}', start=f'{a//60:02d}:{a%60:02d}', end=f'{b//60:02d}:{b%60:02d}')
    if cam:
        fereastra += ", " + t("pe {cam}", cam=nume.get(cam) or t("camera {id}", id=cam))

    def unde(g):
        return ", ".join(str(nume.get(c) or f"cam {c}") for c in g["camere"])

    coverage = semantic.coverage(days=p['days'], visible=visible, since=p['since'], until=p['until'], hours=p['hours'])
    base = {**p, 'coverage': coverage, 'period': fereastra, 'cam': cam}

    def finish(text, groups, top, **extra):
        text += ' ' + t('Poze de detecție în perioada căutată: {indexed}/{saved} indexate.',
                        indexed=coverage['indexed'], saved=coverage['saved'])
        text += ' ' + t('Caut în imaginile salvate și indexate; nu verific întreaga înregistrare video.')
        if coverage['missing']:
            text += ' ' + t('{n} imagini salvate nu sunt încă indexate.', n=coverage['missing'])
        return {**base, 'raspuns': text, 'aparitii': groups[:20], 'top': top, **extra}

    # calea 1: eticheta pura -> intai TRACK-urile (numarare bucata cu bucata),
    # apoi jurnalul de detectii (episoade de prezenta) daca urmarirea nu acopera
    if p["label"]:
        import search_journal
        found = search_journal.find(p['label'], p['since'], p['until'], visible, p['hours'], p['first'])
        grupuri = found['groups']
        text = t('Jurnal: {tracked} obiecte urmărite și {episodes} episoade de detecție.',
                 tracked=found['tracked'], episodes=found['episodes'])
        if found.get('directions'):
            text += ' (' + ', '.join(f'{n} {d}' for d,n in sorted(found['directions'].items())) + ')'
        text += ' ' + t('Sursele se pot suprapune; acestea sunt observații salvate, nu o numărătoare completă a trecerilor.')
        if grupuri:
            g = grupuri[0]
            text += ' ' + (t('Prima apariție găsită: {cand} ({unde}).', cand=_fmt(g['inceput']), unde=unde(g))
                           if p['first'] else t('Ultima: {cand} ({unde}).', cand=_fmt(g['inceput']), unde=unde(g)))
        else:
            text += ' ' + t('Nu am găsit dovezi în perioada căutată; aceasta nu confirmă absența unei treceri.')
        return finish(text, grupuri, 1., counts={k: v for k, v in found.items() if k != 'groups'})

    # calea 2/3: SigLIP — cu filtru de eticheta si grupare fina daca apare un vehicul
    tokens = [t for t in p["concept"].split() if t not in ("de", "cu", "culoare")]
    lbl_hibrid = next((_LABELS[t] for t in tokens if t in _LABELS), None)
    labels = None
    gap = semantic.APPEAR_GAP
    if lbl_hibrid:
        labels = VEHICLE_LABELS if lbl_hibrid in VEHICLE_LABELS else {lbl_hibrid}
        gap = 120
    grupuri, top = semantic.appearances(p["concept"], days=p["days"],
                                        visible=visible, gap=gap, labels=labels,
                                        since=p['since'], until=p['until'], hours=p['hours'])
    if p['first']:
        grupuri.reverse()
    if not grupuri:
        text = (t("Nu am găsit „{concept}” {fereastra}.", concept=p["concept"], fereastra=fereastra) + " "
                + t("Conceptul se caută în pozele salvate la detecții — dacă nu declanșează detecția, nu are cum să apară aici."))
        text += ' ' + t('Nu am găsit dovezi în perioada căutată; aceasta nu confirmă absența unei treceri.')
        return finish(text, [], top)
    ultima = grupuri[0]
    if p["mod"] == "cate":
        text = (t("„{concept}”: aproximativ {n} apariții distincte {fereastra}.", concept=p["concept"], n=len(grupuri), fereastra=fereastra)
                + " " + t("Ultima: {cand} ({unde}).", cand=_fmt(ultima["inceput"]), unde=unde(ultima)))
    else:
        text = (t('Prima apariție găsită: {cand} ({unde}).', cand=_fmt(ultima['inceput']), unde=unde(ultima))
                if p['first'] else t("Ultima data: {cand} ({unde}).", cand=_fmt(ultima["inceput"]), unde=unde(ultima)))
        if len(grupuri) > 1 and not p['first']:
            text += " " + t("Înainte de asta: {cand}.", cand=_fmt(grupuri[1]["inceput"]))
        text += " " + t("În total {n} apariții {fereastra}.", n=len(grupuri), fereastra=fereastra)
    # Onestitate: sub pragul asta, SigLIP potriveste si "girafa cu elicopterul" pe
    # cate ceva (masurat: nonsensul da ~0.075, potrivirile bune 0.08+). Mai bine
    # spunem ca nu suntem siguri decat sa raspundem cu aplomb la orice.
    if any(g.get('weak', g['scor'] < .08) for g in grupuri[:20]):
        text += " " + t("⚠ Potrivirea e slabă — verifică pozele, s-ar putea să mă înșel.")
    return finish(text, grupuri, top, total=len(grupuri), count_complete=False)
