#!/usr/bin/env python3
"""Harta modulelor (docs/module.md), generată din docstring-urile și antetele modulelor.

Fiecare modul Python din app/ (plus config.py și gen_go2rtc.py din rădăcină) și fiecare modul ES
din app/static/js/ trebuie să aibă un domeniu în DOMENII. Descrierea vine din prima linie a
docstring-ului (Python) sau din antetul fișierului (JS), deci harta nu se învechește singură.

    python3 scripts/module_index.py           # verifică: pică dacă un modul n-are domeniu
                                              # sau dacă docs/module.md nu e la zi
    python3 scripts/module_index.py --scrie   # rescrie docs/module.md
"""
import ast
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs", "module.md")
RADACINA = ("config", "gen_go2rtc")   # module Python din rădăcina repo-ului, nu din app/

# (titlu, module Python, module ES). Ordinea de aici e ordinea din hartă.
DOMENII = [
    ("Pornire, configurare, infrastructură",
     "config server_main server db store settings logare sdnotify i18n health metrici jobs routes_jobs routes_system",
     "00-core 05-navigare state"),
    ("Conturi, roluri și acces",
     "users totp routes_auth scoping audit",
     ""),
    ("Camere și flux video",
     "gen_go2rtc stream_proxy camctl drivers onvif discover masca routes_cameras audio_level noise_stats",
     "10-live 15-flux 20-hd 50-camere"),
    ("Înregistrare, redare, probe, date",
     "record_main record continuity lant reencode probe routes_probe marcaje routes_marcaje verificare "
     "routes_media routes_date backup backup_nocturn",
     "30-redare 45-fisa"),
    ("Evenimente, reguli, notificări",
     "hooks rules reguli jurnal snaps routes_automation mqtt notify push routes_push telegram_bot digest "
     "escaladare mod detectie_feedback fata_feedback numar_feedback",
     "40-evenimente 65-program"),
    ("Incidente și triaj",
     "incidents routes_incidents cases absenta episoade stationar",
     ""),
    ("AI: serviciul nvr-ai și percepția",
     "ai_main ai_client ai_track ai_analytics ai_scene detect verify track faces plates reid reid_arhiva "
     "describe cadere cadere_pose scorer_fp recomandari vreme llm routes_analytics mcp_server",
     "46-reid 70-ai"),
    ("„Învață scena”",
     "scena scena_web scena_clasa scena_fundal scena_lume",
     ""),
    ("Antrenare și dataset",
     "antrenare adnotare nesigure routes_ai",
     "75-adnotare"),
    ("Căutare semantică și întrebări",
     "semantic semantic_store semantic_feedback semantic_pg ask search_journal search_time",
     "48-cauta 49-semantic-review"),
    ("Harta proprietății și analitică",
     "harta plan_metrica omografie routes_operare analiza routes_analiza",
     "24-harta-metrica 25-harta 26-harta-calibrare 27-harta-limita"),
    ("Setări (interfață)",
     "",
     "60-setari"),
]

# Ce caută des cine intră prima dată în cod și NU e unde s-ar aștepta. Căile din `…` sunt verificate.
UNDE = [
    ("autentificare, sesiuni, 2FA",
     "`app/users.py` (conturi, roluri, revocarea sesiunilor), `app/totp.py`, `app/routes_auth.py`; "
     "nu există `app/auth.py`"),
    ("cine are voie la ce (viewer / operator, CSRF)",
     "garda `guard()` din `app/server.py` (`VIEWER_GET`, `OPERATOR_ACTIONS`); restricția pe camere în "
     "`app/scoping.py`"),
    ("versiunea", "`config.py` din rădăcină; nu există `app/version.py` și nici `pyproject.toml`"),
    ("configurarea go2rtc", "`gen_go2rtc.py` din rădăcină (nu din `app/`), rulat la pornirea serviciului go2rtc"),
    ("dosare de caz", "`app/cases.py`"),
    ("segmente, lanțul de custodie", "`app/lant.py`"),
    ("arhivă, retenție", "`app/record.py`"),
    ("jurnalul de evenimente",
     "`app/jurnal.py`, în PostgreSQL (tabelul `events`); fișierele `events.*` din `data/` sunt rămășițe"),
    ("migrări de schemă", "dicționarul `MIGRARI` din modulul respectiv, aplicat de `aplica()` din `app/db.py`"),
    ("testele de securitate",
     "`tests/test_authz.py`, `tests/test_auth_2fa.py`, `tests/test_stream_proxy.py`, `tests/test_audit.py`; "
     "nu există `tests/test_security.py`"),
    ("HTML și CSS",
     "o singură pagină, `app/static/index.html`, și un singur `app/static/style.css`; "
     "nu există `templates/` sau `css/`"),
    ("pachetul Python", "`app/` nu e pachet: fără `__init__.py`, importurile sunt plate (`import jobs`)"),
]


def _py(nume):
    cale = os.path.join(ROOT, nume + ".py") if nume in RADACINA else os.path.join(ROOT, "app", nume + ".py")
    return cale, (nume + ".py") if nume in RADACINA else f"app/{nume}.py"


def _rand_py(cale):
    """Primul paragraf al docstring-ului, pe un rând (liniile de listă `- …` nu intră)."""
    doc = ast.get_docstring(ast.parse(open(cale, encoding="utf-8").read())) or ""
    rand = []
    for linie in doc.strip().splitlines():
        if not linie.strip() or linie.lstrip().startswith(("- ", "* ")):
            break
        rand.append(linie.strip())
    return " ".join(rand)


def _rand_js(cale):
    """Prima linie de text din comentariul de la începutul fișierului (sare peste ====)."""
    for linie in open(cale, encoding="utf-8").read().splitlines()[:8]:
        t = re.sub(r"^\s*(?:/\*+|\*+|//+)\s*", "", linie).strip()
        t = re.sub(r"\s*\*/$", "", t)
        if t and not set(t) <= set("=*-/"):
            return t
    return ""


def _scurt(t, n=140):
    return t if len(t) <= n else t[:n - 1].rstrip() + "…"


def module_existente():
    py = {f[:-3] for f in os.listdir(os.path.join(ROOT, "app")) if f.endswith(".py")} | set(RADACINA)
    js = {f[:-3] for f in os.listdir(os.path.join(ROOT, "app", "static", "js")) if f.endswith(".js")}
    return py, js


def probleme_domenii():
    py, js = module_existente()
    vazut_py, vazut_js, out = [], [], []
    for titlu, p, j in DOMENII:
        vazut_py += p.split()
        vazut_js += j.split()
    for tip, vazut, real in (("Python", vazut_py, py), ("ES", vazut_js, js)):
        for m in sorted(set(vazut)):
            if vazut.count(m) > 1:
                out.append(f"modulul {tip} {m} apare în mai multe domenii")
        for m in sorted(set(vazut) - real):
            out.append(f"modulul {tip} {m} e în DOMENII dar nu există")
        for m in sorted(real - set(vazut)):
            out.append(f"modulul {tip} {m} n-are domeniu: adaugă-l în DOMENII din scripts/module_index.py")
    return out


def probleme_unde():
    out = []
    for _, text in UNDE:
        for cale in re.findall(r"`([\w./-]+\.(?:py|html|css|js))`", text):
            neg = re.search(r"(?:nu există|fără)[^;]*`" + re.escape(cale) + "`", text)
            if not neg and not os.path.exists(os.path.join(ROOT, cale)):
                out.append(f"UNDE trimite la {cale}, care nu există")
    return out


def genereaza():
    r = ["# Harta modulelor", "",
         "Generat de `scripts/module_index.py` din docstring-urile și antetele modulelor; nu edita de mână.",
         "Un modul nou primește un domeniu în `DOMENII` din script (CI pică altfel), apoi",
         "`python3 scripts/module_index.py --scrie`.", "",
         "## Unde e…", "", "| Caut | E în |", "|---|---|"]
    r += [f"| {k} | {v} |" for k, v in UNDE]
    for titlu, p, j in DOMENII:
        r += ["", f"## {titlu}", "", "| Fișier | Rol |", "|---|---|"]
        for m in p.split():
            cale, afis = _py(m)
            r.append(f"| `{afis}` | {_scurt(_rand_py(cale)).replace('|', '/')} |")
        for m in j.split():
            r.append(f"| `app/static/js/{m}.js` | "
                     f"{_scurt(_rand_js(os.path.join(ROOT, 'app', 'static', 'js', m + '.js'))).replace('|', '/')} |")
    return "\n".join(r) + "\n"


def main():
    probleme = probleme_domenii() + probleme_unde()
    if probleme:
        print("\n".join(probleme))
        return 1
    text = genereaza()
    if "--scrie" in sys.argv:
        with open(DOC, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"scris {os.path.relpath(DOC, ROOT)}")
        return 0
    actual = open(DOC, encoding="utf-8").read() if os.path.exists(DOC) else ""
    if actual != text:
        print("docs/module.md nu e la zi: python3 scripts/module_index.py --scrie")
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
