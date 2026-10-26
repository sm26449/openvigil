"""Interfata multilingva (i18n): romana e limba sursa, restul sunt dictionare JSON.

Cheia unui text e chiar textul romanesc din cod, ca la gettext: `t("Adaugă camera")`.
In romana t() intoarce cheia neschimbata; in alta limba cauta in dictionarul
`app/static/i18n/<limba>.json` ({"Adaugă camera": "Add camera"}). Un text lipsa din
dictionar ramane in romana — niciodata o eroare, niciodata o cheie tehnica pe ecran.
Un operator poate corecta sau adauga limbi fara sa atinga aplicatia: fisierele din
`data/i18n/<limba>.json` se aplica peste cele din aplicatie (si peste romana).

Parametri: `t("Camera {nume} nu răspunde", nume=x)` — {nume} se inlocuieste DUPA traducere,
ca ordinea cuvintelor sa poata diferi intre limbi; de aceea textele nu se lipesc din bucati.
Plural: valoarea din dictionar poate fi un obiect {"one": ..., "few": ..., "other": ...},
ales dupa parametrul `n` cu regulile limbii (romana are trei forme: 1 cameră, 2 camere,
20 de camere).

index.html si formularele de login sunt traduse pe server, nod de text cu nod de text si
atribut cu atribut (title, placeholder, aria-label, alt...), prin `pagina()`: fara marcaje
in HTML si fara sclipirea textului romanesc inainte sa porneasca JavaScript-ul. Textul din
JavaScript trece prin t() din 00-core.js, cu acelasi dictionar servit la /i18n/<limba>.json.

Limba unei cereri, in ordine: ?lang= in URL (ramane in cookie), preferinta contului
(Contul meu), cookie-ul `lang`, Accept-Language al browserului, NVR_LANG din .env (care e
si limba notificarilor, unde nu exista browser).
"""
import html as _html
import json
import os
import re
import threading
from html.parser import HTMLParser

import config as C

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_APP = os.path.join(ROOT, "app", "static", "i18n")
DIR_DATE = os.path.join(ROOT, "data", "i18n")
SURSA = "ro"
NUME_SURSA = "Română"
# atributele HTML al caror text il vede utilizatorul
ATRIBUTE = ("title", "placeholder", "aria-label", "alt", "label", "data-gol")
# cheile cer cel putin o litera: „✕”, „—”, „0”, „%” nu se traduc
_LITERA = re.compile(r"[^\W\d_]")
_PARAM = re.compile(r"\{(\w+)\}")
_COD = re.compile(r"^[a-z]{2,3}(-[A-Za-z]{2,4})?$")

_lock = threading.Lock()
_dict = {}       # limba -> {cheie: text | {forma: text}}
_nume = {}       # limba -> numele limbii, in limba ei
_pagini = {}     # (id sursa, limba) -> HTML tradus
pref_utilizator = None   # setat de server: () -> limba preferata a contului curent sau None


def _citeste(cale):
    try:
        with open(cale, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def incarca():
    """(Re)citeste dictionarele: cele din aplicatie, apoi cele din data/i18n peste ele."""
    dic, nume = {}, {SURSA: NUME_SURSA}
    for folder in (DIR_APP, DIR_DATE):
        if not os.path.isdir(folder):
            continue
        for fn in sorted(os.listdir(folder)):
            if not fn.endswith(".json"):
                continue
            cod = fn[:-5]
            if not _COD.match(cod):
                continue
            d = _citeste(os.path.join(folder, fn))
            meta = d.pop("_meta", None)
            if isinstance(meta, dict) and meta.get("nume"):
                nume[cod] = str(meta["nume"])
            dic.setdefault(cod, {}).update(d)
            nume.setdefault(cod, cod)
    with _lock:
        _dict.clear(); _dict.update(dic)
        _nume.clear(); _nume.update(nume)
        _pagini.clear()


def disponibile():
    """[(cod, nume), ...] — romana intai, apoi restul in ordinea codului."""
    return [(SURSA, _nume.get(SURSA, NUME_SURSA))] + sorted(
        (c, n) for c, n in _nume.items() if c != SURSA)


def coduri():
    return [c for c, _ in disponibile()]


def dictionar(limba):
    """Dictionarul servit interfetei (fara _meta); gol pentru o limba necunoscuta."""
    return dict(_dict.get(limba, {}))


def plural(limba, n):
    """Forma de plural (CLDR) pentru n: one / few / other."""
    try:
        n = abs(float(n))
    except (TypeError, ValueError):
        return "other"
    intreg = n == int(n)
    if limba == SURSA:
        if n == 1 and intreg:
            return "one"
        if not intreg or n == 0 or 1 <= n % 100 <= 19:
            return "few"
        return "other"
    if limba in ("fr", "pt"):
        return "one" if n in (0, 1) else "other"
    return "one" if n == 1 and intreg else "other"


def t(cheie, limba=None, **p):
    """Textul `cheie` in limba data (implicit: a cererii curente), cu {parametrii} inlocuiti."""
    limba = limba or limba_curenta()
    v = _dict.get(limba, {}).get(cheie)
    if isinstance(v, dict):
        forma = plural(limba, p["n"]) if p.get("n") is not None else "other"
        v = v.get(forma) or v.get("other")
    if not isinstance(v, str) or not v:
        v = cheie
    if p:
        v = _PARAM.sub(lambda m: str(p[m[1]]) if m[1] in p else m[0], v)
    return v


def limba_valida(cod):
    return cod if cod and _COD.match(cod) and cod in coduri() else None


def limba_curenta():
    """Limba cererii curente (vezi ordinea din antet); in afara unei cereri, NVR_LANG."""
    implicit = limba_valida(getattr(C, "LIMBA", SURSA)) or SURSA
    try:
        from flask import g, has_request_context, request
    except ImportError:            # procesele fara Flask (record, ai)
        return implicit
    if not has_request_context():
        return implicit
    if getattr(g, "limba", None):
        return g.limba
    limba = limba_valida(request.args.get("lang"))
    if limba:
        g.limba_cookie = limba    # server.py o pune in cookie la raspuns
    if not limba and pref_utilizator:
        try:
            limba = limba_valida(pref_utilizator())
        except Exception:
            limba = None
    if not limba:
        limba = limba_valida(request.cookies.get("lang"))
    if not limba:
        limba = request.accept_languages.best_match(coduri())
    g.limba = limba or implicit
    return g.limba


# ---------------------------------------------------------------- HTML static
class _Pagina(HTMLParser):
    """Rescrie un document HTML traducandu-i nodurile de text si atributele vizibile.
    Tot ce nu se traduce (tag-uri, comentarii, script, style) iese neschimbat, octet cu
    octet, din textul sursa — nu re-serializam documentul, deci `viewBox` ramane `viewBox`."""
    _ATTR = {a: re.compile(r'(\s' + re.escape(a) + r'\s*=\s*)(?:"([^"]*)"|\'([^\']*)\')')
             for a in ATRIBUTE}

    _BRUT = ("pre", "textarea", "code")      # text vizibil, dar cu spatiile si continutul lui
    _CDATA = ("script", "style")              # parser-ul nu decodeaza entitatile aici

    def __init__(self, limba, chei=None):
        super().__init__(convert_charrefs=True)
        self.limba, self.chei, self.out = limba, chei, []
        self.brut, self.cdata = 0, 0

    def _tradus(self, text):
        cheie = " ".join(text.split())
        if not cheie or not _LITERA.search(cheie):
            return None
        if self.chei is not None:
            self.chei.append(cheie)
            return None
        v = t(cheie, self.limba)
        return None if v == cheie else v

    def _tag(self, tag, attrs, raw):
        if tag == "html":
            raw = re.sub(r'\slang="[^"]*"', f' lang="{self.limba}"', raw, count=1)
        for a, val in attrs:
            if a in self._ATTR and val:
                nou = self._tradus(val)
                if nou is not None:
                    raw = self._ATTR[a].sub(lambda m: m[1] + '"' + _html.escape(nou, quote=True) + '"',
                                            raw, count=1)
        self.out.append(raw)

    def handle_starttag(self, tag, attrs):
        self._tag(tag, attrs, self.get_starttag_text())
        if tag in self._BRUT:
            self.brut += 1
        elif tag in self._CDATA:
            self.cdata += 1

    def handle_startendtag(self, tag, attrs):
        self._tag(tag, attrs, self.get_starttag_text())

    def handle_endtag(self, tag):
        if tag in self._BRUT and self.brut:
            self.brut -= 1
        elif tag in self._CDATA and self.cdata:
            self.cdata -= 1
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if self.cdata:
            self.out.append(data)
            return
        if self.brut or not data.strip():
            self.out.append(_html.escape(data, quote=False))
            return
        nou = self._tradus(data)
        if nou is None:
            self.out.append(_html.escape(data, quote=False) if self.chei is None else data)
            return
        stanga = data[:len(data) - len(data.lstrip())]
        dreapta = data[len(data.rstrip()):]
        self.out.append(stanga + _html.escape(nou, quote=False) + dreapta)

    def handle_comment(self, data):
        self.out.append(f"<!--{data}-->")

    def handle_decl(self, decl):
        self.out.append(f"<!{decl}>")

    def handle_pi(self, data):
        self.out.append(f"<?{data}>")

    def unknown_decl(self, data):
        self.out.append(f"<![{data}]>")


def pagina(sursa, limba=None):
    """Documentul HTML `sursa` tradus in `limba` (memorat per text sursa si limba)."""
    limba = limba or limba_curenta()
    cheie = (hash(sursa), limba)
    with _lock:
        gata = _pagini.get(cheie)
    if gata is None:
        p = _Pagina(limba)
        p.feed(sursa)
        p.close()
        gata = "".join(p.out)
        with _lock:
            if len(_pagini) > 64:
                _pagini.clear()
            _pagini[cheie] = gata
    return gata


def extrage_html(sursa):
    """Cheile (textele vizibile) dintr-un document HTML, in ordinea aparitiei, fara dubluri."""
    chei = []
    p = _Pagina(SURSA, chei)
    p.feed(sursa)
    p.close()
    return list(dict.fromkeys(chei))


incarca()
