# Harta modulelor

Generat de `scripts/module_index.py` din docstring-urile și antetele modulelor; nu edita de mână.
Un modul nou primește un domeniu în `DOMENII` din script (CI pică altfel), apoi
`python3 scripts/module_index.py --scrie`.

## Unde e…

| Caut | E în |
|---|---|
| autentificare, sesiuni, 2FA | `app/users.py` (conturi, roluri, revocarea sesiunilor), `app/totp.py`, `app/routes_auth.py`; nu există `app/auth.py` |
| cine are voie la ce (viewer / operator, CSRF) | garda `guard()` din `app/server.py` (`VIEWER_GET`, `OPERATOR_ACTIONS`); restricția pe camere în `app/scoping.py` |
| versiunea | `config.py` din rădăcină; nu există `app/version.py` și nici `pyproject.toml` |
| configurarea go2rtc | `gen_go2rtc.py` din rădăcină (nu din `app/`), rulat la pornirea serviciului go2rtc |
| dosare de caz | `app/cases.py` |
| segmente, lanțul de custodie | `app/lant.py` |
| arhivă, retenție | `app/record.py` |
| jurnalul de evenimente | `app/jurnal.py`, în PostgreSQL (tabelul `events`); fișierele `events.*` din `data/` sunt rămășițe |
| migrări de schemă | dicționarul `MIGRARI` din modulul respectiv, aplicat de `aplica()` din `app/db.py` |
| testele de securitate | `tests/test_authz.py`, `tests/test_auth_2fa.py`, `tests/test_stream_proxy.py`, `tests/test_audit.py`; nu există `tests/test_security.py` |
| HTML și CSS | o singură pagină, `app/static/index.html`, și un singur `app/static/style.css`; nu există `templates/` sau `css/` |
| pachetul Python | `app/` nu e pachet: fără `__init__.py`, importurile sunt plate (`import jobs`) |

## Pornire, configurare, infrastructură

| Fișier | Rol |
|---|---|
| `config.py` | Configurarea OpenVigil, citita din variabile de mediu. |
| `app/server_main.py` | Punct de pornire pentru nvr-web. |
| `app/server.py` | Aplicatia Flask nvr-web: garda de acces, registrul de camere, fluxurile go2rtc, clipurile. |
| `app/db.py` | Stratul PostgreSQL (2.0.0) — un pool de conexiuni per proces, context-manageri `conn()` / `tx()`, si `aplica(...)`, oglinda PG a lui app/sc… |
| `app/store.py` | Scriere JSON ATOMICA — o singura implementare pentru tot ce e configurare/stare. |
| `app/settings.py` | Setari editabile din UI, persistate in data/settings.json (chmod 600 — contine parole). Fallback la valorile din config.py pentru instalari… |
| `app/logare.py` | Jurnalul proceselor (nvr-web, nvr-ai, nvr-record): `logging` cu niveluri in loc de print(). |
| `app/sdnotify.py` | sd_notify fara biblioteci: READY=1 la pornire si WATCHDOG=1 periodic, catre systemd. |
| `app/i18n.py` | Interfata multilingva (i18n): romana e limba sursa, restul sunt dictionare JSON. |
| `app/health.py` | Verificari de sanatate: fluxul video al unei camere si starea serverului. |
| `app/metrici.py` | Metrici per model (faza 6): cat de des si cat de repede lucreaza fiecare. |
| `app/jobs.py` | Coada de joburi: lucrul lung (export MP4, fisa probatorie, clip, chenare, dosar ZIP, adaugarea unei camere) ruleaza pe fire de lucru propri… |
| `app/routes_jobs.py` | Blueprint: coada de joburi — stare, lista, descarcarea rezultatului, anulare. |
| `app/routes_system.py` | Blueprint: stare & configurare sistem — monitorizare, spatiu, setari, unelte. |
| `app/static/js/00-core.js` | Nucleu: importuri, html``, toast, sesiunea (ME), coada de joburi |
| `app/static/js/05-navigare.js` | Navigare: taburi, rutare pe hash, taste globale, modul acasa/plecat/noapte, pulsul si legaturile din antet |
| `app/static/js/state.js` | Starea partajata intre ecrane: variabilele scrise din mai multe module (ES nu lasa un modul |

## Conturi, roluri și acces

| Fișier | Rol |
|---|---|
| `app/users.py` | Utilizatori si roluri pentru interfata web. |
| `app/totp.py` | TOTP (RFC 6238) pentru autentificarea in doi pasi — doar biblioteca standard. |
| `app/routes_auth.py` | Blueprint: autentificare — login/setup/logout + conturi (/api/me, /api/users). |
| `app/scoping.py` | Restrictia pe camere a unui cont: care camera cere o cerere HTTP? |
| `app/audit.py` | Jurnal de modificari: cine, cand, ce a schimbat in configuratie. |

## Camere și flux video

| Fișier | Rol |
|---|---|
| `gen_go2rtc.py` | Genereaza configurarea go2rtc (data/go2rtc.yaml) din camere; ruleaza la pornirea serviciului go2rtc. |
| `app/stream_proxy.py` | Releu TCP autentificat intre browser si go2rtc. |
| `app/camctl.py` | Control camere Hikvision standalone: capabilitati (microfon/difuzor/sirena/stroboscop/releu), actiuni manuale si evenimente de alarma in ti… |
| `app/drivers.py` | Stratul de drivere: ce producator vorbeste camera si cum (Etapa 7, multi-vendor). |
| `app/onvif.py` | Client ONVIF minimal (SOAP peste HTTP cu WS-Security UsernameToken), fara biblioteci noi. |
| `app/discover.py` | Descoperire camere in retea (scanner). |
| `app/masca.py` | Masca de confidentialitate: dreptunghiuri (normalizate 0..1) innegrite pe o camera. |
| `app/routes_cameras.py` | Blueprint: camere — CRUD, actiuni (sirena/strobo/say/volum), PTZ, stream, config. |
| `app/audio_level.py` | Masurarea continua a zgomotului pe camerele cu microfon, cu reguli de alarma. |
| `app/noise_stats.py` | Statistici orare de zgomot, pastrate pe disc, ca sa avem cu ce calibra pragurile. |
| `app/static/js/10-live.js` | Live: grila, vizualizari, filtre, controalele camerei, badge-uri, pop-up la critic, panoul I/O, densitate/mod perete |
| `app/static/js/15-flux.js` | Fluxul de evenimente de langa Live: ultimele cadre salvate azi (detectii, fete, numere, |
| `app/static/js/20-hd.js` | HD: overlay-ul pe o camera (live, time-shift, PTZ, zoom digital, chenare) |
| `app/static/js/50-camere.js` | Camere: adaugare, editoare (zona, linii), operatii in masa, tabel, panoul de detaliu |

## Înregistrare, redare, probe, date

| Fișier | Rol |
|---|---|
| `app/record_main.py` | Serviciul de inregistrare (nvr-record), separat de interfata web: restarturile nvr-web (setari, modele noi) nu mai intrerup inregistrarea c… |
| `app/record.py` | Inregistrare continua pe disc pentru camerele standalone (n-au SD/HDD propriu). |
| `app/continuity.py` | Garantia ca inregistram — si ca ce am inregistrat chiar sta pe disc cat spune retentia. |
| `app/lant.py` | Lantul de custodie al inregistrarilor: SHA-256 la inchiderea fiecarui segment. |
| `app/reencode.py` | Re-encodare inregistrari vechi pentru a castiga spatiu (optional, din Setari). |
| `app/probe.py` | Probe: intervale de inregistrare marcate „pastreaza ca proba", scutite de retentie. |
| `app/routes_probe.py` | Blueprint: probe („pastreaza ca proba") pe inregistrari. |
| `app/marcaje.py` | Marcaje (bookmarks) pe inregistrari: un moment sau un interval pe o camera, cu eticheta. |
| `app/routes_marcaje.py` | Blueprint: marcaje (bookmarks) pe inregistrari. |
| `app/verificare.py` | Verificarea publica a probelor: un fisier exportat (sau un hash) -> „e al nostru si neatins?”. |
| `app/routes_media.py` | Blueprint: media — clip/boxes, thumb, snapshots, recordings, play, export. |
| `app/routes_date.py` | Blueprint: date care supravietuiesc — lantul de custodie al segmentelor si copia nocturna criptata. |
| `app/backup.py` | Salvarea si refacerea configuratiei. |
| `app/backup_nocturn.py` | Copia nocturna CRIPTATA a starii: tot ce nu se poate reface (configuratie, conturi, reguli, jurnalul de evenimente, marcaje, dosare, biblio… |
| `app/static/js/30-redare.js` | Inregistrari: redare, timeline cu zoom, marcaje, export, analitica pe cadru (regiune, heatmap, time-lapse, masca) |
| `app/static/js/45-fisa.js` | Fisa evenimentului: un singur loc pentru orice eveniment (din flux, jurnal, incidente): |

## Evenimente, reguli, notificări

| Fișier | Rol |
|---|---|
| `app/hooks.py` | Iesirea catre automatizari: fiecare eveniment pleaca ca POST JSON catre Node-RED. |
| `app/rules.py` | Motorul de reguli + jurnalul permanent de evenimente. |
| `app/reguli.py` | Reguli de analiză: stratul subțire „ce înseamnă și pe cine anunț" peste percepție (YOLO / track / scenă / VLM). Vezi docs/reguli-analiza.md. |
| `app/jurnal.py` | Jurnalul permanent de evenimente — PostgreSQL (tabelul `events`, vezi app/db.py). |
| `app/snaps.py` | Snapshot-uri la detectii YOLO si alarme de camera + listare pentru tabul "Alarme". |
| `app/routes_automation.py` | Blueprint: jurnal de evenimente (+CSV), webhooks, MQTT. |
| `app/mqtt.py` | Puntea MQTT — evenimentele sistemului, publicate pentru Home Assistant / Node-RED / orice altceva vorbeste MQTT, plus actiunea "mqtt" din m… |
| `app/notify.py` | Notificari Telegram + email (text + poza) cu throttle, si watchdog pentru camerele standalone. Configurabile din UI (tabul Setari -> data/s… |
| `app/push.py` | Web Push catre PWA: incidentele ajung pe telefon ca notificare nativa. |
| `app/routes_push.py` | Blueprint: notificari push (Web Push) — abonarea dispozitivului curent si proba. |
| `app/telegram_bot.py` | Comenzi prin Telegram: /acasa, /plecat, /noapte, /mod, /stare — de pe telefon, fara interfata. Notificarile pleaca oricum prin acelasi bot… |
| `app/digest.py` | Rezumatul zilnic — ce s-a intamplat azi, dintr-o privire. |
| `app/escaladare.py` | Escaladarea alertelor critice către un serviciu extern de notificări, printr-un `POST /v1/fire` (contractul: `{"type": ..., "context": {...… |
| `app/mod.py` | Modul sistemului: acasa / plecat / noapte — comutatorul de „armare" (Home Mode). |
| `app/detectie_feedback.py` | Butoanele ✓ ✕ ∅ sub notificarile de detectie YOLO de pe Telegram (🚨 om / caine / pisica / urs, 👤 fata, 🚗 numar primesc doar textul; aici e… |
| `app/fata_feedback.py` | Butoanele ✓ ✕ sub notificarile de FATA recunoscuta (👤 <nume>) de pe Telegram. |
| `app/numar_feedback.py` | Butoanele ✓ ✕ sub notificarile de NUMAR de inmatriculare (🚗 <placa>) de pe Telegram. |
| `app/static/js/40-evenimente.js` | Evenimente: incidente, dosare, rezumat, poze, cautare semantica, lightbox, clip, jurnal |
| `app/static/js/65-program.js` | Program & automatizare: orarul saptamanal de moduri (timeline) + starea regulilor. |

## Incidente și triaj

| Fișier | Rol |
|---|---|
| `app/incidents.py` | Panoul de incidente — triajul in timp real pentru un operator. |
| `app/routes_incidents.py` | Blueprint: incidente + dosare de caz + rezumat zilnic. |
| `app/cases.py` | Pachetul de caz — un dosar de probe pentru un incident, gata de predat. |
| `app/absenta.py` | „Cât ai lipsit” — ce s-a întâmplat de la ultima privire, în câteva rânduri. |
| `app/episoade.py` | Episoade de persoana (E2): detectiile-cadre devin povestea unei prezente. |
| `app/stationar.py` | Registrul obiectelor stationare (episoade E1): parcarea e o STARE, nu un eveniment. |

## AI: serviciul nvr-ai și percepția

| Fișier | Rol |
|---|---|
| `app/ai_main.py` | Serviciul de inferenta nvr-ai: modelele AI intr-un singur proces. |
| `app/ai_client.py` | Client subtire pe care nvr-web il foloseste sa vorbeasca cu serviciul nvr-ai. |
| `app/ai_track.py` | Urmarire cu model PARTAJAT + inferenta in LOT — nucleul nvr-ai. |
| `app/ai_analytics.py` | Analitica peste urmarire (nvr-ai, Faza 2) — consuma track-uri, scoate intelesuri. |
| `app/ai_scene.py` | Detector de obiect NOU aparut / DISPARUT pe camere statice. |
| `app/detect.py` | Detectie animale/persoane pe camerele selectate (YOLO pe GPU, cu fallback CPU). |
| `app/verify.py` | A doua opinie cu YOLO stock (yolo11m, 80 clase COCO) pentru detectiile 'person' incerte ale modelului fine-tuned curte-v1. |
| `app/track.py` | Urmarirea obiectelor (ByteTrack) — numarare exacta, bucata cu bucata. |
| `app/faces.py` | Recunoastere persoane (Face ID) — cine e omul din cadru, nu doar ca e un om. |
| `app/plates.py` | Citire numere de inmatriculare (LPR) — ce masina a trecut prin fata camerei. |
| `app/reid.py` | Re-ID pe corp (OSNet): "cine e omul asta?" fara sa-i vedem fata. |
| `app/reid_arhiva.py` | Arhiva de corpuri (re-ID) pe zile: „gaseste-l pe toate camerele”. |
| `app/describe.py` | Descrierea persoanelor detectate — "barbat ~40 de ani, geaca neagra, rucsac". |
| `app/cadere.py` | Regula de cădere — orchestrarea (partea nvr-ai). Leagă percepția (pose) de logica pură (reguli.scor_cadere + dwell), confirmă cu VLM și, la… |
| `app/cadere_pose.py` | Pose (keypoints) pentru regula de cădere — rulează în nvr-ai (are GPU + modelul). Model mic (yolo11n-pose), încărcat lenef, pe decupajul pe… |
| `app/scorer_fp.py` | Scorerul de falsuri (fals-pozitive) — memoria spatiala + semantica a verdictelor. |
| `app/recomandari.py` | Recomandari per camera („Invata scena”, faza B): ce ar imbunatati detectia pe camera asta, deduse din ce a vazut sistemul (jurnal, candidat… |
| `app/vreme.py` | Vremea, din doua surse, ca detectia sa stie in ce conditii vede: |
| `app/llm.py` | Un singur loc pentru „cere un text scurt unui model de limbaj”, cu doua spate: |
| `app/routes_analytics.py` | Blueprint: analiza AI — alarme/recatalogare, reguli, cautare semantica, urmarire, LPR. |
| `app/mcp_server.py` | Server MCP (Model Context Protocol) peste sistem, pentru un asistent (Claude, un agent Node-RED, orice client MCP): intreaba ce s-a intampl… |
| `app/static/js/46-reid.js` | „Gaseste-l pe toate camerele”: omul dintr-o poza (sau dintr-o aparitie gasita) e cautat |
| `app/static/js/70-ai.js` | AI: Face ID, LPR, numarare, navigare, motorul de reguli |

## „Învață scena”

| Fișier | Rol |
|---|---|
| `app/scena.py` | Detectie pe schimbare: fundalul unei camere fixe si regiunile care difera de el. |
| `app/scena_web.py` | Partea de web a detectiei pe schimbare („Invata scena”, motorul e scena.py in nvr-ai): |
| `app/scena_clasa.py` | Clasificarea unui decupaj „ceva nou” (din scena.py): ce e obiectul din regiunea schimbata. |
| `app/scena_fundal.py` | Exemplele invatate din verdictele omului, per camera („Invata scena”, nvr-ai). |
| `app/scena_lume.py` | YOLO-World pentru vocabularul deschis al „Invata scena” (in nvr-ai). |

## Antrenare și dataset

| Fișier | Rol |
|---|---|
| `app/antrenare.py` | Joburile de antrenament (ruleaza in nvr-ai): construirea datasetului etichetat si antrenarea YOLO, ca subprocese in venv-ul proiectului, cu… |
| `app/adnotare.py` | Editorul de adnotare: citirea si scrierea etichetelor YOLO din data/finetune. |
| `app/nesigure.py` | Coada cadrelor „nesigure”: cadrele din data/dataset pe care modelul ACTIV le vede cu incredere intre LO si HI (nici sigur, nici nimic). Ast… |
| `app/routes_ai.py` | Blueprint: antrenare model (fine-tune local, data/finetune) + Face ID. |
| `app/static/js/75-adnotare.js` | Adnotare: etichetele datasetului de antrenament (data/finetune), cadru cu cadru |

## Căutare semantică și întrebări

| Fișier | Rol |
|---|---|
| `app/semantic.py` | Cautare semantica in alarme — "masina alba la poarta" gaseste poza, fara etichete. |
| `app/semantic_store.py` | Indexul semantic persistent si coada de indexare, PostgreSQL + pgvector. |
| `app/semantic_feedback.py` | Exemple umane pentru un concept: memorie explicita, imagini pastrate si export PG. |
| `app/semantic_pg.py` | Prototip pgvector izolat: import fara GPU si cautare exacta, in baza de TEST. |
| `app/ask.py` | Intrebari in limbaj natural peste indexul semantic. |
| `app/search_journal.py` | Observatii din jurnal pentru intrebari: filtre in SQL, fara LIMIT global inaintea filtrarii. |
| `app/search_time.py` | Intervale calendaristice locale pentru cautare; UTC epoch la granita cu stocarea. |
| `app/static/js/48-cauta.js` | O singura cautare, din antet (campul sau tasta /): textul merge la cautarea semantica |
| `app/static/js/49-semantic-review.js` | Exemple umane pentru cautarea vizuala: candidati, arhiva si exemple pastrate. |

## Harta proprietății și analitică

| Fișier | Rol |
|---|---|
| `app/harta.py` | Harta proprietatii: un plan (imagine) cu camerele asezate si conurile lor de vedere. |
| `app/plan_metrica.py` | Masura planului hartii: cum devin pixelii imaginii metri pe teren. |
| `app/omografie.py` | Omografia camera -> plan: din perechi de puncte (imaginea camerei <-> planul hartii) o matrice 3x3 care duce orice punct DE PE SOL din cadr… |
| `app/routes_operare.py` | Blueprint: operare ca Synology — modul acasa/plecat/noapte si harta proprietatii. |
| `app/analiza.py` | Analitica retroactiva din ce avem deja in jurnal si pe disc (Etapa 5). |
| `app/routes_analiza.py` | Blueprint: analitica retroactiva — cautare pe regiune, harta de caldura, time-lapse. |
| `app/static/js/24-harta-metrica.js` | Harta: masura planului (pixeli <-> metri), pentru interfata |
| `app/static/js/25-harta.js` | Harta proprietatii |
| `app/static/js/26-harta-calibrare.js` | Harta: calibrarea unei camere pe plan |
| `app/static/js/27-harta-limita.js` | Harta: limita proprietatii (scara din laturile cunoscute, unghiuri, suprafata) |

## Setări (interfață)

| Fișier | Rol |
|---|---|
| `app/static/js/60-setari.js` | Setari: incarcare/salvare, semantic, card-tabs, MQTT, utilizatori, Contul meu, automatizari, continuitate, backup |
