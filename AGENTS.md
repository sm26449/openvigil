# AGENTS.md — OpenVigil

Ghid pentru agenții de cod (Claude Code, Codex, Cursor, Copilot). Public: nu conține nimic
despre o instalare anume. Detaliile unei mașini (căi, porturi locale, comenzi de deploy) stau
într-un `CLAUDE.md` / `CLAUDE.local.md` local, ignorat de git: **dacă există, citește-l după
acesta** — are prioritate pentru tot ce ține de mașina respectivă.

## 1. Ce e proiectul, în 5 rânduri

NVR self-hosted (AGPL): înregistrare continuă, live/redare în browser, detecție AI pe GPU,
triaj de incidente, hartă a proprietății, căutare semantică. **O singură imagine Docker**,
cinci servicii în Compose, rețea `host`: `web` (Flask, interfață + API), `ai` (modelele, GPU),
`record` (înregistrare), `go2rtc` (fluxuri video), `db` (PostgreSQL + pgvector).
Codul e în română (nume de module, funcții, comentarii); interfața e ro + en.

## 2. Înainte să cauți: harta

**Citește întâi `docs/module.md`** — fiecare modul, pe domenii, cu rolul lui și un tabel
„Unde e…” pentru ce NU e unde te-ai aștepta. E generat din docstring-uri și verificat în CI.
Prima linie a docstring-ului oricărui modul spune ce face: `head -3 app/<modul>.py` ajunge.

Capcane dovedite (fiecare a costat sesiuni întregi de căutat):

| Cauți | E în | NU există |
|---|---|---|
| autentificare, 2FA, sesiuni | `app/users.py`, `app/totp.py`, `app/routes_auth.py` | `app/auth.py` |
| cine are voie la ce | garda `guard()` din `app/server.py` (`VIEWER_GET`, `OPERATOR_ACTIONS`), `app/scoping.py` | — |
| versiunea | `config.py` din **rădăcină** | `app/version.py`, `pyproject.toml` |
| configurarea go2rtc | `gen_go2rtc.py` din **rădăcină** | `app/gen_go2rtc.py` |
| dosare de caz | `app/cases.py` | `app/dosare.py` |
| lanțul de custodie (segmente) | `app/lant.py` | `app/segmente.py` |
| arhivă, retenție | `app/record.py` | `app/arhiva.py` |
| jurnalul de evenimente | `app/jurnal.py` (PostgreSQL, tabelul `events`) | `data/events.jsonl`, `events.db` (rămășițe) |
| migrări de schemă | `MIGRARI` din modulul respectiv + `db.aplica()` | `app/schema.py` |
| teste de securitate | `tests/test_authz.py`, `test_auth_2fa.py`, `test_stream_proxy.py`, `test_audit.py` | `tests/test_security.py` |
| HTML / CSS | `app/static/index.html`, `app/static/style.css` (câte unul) | `templates/`, `css/` |
| pachet Python | `app/` e **plat**: `import jobs`, fără `__init__.py` | — |
| rute HTTP | blueprint-urile `app/routes_*.py`; `/api/journal` e în `routes_automation.py`, harta + modul acasă/plecat în `routes_operare.py`, căutarea semantică în `routes_analytics.py` | — |

Fișierele mari (1200–2200 de linii: `style.css`, `index.html`, `js/60-setari.js`,
`js/50-camere.js`, `js/40-evenimente.js`, `server.py`, `semantic.py`): caută cu
`grep -n` funcția sau selectorul, apoi citește doar intervalul. Nu le citi întregi.

## 3. Glosar (codul e în română)

harta = harta proprietății · redare = playback · inregistrare = recording · jurnal = event log ·
incident / triaj = incident triage · dosar / cases = evidence case · probe = evidence holds ·
lant = chain of custody · marcaje = bookmarks · setari = settings · camere = cameras ·
mod acasa/plecat/noapte = home/away/night · escaladare = alert escalation (serviciu extern) ·
absenta = „cât ai lipsit” · omografie = homography camera→plan · plan_metrica = plan scale ·
scena („Învață scena”) = change detection learned per camera · paznic = watchdog ·
masca = privacy mask · releu = authenticated stream relay (`stream_proxy.py`).

## 4. Arhitectura care contează

- **Web ↔ AI:** `web` nu încarcă modele; vorbește cu `ai` prin `app/ai_client.py`
  (contract în `docs/ai-contract.md`). Un script care importă `detect`/`semantic` direct în
  serviciul `ai` are nevoie de `detect.IS_HOST = True` (și `semantic.IS_HOST = True`),
  altfel deleagă înapoi la serviciu.
- **Fluxul video:** browserul **nu** vorbește cu go2rtc. go2rtc ascultă doar pe 127.0.0.1;
  browserul trece prin releul autentificat `app/stream_proxy.py`, care acceptă doar nume de
  flux publicate și reverifică accesul periodic.
- **Un eveniment:** `hooks.emit(tip, cam, **date)` → `rules.on_event` (jurnal + reguli +
  acțiuni) → `mqtt.publish_event` → webhook. Notificări: `notify.py`, `push.py`, `escaladare.py`.
- **Lucru lung = job:** `jobs.submit(...)` → 202 + `/api/jobs/<id>`; UI prin `runJob`.
- **Importuri:** `server.py` înregistrează blueprint-urile, iar `routes_*` importă din
  `server` (`current_user`, `_visible_ids`, `get_cameras`). Multe importuri sunt amânate în
  funcții tocmai ca să rupă cicluri: nu le muta la începutul fișierului.
- **Interfața:** module ES fără pas de build (`app/static/js/NN-ecran.js`, intrarea `app.js`).
  Variabilă scrisă din mai multe module → `state.js` (`S.x`). Export/import explicit, verificat
  de `scripts/module_check.py`. Orice `innerHTML` trece prin șablonul `html```(CSP strict).

## 5. Reguli care nu se negociază

1. **Repo public.** Nimic despre o instalare reală în git: nume de camere, IP-uri, domenii,
   parole, jetoane, capturi. Rapoartele locale merg în `docs/audits/` (exclus din git).
2. **Nicio imagine de la camere reale** în repo, documentație, site sau release. Capturile se
   fac pe o instanță demo cu date sintetice, sau cu blur.
3. **Text nou în interfață:** `t("text românesc cu diacritice")` + o linie în
   `app/static/i18n/en.json`. CI pică pe chei lipsă (`i18n_check.py`) și pe texte fără
   diacritice (`diacritice_check.py`).
4. **Modul Python nou:** docstring pe prima linie + domeniu în `DOMENII` din
   `scripts/module_index.py`, apoi `python3 scripts/module_index.py --scrie`.
5. **Schimbare de schemă** = migrare numerotată în `MIGRARI`, niciodată `ALTER` ad-hoc.
6. **pytest NU rulează pe baza de producție**: `tests/conftest.py` golește tabelul `events`.
   Rulează doar pe un PostgreSQL de unică folosință, marcat cu `OPENVIGIL_TEST_DB=1`
   (protecția și motivul sunt descrise la începutul lui `conftest.py`).
7. **Commit-uri:** Conventional Commits în română, fără referințe la asistenți AI în mesaj.
   Push, PR, merge, tag **doar la cererea explicită** a omului; release public rar
   (cel mult o dată pe săptămână), dezvoltarea și testarea se fac local.
8. **Nu reporni `record` sau `go2rtc` fără să anunți**: întrerupe înregistrarea.
9. **Secretele vin din mediu**, niciodată din chat sau din cod; nu le afișa în ieșiri.

## 6. Logica de domeniu pe care agenții au greșit-o

- **Geometria camerelor:** înălțimea și înclinarea sunt introduse de om, aproximativ. Când
  o calibrare (omografie) le contrazice, **nu suprascrie valorile omului**: arată
  diferența și propune. Zona de sol a unei camere calibrate vine din calibrare.
- **Planul hărții nu e la scară uniformă:** un singur factor m/px nu ajunge. Scara vine din
  limita proprietății cu laturi cunoscute (metrică anizotropă), cu unghiuri și arie.
- **Alertele de sistem** pleacă prin serviciul extern de notificări (`escaladare.py`,
  contract în `docs/alerte.md`); botul Telegram al NVR-ului e rezerva. Contractul real are
  mai multe stări decât `ok`: tratează-le pe toate înainte de a cădea pe rezervă.
- **Totul se învață din interfață**, nu din scripturi: etichetări, exemple semantice,
  verdicte. O funcție nouă de învățare primește UI.
- **Telefonul și PWA-ul instalat sunt platforme de primă clasă**: verifică pe lățime de
  telefon și în modul ecran complet, nu doar pe desktop.
- **Lucrul se face pe faze**: o fază, testată, verificată, apoi următoarea; la sfârșit un
  audit, apoi publicarea.

## 7. Verificări înainte de a spune „gata”

```
python3 -m compileall -q app scripts config.py gen_go2rtc.py
python3 scripts/module_check.py && python3 scripts/module_index.py
python3 scripts/i18n_check.py && python3 scripts/diacritice_check.py
# pytest doar cu OPENVIGIL_TEST_DB=1 pe o bază de test; regresia de interfață: cd tests && npm run test:ui
```

După o schimbare de rută HTTP: testele backend nu ating rutele, deci verifică și pe o
instanță pornită, autentificat. Raportează exact ce a trecut și ce nu.
