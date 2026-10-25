# Contractul web ↔ ai (v1)

> Ideea (împrumutată de la OpenNVR ai-adapter): nu mutăm doar cod — definim un
> contract stabil. **nvr-web = consumator** (UI, output-plane), **nvr-ai =
> furnizor de inferență** (modelele, o singură dată). Versiunea contractului
> apare în `/capabilities` → `contract`; crește DOAR la schimbări incompatibile.

## Transport & autentificare

- HTTP pe **127.0.0.1** (nvr-ai ascultă doar pe loopback, port **8077**).
- Jeton partajat pe disc: `data/ai_token` (0600, creat de web la pornire).
  - Cereri cu corp **JSON**: `{"token": ...}` în corp.
  - Cereri cu corp **binar (JPEG)**: `?token=` în query.
- Peste graniță circulă DOAR: evenimente mici (JSON), cadre de **fallback**
  (JPEG, cateva/min) și căi de fișiere pe FS-ul comun. Fluxul continuu de cadre
  NU trece niciodată prin HTTP — fiecare serviciu și-l trage singur
  (go2rtc / ISAPI picture).

## Proprietate (cine deține ce)

| Ce | Proprietar | De ce |
|----|-----------|-------|
| Reguli, jurnal (`events.db`, SQLite), MQTT, webhook | **nvr-web** | stare in-memory (cooldown-uri) care nu poate exista în două procese |
| Notificări Telegram pentru față/număr (`notify.face/plate`) | **nvr-ai** | singurul apelant după pas 1; cooldown-urile au un singur stăpân |
| Modelele: YOLO partajat, verify, InsightFace + anti-spoof, ALPR, Qwen VLM, SigLIP | **nvr-ai** | VRAM: o instanță de fiecare |
| Indexul semantic (`data/semantic/`) | **nvr-ai scrie** (worker + backfill + prune); web trimite doar (zi, nume) la `/semantic/index` | modelul e acolo (#9 pas 3) |
| Biblioteca de fețe (`data/faces/*`): embeddings, crop-uri, log | **nvr-ai scrie** (enroll/delete/proces); web doar **citește** (liste, poze, istoric) | scrierea cere modelul |
| `data/settings.json`, `cameras.json`, `rules.json` | **nvr-web scrie**, nvr-ai citește (mtime) | UI-ul e în web |
| Snapshot-uri (`data/snapshots/`) | ambele scriu fișiere JPG (FS comun); indexarea o face web | fișiere independente, fără conflict |

**În procesul nvr-ai**, `hooks.emit` și `semantic.enqueue` sunt REDIRECTATE
(`ai_main._shim_output_plane`) către web — modulele mutate nu știu în ce proces
rulează.

## nvr-ai → nvr-web

| Endpoint | Corp | Rol |
|----------|------|-----|
| `POST /api/internal/emit` | `{token, tip, cam, date}` | orice eveniment de inferență → `hooks.emit` (jurnal + reguli + MQTT + webhook) |

(`/api/internal/index` a existat doar cât indexul era în web — de la #9 pas 3
indexarea curge invers, web → nvr-ai `/semantic/index`.)

## nvr-web → nvr-ai

| Endpoint | Cerere | Răspuns | Note |
|----------|--------|---------|------|
| `GET /health` | – | `{ok, cams, urmarite, scene, bucla_track, bucla_detect, reid}` | fără jeton (doar loopback) |
| `GET /state` | – | starea urmăririi | proxy în `/api/track` |
| `GET /capabilities` | – | vezi mai jos | contractul viu |
| `POST /detect_clip` | JSON `{token, path, fps, imgsz}` | `{fps, frames}` | chenare pe clip; calea pe FS comun |
| `POST /face/process?cam=` | JPEG (fallback) | `{gasiti: [nume]}` | Face ID complet, cu efecte (crop, log, notify; evenimente prin web) |
| `POST /face/known` | JPEG | `{nume}` | gating intrus (camctl) |
| `POST /face/enroll?name=` | JPEG | rezultatul enroll | 400 + `{error}` la poză fără față |
| `POST /face/auto_enroll` | – | `{rezidenti}` | lent (rebuild) |
| `POST /face/delete?name=&photo=` | – | `{ok}` | poate reconstrui embedding (model) |
| `POST /face/rename` (JSON: vechi, nou) | – | `{ok, nume}` | fără model, dar biblioteca are un singur scriitor |
| `POST /lpr/process?cam=` | JPEG (fallback) | `{numere}` | LPR cu efecte |
| `POST /describe/person?cam=&boxes=` | JPEG | `{ok}` | fire-and-forget (VLM lent); rezultatul vine ca eveniment |
| `POST /describe/vehicle?cam=&boxes=&moment=` | JPEG | `{ok}` | idem |
| `POST /warmup` | JSON `{token, face?, lpr?, describe?, semantic?}` | `{ok}` | preîncarcă async |
| `POST /model/verifica` | JSON `{token, path}` | `{ok, clase, sha256}` sau 422 `{error}` | pre-flight pe un CANDIDAT (pe CPU): se încarcă? are person + un vehicul? inferează? |
| `POST /model/reincarca` | JSON `{token}` | `{ok, amprenta, clase}` | reîncărcare forțată + confirmare după swap |
| `POST /semantic/index` | JSON `{token, day, fn}` | `{ok}` | un snapshot nou (FS comun) intră în index |
| `POST /semantic/prune` | JSON `{token}` | `{ok}` | curăță shard-urile zilelor șterse de retenție |
| `POST /semantic/search` | JSON `{token, q, days, limit, visible}` | `{results}` | `visible` = camerele permise sesiunii, calculate de web |
| `POST /ask` | JSON `{token, q, visible, cam_names, cam}` | răspunsul `ask.answer` | citește și jurnalul de evenimente (FS, read-only) |
| `GET /semantic/status` | – | `{enabled, indexed, days, backfill}` | |
| `POST /semantic/backfill` | JSON `{token}` | `{ok, started, ...}` | |

## Degradare & fallback

- Comutatorul e `settings.ai_service` **și** health-ul viu (`ai_client.remote()`,
  cache 5s). Orice eșec de transport → `None` → apelantul cade pe implementarea
  **locală** (modelul se încarcă lazy în web doar atunci). nvr-ai căzut nu
  omoară nicio funcție, doar mută costul înapoi în web.
- Răspunsurile de eroare: `{error: ...}` + cod HTTP (400 cerere, 401 jeton,
  403 non-local, 404 inexistent, 500 inferență).

## /capabilities (fingerprinting)

```json
{"serviciu": "nvr-ai", "contract": 1, "pornit_de_s": 123,
 "permisiuni": ["cadre:go2rtc+isapi", "evenimente:web:/api/internal/emit", ...],
 "modele": {"yolo": {"activ": true, "incarcat": true, "cale": "...", "sha256": "..."},
            "face": {"activ": true, "incarcat": true, "antispoof": true, ...},
            "lpr": {...}, "describe": {...}, "verify": {...}}}
```

SHA-256 pe fișierele de model (cache pe mtime+mărime) = **amprenta modelului
care a produs o detecție**: probe mai solide + hot-swap verificabil (după un
fine-tune, hash-ul nou confirmă că serviciul chiar rulează modelul nou).

**Fingerprinting per-eveniment** (din 2026-09-10): evenimentele produse de
modelul YOLO — `detectie` (inclusiv sursa „regiune"), `intrus`, `obiect` —
poartă câmpul `model: "<fisier>@<sha12>"` (`detect.amprenta()`, cache pe
mtime). Hash-ul scurt se verifică contra celui complet din `/capabilities`;
după un fine-tune aplicat din UI, evenimentele arată vizibil amprenta nouă.

## Hot-swap formal (faza 6, 2026-09-10)

Schimbarea modelului YOLO (activate/revert din UI) e o **tranzacție verificată**:
1. **pre-flight** — candidatul se validează în nvr-ai (`/model/verifica`, pe CPU):
   se încarcă, acoperă clasele contractului (person + un vehicul), inferează;
2. **swap** pe disc (cu `.bak`);
3. **confirmare** — `/model/reincarca` forțează încărcarea în procesul care
   servește și întoarce amprenta nouă (aceeași care apare apoi pe evenimente);
4. **rollback automat** — dacă reîncărcarea pică, modelul vechi e repus și
   re-confirmat; UI-ul primește motivul.
Ori aterizează complet și verificat, ori nu se schimbă nimic. Cu nvr-ai oprit,
activate rămâne pe fallback-ul local (notat în răspuns).

## Reguli de evoluție

1. Câmpuri noi se ADAUGĂ, nu se redenumesc (ca la webhook-uri).
2. Endpoint nou = minor (contract neschimbat); schimbare de semnificație = `contract+1`
   și web-ul refuză nepotrivirea (de implementat când apare cazul).
3. Modulele partajate (faces/plates/describe) rămân importabile în AMBELE
   procese — fallback-ul local e parte din contract.
