# nvr-ai — serviciul de inferență

> Stare: **MIGRAREA #9 COMPLETĂ (2026-09-02)** — contract (`docs/ai-contract.md`),
> Face ID/LPR/VLM (pas 1), bucla de detecție (pas 2) și SigLIP/semantic (pas 3)
> rulează toate în nvr-ai; nvr-web nu mai ține niciun model pe GPU. Rămân doar
> features (faza 6: marcare/overlay, metrici, hot-swap formal). Document viu.

## De ce

Azi toată inferența (YOLO, Face ID, LPR, SigLIP, Qwen VLM, tracking) trăiește în
procesul `nvr-web`, pe un singur GPU de 16 GB. Două probleme:

1. **VRAM.** Fiecare lucrător ByteTrack ține o **instanță proprie** de model YOLO;
   5 camere de stradă = 5 modele → GPU la 97%, erori cuDNN. De aceea tracking-ul a
   fugit pe CPU. Un serviciu cu **un model partajat + stare per cameră** rezolvă asta.
2. **Cuplaj.** Restart-ul web-ului (setări, model nou) reîncarcă toate modelele. Un
   serviciu separat (ca `nvr-record`, `nvr-go2rtc`) decuplează inferența de UI.

## Principii

- **Un proces deține modelele o singură dată.** `nvr-ai` e frate cu `nvr-record`:
  autonom, pornit de systemd, își trage singur cadrele.
- **Un singur proprietar al output-plane-ului.** Motorul de reguli + jurnalul +
  MQTT + webhook au stare in-memory (cooldown-uri, `_last_fire`, client MQTT) care
  **nu poate exista în două procese** fără să dubleze sirene/notificări. Rămâne în
  `nvr-web`. `nvr-ai` **produce** evenimente și le **trimite** la web; web le trece
  prin `hooks.emit` ca azi.
- **Fără IPC de cadre.** `nvr-ai` își trage singur RTSP/HD; peste proces circulă doar
  evenimente mici (JSON) și stare (contoare) — nu imagini de 900 KB.
- **Extensibil.** Un strat de analitică (zone, linii, viteză…) stă peste tracking și
  se adaugă fără să atingă nucleul.

## Arhitectura

```
  camere (RTSP/HD)
        │  (nvr-ai își trage singur cadrele)
        ▼
┌─────────────────────────  nvr-ai  ─────────────────────────┐
│  Registru de modele (o instanță fiecare, ordine bf16):     │
│    YOLO · InsightFace · anti-spoof · fast-alpr · SigLIP ·  │
│    Qwen-VL                                                  │
│  Conductă:  motion-gate → detect (batch) → tracking        │
│    (1 model + N stări ByteTrack) → analitică → sub-taskuri  │
│    (face/LPR/VLM pe crop-uri) → evenimente                 │
│  API local :8078  (control · stare · on-demand)            │
└───────────────┬─────────────────────────┬──────────────────┘
                │ evenimente (POST)        │ stare (GET), on-demand
                ▼                          ▲
┌─────────────────────────  nvr-web  ────────────────────────┐
│  Output-plane: hooks.emit → jurnal + reguli + MQTT + webhook│
│  UI, config pe disc (rules/settings/cameras.json), snapshot│
│  /api/internal/emit  ← evenimente de la nvr-ai             │
│  /api/track, /api/system  → proxy la nvr-ai (stare)        │
└────────────────────────────────────────────────────────────┘
```

**Transport intern:** HTTP pe `127.0.0.1` cu un token partajat (`data/ai_token`).
Rata de evenimente e mică (zeci/min) → HTTP e suficient; cadrele nu trec niciodată.

**Config partajat pe disc:** `data/settings.json`, `data/cameras.json`,
`data/detect.json`, `data/rules.json` — scrise de web (UI), citite de nvr-ai (urmărește
mtime, ca `nvr-record`). `data/events.db` (jurnalul SQLite) are **un singur writer** (web).

## Fluxul de evenimente (decizia critică)

`nvr-ai` **nu** cheamă `rules`/`mqtt` local. Produce un eveniment și-l trimite:

```
nvr-ai:  POST 127.0.0.1:8079/api/internal/emit  {tip, cam, ...date, token}
nvr-web: hooks.emit(tip, cam, **date)  →  jurnal + reguli + MQTT + webhook
```

Așa: un singur jurnal, un singur motor de reguli, o singură sirenă. Evenimentele de
alarmă cameră (camctl), zgomot, online/offline rămân emise direct în web — nedublate.

## Features viitoare (proiectăm loc de-acum)

**Analitică din tracking** (strat separat, consumă track-uri):
- Linii de numărare / tripwire direcțional (in/out) definite în UI
- Zone/poligoane („intrare în curte") definite în UI
- Zăbovire/loitering (obiect > N secunde), viteză vehicule, traiectorie
- Re-ID între camere (greu; lăsăm interfața pregătită)

**Marcare/adnotare:** overlay cutii+etichete+track pe live și pe clipuri; atribute
(culoare/tip — există), marcă vehicul (model specializat).

**Operare/scalare:** metrici per model (fps, latență, adâncime coadă), health;
hot-swap de model; al doilea GPU / nod remote apelat prin același API; degradare
grațioasă (GPU ocupat → scade fps, nu crapă).

## Detector de obiect nou/dispărut (`ai_scene.py`) — implementat

Pe o cameră **statică**, un obiect lăsat sau luat schimbă o regiune mică și o ține
schimbată mult timp — spre deosebire de o mașină/om care trec. `SceneWatcher`:

- ține o **mediană lentă** a scenei (mișcarea dispare din ea), o compară cu o referință;
- raportează **regiunile mici care persistă** peste un prag, **ignorând** ce vede
  tracker-ul ca obiect în mișcare (masca de boxuri) și schimbările **globale** de lumină
  (nori, zi↔noapte); driftul lent de lumină e urmărit cu EMA doar în zonele stabile;
- distinge **nou** vs **dispărut** după densitatea de muchii din regiune;
- emite `obiect_nou` / `obiect_disparut` → feed de incidente (oră + clip), severitate
  „atenție".

Rulează în bucla batch nvr-ai (are deja cadrele + detecțiile), **opt-in per cameră** și
doar pentru camere care sunt și urmărite (de acolo ia cadrele).

**Config (runtime, în `data/settings.json` — gitignored, specific instalării):**
- `scene_cams`: lista de camere vegheate. Se setează din UI: Live → editorul de
  linii/zone al camerei → checkbox **„obiect nou/lipsă"**.
- `scene_persist_min`: minute de persistență până la raportare (implicit 30).
- `scene_roi_top`: fracția de sus a cadrului ignorată (implicit 0.35). Pe camerele
  de curte înclinate în jos, top-ul e cer/copaci/acoperișuri — sursa principală de
  fals-pozitive (nori, soare, vânt). Zona utilă (solul) e jos.

**Severitate `info`** (nu `atentie`): la revizia din 2026-08-31, detectorul producea
~330 evenimente/1.5 zile, majoritatea fals-pozitive de lumină la marginea de sus a
cadrului, inundând panoul de incidente (ca vechiul VMD). Fix: ROI top + persistență
30 min + severitate `info` → apar în jurnal + căutare, NU în triajul de incidente.

**Activ acum:** camerele **7, 8, 9, 14** (curte + stradă). E setare de rulare, deci nu
apare în git; supraviețuiește restartului (nvr-ai recitește `settings.json`).

**Verificare:** `tests/test_scene.py` — apariție, dispariție, obiect în mișcare ignorat,
lumină globală ignorată, prag, throttle (cadre sintetice + timp simulat).

## A doua opinie pe 'person' (`verify.py`) — implementat

Modelul fine-tuned curte-v1 e bun ziua, dar la lumină slabă/amurg biasează spre
`person` pe forme ambigue (câini, vehicule la distanță). `verify.py` cere o a doua
părere de la **yolo11m stock** (80 clase COCO, robust, `data/yolo11m_stock.pt`):
pentru fiecare cutie `person` sub 0.85 încredere, decupează regiunea și întreabă
stock; dacă stock vede clar un vehicul/animal (≥0.45), corectează eticheta.

- Model stock **lazy** (GPU, ~516MB) — se încarcă doar când chiar apare un `person`
  incert, deci fără cost de VRAM cât timp nu e nevoie.
- Integrat în `clip_detect` (chenarele de pe clipuri) — rulează doar pe cadrele cu
  `person`, on-demand. Nu atinge `person` foarte sigur și nu e pe bucla live de
  tracking (hot path). Extensibil la confirmarea de intrus (`confirm_alarm`).
- Chenarele de pe clipuri folosesc `detect.CONF` (0.50), la fel ca detecția de
  incidente — nu 0.35 (care lăsa ghiceli slabe să apară).

## Rollout pe faze (fiecare testabilă, nu strică precedenta)

1. ✅ **Schelet nvr-ai + tracking cu model partajat.** `ai_main.py` (serviciu) +
   `ai_track.py` (1 model YOLO + N stări ByteTrack). Trage RTSP, numără, trimite
   evenimente „obiect" la web. Web oprește lucrătorii track locali; `/api/track`
   devine proxy. **Câștig VRAM imediat.**
2. ✅ **Strat de analitică** (zone, linii, zăbovire, viteză) peste tracking — aditiv.
3. ✅ *(#9 pas 0+1, 2026-09-02 — ordinea 3↔4 inversată)* **Contract + Face ID / LPR /
   VLM în nvr-ai, on-demand.** Motivul inversării: sub-taskurile consumă EXACT
   cadrul detecției; dacă bucla se muta prima, cadrele ar fi traversat procesele.
   Contract: `docs/ai-contract.md`; `/capabilities` cu SHA-256 pe modele; în nvr-ai
   `hooks.emit`/`semantic.enqueue` sunt redirectate către web (shim). Fallback:
   orice eșec de transport → implementarea locală (lazy) din web. VRAM web:
   11.9 GB → 3.4 GB.
4. ✅ *(#9 pas 2, 2026-09-02)* **Detecția de bază + tiling în nvr-ai** (BatchLoop,
   motion-gate, CamState, confirmarea de intrus). `detect.IS_HOST` decide gazda —
   același modul, web-ul ține bucla oprită cât `ai_service` e pornit (fallback dacă
   e oprit). Sub-taskurile (face/LPR/VLM) sunt apeluri LOCALE în nvr-ai
   (`ai_client.remote` shimuit la False). Starea (`/detect/state`) și confirmarea
   (`/detect/confirm`, cu gating Face ID inclus) se proxează; hot-reload de model
   prin mtime pe `MODEL_PATH` (detect + modelul partajat de tracking). Ordinea bf16
   e păstrată și aici: Qwen se încarcă ÎNAINTE să pornească buclele.
5. ✅ *(#9 pas 3, 2026-09-02)* **SigLIP + indexul semantic în nvr-ai.**
   `semantic.IS_HOST` (ca la detect); în web, `enqueue`/`prune` se TRIMIT la nvr-ai
   (`/semantic/index`, `/semantic/prune` — fișierele pe FS comun, circulă doar
   zi+nume); `/api/search`, `/api/ask`, `/api/semantic/*` → proxy cu fallback local.
   Ordinea bf16 completă în nvr-ai: SigLIP → Qwen → buclele. **Web fără GPU** —
   toate modelele într-un singur proces, migrarea #9 e completă.
6. **Marcare/overlay + features avansate** — ÎN CURS:
   - ✅ *(2026-09-03)* **Chenare pe HD live** (butonul „Chenare"): verde = raportat,
     gri punctat = văzut dar filtrat, cu motivul (mărime/margine/zonă/neconfirmat)
     și încrederea. `CamState.last_boxes` → `snapshot()` → `/detect/state` →
     `/api/events?full=1`. Estompare când motion-gate ține camera pe loc; live-only.
     Unealta de diagnoză pentru „de ce n-a raportat / de ce a greșit".
   - Rămân: overlay pe clipuri e deja în playerul de clip (`/api/clip/boxes`);
     metrici per model, hot-swap formal.

## Episoade de comportament (cerința 2026-09-09; E1 implementat)

Detecțiile-cadre → **episoade** (viața unui obiect): cine, cât a stat, pe unde,
încotro, ce a făcut. Trei faze:

- **E1 ✅ (2026-09-09) — Staționare** (`app/stationar.py` + ancora din `track.py`):
  vehiculul pe loc >2 min devine PARCARE — un eveniment `parcare_inceput` (sev
  atenție), suprimare totală cât stă (detect + confirmarea de intrus, vizibil în
  overlay ca „parcat (suprimat)", box albastru cu durata), `parcare_sfarsit` cu
  `durata_min` la plecare. Registru persistat (`data/stationar.json`) — restartul
  nu re-alertează; track-ID-uri noi se ADOPTĂ prin suprapunere (IoU≥0.3), fără
  duplicate. Pierdută din vedere >10 min → închidere cu notă. Durata se măsoară
  de la OPRIRE (ancoră), nu de la confirmare. Doar camerele cu tracking (7/8/9/13/14).
- **E2 ✅ (2026-09-09) — Episoade de persoană** (`app/episoade.py`): prezența
  (≥1 om în cadru) devine UN eveniment `episod` la închidere (GAP 45s): câți
  simultan, aparițiile, zonele UI traversate, direcțiile, durata + numele
  (jurnal Face ID) și semnalmentele VLM (jurnal evenimente), cu `text` gata
  compus (cardul de incident îl afișează direct). Severitate: cunoscut=info,
  necunoscut=atenție/critic noaptea. Hrănit din bucla de urmărire (fir unic).
- **E3 ✅ (2026-09-09) — „Ce a făcut"**: traiectoria omului se eșantionează în
  tracker (~2s/punct); `episoade.comportament()` deduce fraza („a stat 75s la
  poarta" din dwell-ul pe zonele UI / „s-a învârtit" — drum ≥0.5 cu net mic /
  „a zăbovit" / „a trecut"), care intră în text + câmpul `comportament`; cardul
  de incident are mini-harta traseului (SVG, verde=intrare, roșu=ieșire),
  traseul (≤40 puncte) călătorește în eveniment. Semnalmentele VLM erau deja
  legate din E2 (jurnal); VLM suplimentar pe cadre-cheie rămâne idee de viitor
  dacă frazele euristice nu ajung.

## Faza 1 — ce construim acum

- `app/ai_track.py` — nucleu: un `YOLO` partajat, dict `{cam: BYTETracker}`, numărare
  distinctă + direcție per cameră (logica din `track.py`, dar cu model unic).
- `app/ai_main.py` — serviciul: citește `settings.track_cams`, pornește/oprește
  urmărirea per cameră (urmărește mtime settings), expune API-ul local.
- `app/ai_client.py` — helper subțire pe care web-ul îl folosește să întrebe starea.
- `nvr-web`: endpoint intern `/api/internal/emit`; `/api/track` → proxy la nvr-ai;
  lucrătorii `track.py` locali se opresc când `nvr-ai` e activ.
- `deploy/systemd/nvr-ai.service`.

**Verificare:** aceleași contoare ca tracking-ul local (aceeași acuratețe), VRAM cu
un singur model de tracking, evenimentele „obiect" ajung în jurnal prin web,
pytest + Playwright verzi.
