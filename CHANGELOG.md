# Schimbări

Formatul urmează [Keep a Changelog](https://keepachangelog.com/) și
[Conventional Commits](https://www.conventionalcommits.org/).

## [Nepublicat]

## [2.0.5] — 2026-11-05

### Reparat
- **Scorerul de falsuri își urmărește feedback-ul** (B4/B5): după fiecare verdict, memoria
  (`vectori_fp`) se re-sincronizează din `feedback.jsonl` pentru acea cameră (în fundal); înainte
  nimic nu o actualiza automat. Dedup după `id` (ultimul verdict al unei detecții contează), ca
  verdictele repetate să nu umfle numărătoarea. Reîncărcarea e atomică (o tranzacție, rollback la
  eroare), nu ștergeri + inserări care puteau lăsa memoria goală.
- **Transportul de evenimente web↔AI e idempotent și corect datat** (Lot C): fiecare eveniment
  primește la sursă un id stabil (`_eid`), deci un retry din spool (sau un răspuns HTTP pierdut) nu
  mai duplică jurnalul și nu mai repetă acțiunile (sirenă/MQTT/webhook/push/Telegram). Ora originii
  (`_origine`) datează evenimentul la producere, nu la recepție — cele recuperate după o pană nu mai
  apar la ora revenirii, iar severitatea zi/noapte e corectă. Eroarea de persistență nu mai e
  înghițită tăcut.

### Documentație
- Sincronizare cu PostgreSQL: README, `docs/operare.md`, `docs/ai-contract.md`, `docs/nvr-ai.md` și
  manualele EN/RO nu mai descriu SQLite (`events.db`/`segmente.db`); `data/` are datadir-ul PG în
  `data/pg/`. `server_main.py`: fișierul e rulat de entrypoint-ul containerului (Docker Compose).

## [2.0.4] — 2026-11-05

### Reparat
- **Tiling-ul nu mai ocolește filtrele de eligibilitate** (scorer/detecție, Lot B): un singur gate
  comun (noapte/meteo/margine/**zonă**/**învățat**) pe box-uri normalizate în cadrul întreg, aplicat
  identic pe substream și pe calea de tiling (`sursa="regiune"`). Înainte, un fals suprimat pe
  substream — sau un obiect în afara zonei — tot producea eveniment + notificare prin tiling.
- **Un om real nou nu mai poate fi suprimat** într-o celulă IR învățată ca fals: suprimarea = candidat
  pozițional **și** fără veto semantic. Dovada că detecția e reală (seamănă cu un exemplar „ok", sau
  nu seamănă cu niciun fals învățat) **blochează** suprimarea (embedding SigLIP la live, în spatele
  flag-ului per-cameră `scorer_semantic`, implicit oprit). Invariantă: suprimarea nouă e un subset
  strict al celei vechi, deci nu se pot rata în plus detecții reale.
- **Scorerul nu mai întârzie detecția tuturor camerelor** când PostgreSQL e degradat: cache pe celule
  + circuit breaker + `statement_timeout` scurt; la eroare, fail-open (nu suprimă). Un „ok" proaspăt
  ridică imediat mutul (invalidarea celulei la scriere).

## [2.0.3] — 2026-11-05

### Reparat
- **Restore atomic** (`backup_nocturn`): refacerea validează toate CSV-urile întâi, apoi
  TRUNCATE + COPY într-o singură tranzacție — o cădere la mijloc sau un CSV invalid lasă baza
  neatinsă (rollback), nu pe jumătate restaurată.
- **Backupul eșuează explicit** la orice eroare PostgreSQL, în loc să rotească peste copiile bune
  o arhivă incompletă. Dump consistent dintr-un singur snapshot (`REPEATABLE READ READ ONLY`) și
  ordonat după cheia IDENTITY; manifestul notează rândurile per tabel (`pg_randuri`) și `complet`.
- **Lanțul de custodie rămâne valabil după re-encodare**: `lant` reține derivarea (coloane noi
  `sha256_orig` + `recodat`, migrarea 2), iar `reencode` actualizează lanțul *înainte* de a șterge
  originalul. La arhivare, registrul se scrie tot înainte de ștergerea sursei.
- **Un `users.json` corupt sau gol nu mai deschide `/setup`** (altfel oricine putea crea un admin
  fără autentificare): `setup_permis()` distinge „fișier absent” de „corupt” → blocaj 503, nu
  prima pornire.
- **Migrările de schemă nu se mai aplică concurent**: advisory lock PostgreSQL per modul, ca cele
  patru procese (go2rtc/web/record/ai) care pornesc deodată să nu ruleze aceeași migrare în paralel.

## [2.0.2] — 2026-11-04

### Reparat
- Testele rulate în imaginea Docker (pasul „Teste in imagine” din release, fără un server
  Postgres) se blocau ~30 s per test: `conftest` verifica PostgreSQL prin pool la fiecare test,
  iar `.connection()` aștepta timeout-ul pool-ului. Acum verificarea PG se face o singură dată
  pe sesiune (cache), iar `db.ping()` se conectează direct cu `connect_timeout` scurt (fail-fast);
  fără Postgres toată suita se sare în câteva secunde. Pool-ul primește și `connect_timeout=10`.

## [2.0.1] — 2026-11-04

### Reparat
- Imaginea Docker nu includea driverul PostgreSQL: `psycopg` / `psycopg-binary` / `psycopg-pool`
  și `pgvector` erau în `requirements.txt` și `requirements-ci.txt` (deci CI-ul trecea), dar
  lipseau din `requirements.lock`, din care se construiește imaginea (`--no-deps --require-hashes`).
  Adăugate cu hash-uri; fără ele, serviciile pe 2.0.0 nu porneau (`No module named 'psycopg_pool'`).

## [2.0.0] — 2026-11-04

### Adăugat
- Migrarea pe **PostgreSQL + pgvector** (2.0.0) — fundația (Faza 0): serviciul `db`
  (`pgvector/pgvector:pg16`, doar pe `127.0.0.1`, datadir sub `DATA_DIR`), modulul `app/db.py`
  (pool de conexiuni, `conn()`/`tx()`, `aplica()` în tiparul lui `schema.py`) și un Postgres în
  CI cu test de fum. Nu mută încă niciun tabel — plan complet în `docs/ro/postgres-migrare.md`.
- PostgreSQL, Faza 1: `push` (abonamente), `jobs` (coada) și `marcaje` (bookmarks) mutate din
  SQLite pe PostgreSQL prin `app/db.py` (nu mai împart baza `events.db` cu jurnalul). Script de
  migrare a datelor one-shot (`scripts/migrare_pg.py`, idempotent) și teste pe Postgres.
- PostgreSQL, Faza 2: **jurnalul de evenimente** (`app/jurnal.py`, tabelul `events`) mutat pe
  PostgreSQL. Contractul `query()`/`insert()` neschimbat pentru restul sistemului. Căutarea de
  text folosește acum `ILIKE` (în PostgreSQL `LIKE` e sensibil la litere mari), ordinea în
  aceeași secundă vine din coloana `seq` (IDENTITY) în locul `rowid`-ului, iar migrarea veche din
  `events.jsonl` a fost scoasă (datele istorice se mută cu `scripts/migrare_pg.py`, care acum
  include și `events`).
- PostgreSQL, Faza 3: **lanțul de custodie** (`app/lant.py`, tabelul `segmente`) mutat pe
  PostgreSQL — scris de nvr-record, citit de nvr-web, acum peste aceeași bază (fără WAL între
  procese). `scripts/migrare_pg.py` mută și `segmente` și **verifică** după migrare că fiecare
  amprentă SHA-256 a ajuns neschimbată (lanțul rămâne valabil); re-hash-ul fișierelor de pe disc
  rămâne disponibil prin `verifica()`/fișa probatorie.
- PostgreSQL, Faza 4a: **scorerul de falsuri** (`app/scorer_fp.py`) — memorie spațială + semantică
  a verdictelor, în `pgvector` (tabelul `vectori_fp`, SigLIP 768-dim). Scor cu doi termeni
  (rata de fals pe pătrățel+etichetă+regim + similaritate cosinus la exemplarele „greșit” vs „ok”),
  cu plasa de siguranță `ok==0`. Motorul + testele; cablarea în bucla de detecție și migrarea
  fețe/re-ID/scenă în pgvector urmează.
- PostgreSQL, Faza 4b: scorerul **cablat în bucla de detecție** (`detect.CamState._mut_invatat`,
  `filtru="invatat"` lângă filtrul de zonă) — opt-in per cameră (`scorer_fp` în config) și
  fail-safe (orice eroare → nu suprimă, alarma tot sună).
- PostgreSQL, Faza 5: **gata fără SQLite.** Zero `sqlite3` în codul aplicației — `rules` prinde
  erori generice de jurnal (nu `sqlite3.Error`), `backup_nocturn` scrie baza în arhivă ca
  `pg/<tabel>.csv` (dump logic prin `COPY`, fără `pg_dump`) și o reface cu `incarca_pg()`,
  `app/schema.py` (migratorul SQLite, nefolosit) a fost șters. Fețe/re-ID/scenă rămân pe fișiere
  (nu folosesc SQLite). Migrarea fețe/re-ID/scenă în `pgvector` (4c) rămâne opțională, pentru când
  apare nevoia de căutare vectorială la scară. Cutover-ul datelor: `scripts/migrare_pg.py`.

### Reparat
- Aplicarea unui model antrenat local din interfață (`/api/train/apply`) dădea 500: decoratorul
  de rută stătea pe funcția-ajutor `_swap_formal(sursa)`, nu pe handler. Mutat pe
  `api_train_apply`; revenirea (`/api/train/revert`) nu era afectată.

## [1.1.0] — 2026-11-02

**Highlights (English).** "Learn the scene" — per-camera change detection that learns each
camera's background and classifies only what is new, with an editable open vocabulary
(SigLIP + YOLO), on-demand deep analysis (YOLO-World + Qwen2.5-VL), and verdict buttons both
in the camera card and on Telegram — for scene candidates *and* for YOLO detections — that feed
the training set and build per-camera learned examples. Outdoor and camera-estimated **weather**
on every event, with a bad-weather detection threshold. **Per-camera recommendations** applied
with one click. A configurable **language model** (the local Qwen or an OpenAI-compatible
server) and an **MCP server** so an assistant can ask the system what happened. Recordings,
the archive tier and the nightly backups can each live on a separate disk. The Romanian
detail follows.

### Adăugat
- „Învață scena” (în probă, per cameră, din fișa camerei): camera își învață fundalul și
  clasifică doar ce e nou, pe decupaj din fluxul HD, cu vocabular deschis editabil din
  interfață (SigLIP) plus YOLO; candidații apar în fișă cu butoane ✓ / ✕ / ? / 🔍, iar
  verdictele se strâng în `data/scena/feedback.jsonl` pentru antrenamentul următor.
  Opțional, candidații pleacă pe Telegram ca poză cu butoane (✓ corect, ✕ ce e de fapt,
  🔍 reanalizează); 🔍 cere o analiză adâncă (YOLO la 640, tot vocabularul, modelul de limbaj
  Qwen2.5-VL) și răspunde în același chat. Evenimente noi: `scena_candidat`, `scena_analiza`.
  Vocabularul trece și prin YOLO-World (detector cu vocabular deschis, `yolov8m-worldv2`,
  descărcat la prima pornire în `data/modele/`): vede obiectul din decupaj, nu fundalul, și
  câștigă când YOLO-ul antrenat nu e sigur. Numele românești primesc forma engleză pentru
  CLIP din dicționarul interfeței, din „nume = english name” scris în vocabular sau printr-o
  traducere memorată; setarea `scena_lume` îl oprește. În analiza adâncă, eticheta modelului
  de limbaj e crezută doar dacă o confirmă YOLO-World, SigLIP sau YOLO-ul antrenat; altfel
  rămâne afișată ca neconfirmată și decide restul.
  Pe Telegram, „necunoscut” pleacă doar ziua și pe vreme bună (noaptea e granulație de
  infraroșu, pe ploaie sunt picături), iar noaptea pleacă doar etichetele date de un detector
  cu încredere de cel puțin 0,5; fiecare decizie de trimitere sau sărire e în jurnalul de
  sistem. Textul analizei adânci arată din vocabular doar numele care ies în față, nu
  trei nume oricum.
- Verdictele din „Învață scena” intră în datasetul de antrenament (Setări → Unelte → Model):
  decupajele confirmate devin exemple pozitive, cu chenarul obiectului pus de YOLO-World,
  iar „e doar ploaie / umbră / frunze” devin fundaluri fără obiecte; numele noi din vocabular
  cu destule exemple devin clase noi (`data/finetune/clase.json` ține id-urile stabile).
  Antrenamentul se poate porni pe yolo11m sau yolo11l și e evaluat automat pe clase, pe
  setul de validare fix (`scripts/evalueaza.py`, `runs/<nume>/evaluare.json`), iar
  rezultatele apar în cardul de antrenament.
- Recomandări per cameră, în fișa camerei, deduse din ce a văzut sistemul (jurnal, candidați,
  verdicte, harta de instabilitate) și aplicabile cu un clic: detecție pe regiuni HD când
  obiectele sunt mici, zonă de detecție sub banda instabilă (copaci, cer), prag de încredere
  mai mare noaptea când ai respins „om” de mai multe ori (reglaj nou, per cameră:
  `conf_noapte`), pornirea urmăririi sau a scenei, nume noi în vocabular. Aplicările se
  auditează. Recomandarea „zonă fierbinte” vede când candidații scenei se îngrămădesc
  într-un petic mic al cadrului (o tufă în vânt, o creangă în infraroșu, granulație pe
  gazon) și propune o zonă de detecție care îl lasă afară.
- Zona de detecție a camerei (desenată în fișa ei) e respectată și de „Învață scena”: ce e
  în afara ei nu devine candidat și nu consumă clasificări.
- Decupajele scenei intră în indexul căutării semantice, cu vectorul SigLIP deja calculat
  la compararea cu vocabularul (fără GPU în plus): „mașină albă” sau „om cu umbrelă” găsește
  și ce a văzut doar scena, nu și YOLO; în rezultate sunt marcate 🧩. Ploaia, umbrele și
  frunzele nu intră. Decupajele scenei au retenția pozelor (`retention_snapshots_days`),
  cu excepția celor cu verdict, care rămân pentru antrenament.
- Căutarea din antet caută și în text, fără diacritice: descrierile modelului de limbaj
  („mașină albă”), rezumatele episoadelor, nume, numere (`/api/journal?text=&zile=`).
  Verdictul omului pe un candidat ajunge și în indexul căutării (eticheta nouă, sau rândul
  iese dacă era doar fundal). Pragul de noapte al candidaților trimiși pe Telegram e setare
  (`scena_telegram_noapte`, Setări → Locație și vreme) și poate fi per cameră, din
  recomandări (`scena_noapte`), când ai respins de mai multe ori etichete de noapte. Pe
  vreme rea scena cere de trei ori mai multe cadre până la clasificare și înjumătățește
  bugetul de clasificări.
- Exemple învățate din verdicte, per cameră: orice verdict cu nume face din decupajul
  candidatului un exemplu al camerei (vectorul SigLIP, `data/scena/<cam>/fundal_invatat.*`).
  Numele de fundal („nimic”, „frunze”, „umbră”, „ploaie”, „copac”) se potrivesc la peste 0,85
  și bat orice etichetă: candidatul nu pleacă pe Telegram și nu intră în index. Numele de
  obiecte (✓ pe eticheta propusă sau ✕ cu numele adevărat: „găină”, „mașină”…) se potrivesc
  la peste 0,82 și înlocuiesc doar „necunoscut” și scorurile mărunte ale vocabularului, nu
  eticheta unui detector (sursa „învățat din verdicte”). Câștigă cel mai apropiat exemplu care
  își trece pragul. Pragurile, măsurate pe instalație: fundalurile seamănă între ele la ~0,88,
  găinile fără nume cu cele numite la ~0,87, iar prelata și pământul ating cel mult 0,82 față
  de găini. Exemplele se refac din `feedback.jsonl` la pornirea nvr-ai. În fișa camerei,
  necunoscuții și fundalul sunt ascunși implicit, cu un comutator.
- Butoane ✓ ✕ ∅ și sub notificările de detecție YOLO de pe Telegram (🚨 om / câine / pisică /
  urs), nu doar sub candidații scenei. Verdictul intră în același `feedback.jsonl`, cu
  decupajul din chenarul detecției (sidecarul snapshotului) salvat în `data/scena/<cam>/`:
  ✓ devine exemplu de obiect al camerei, ∅ exemplu de fundal, ✕ deschide vocabularul; toate
  intră în datasetul de fine-tune și în recomandarea de prag de noapte. Mesajele fără snapshot
  (cooldown-ul pozelor) rămân fără butoane.
- Server MCP (`POST /mcp`, cu jetonul API ca `X-Token` sau `Authorization: Bearer`): un
  asistent poate întreba sistemul ce s-a întâmplat (evenimente cu filtru de text, episoade,
  candidați), căuta în poze, cere cadrul unei camere, vedea recomandările și comuta modul.
  Puntea stdio `scripts/openvigil_mcp.py` pentru Claude Desktop / Claude Code; `docs/mcp.md`.
- Model de limbaj configurabil (`llm.py`): Qwen-ul local din nvr-ai sau, opțional, un server
  compatibil OpenAI (Ollama, LM Studio, vLLM) din Setări → Unelte, cu buton de test.
  Rezumatele episoadelor de prezență pot fi scrise de el (o propoziție, numai din fapte;
  șablonul rămâne ca `text_sablon`). Jurnalul se poate filtra după text.
- Vremea (Setări → Unelte → Locație și vreme): localitatea se caută după nume (geocodare
  Open-Meteo, fără cheie), vremea de afară se citește la 5 minute și se combină cu ce
  estimează camerele (ploaie din cadrele sărite ale scenei, vânt din zonele instabile, ceață
  din contrastul fundalului). Fiecare eveniment din jurnal primește câmpul `vreme`; un prag de
  încredere opțional (`vreme_prag`) se aplică detecției pe ploaie, ninsoare, ceață sau
  furtună; pe vreme rea candidații „necunoscut” nu mai pleacă pe Telegram (comutabil).
  Unealta MCP `vreme`; `data/vreme.json` e citit de nvr-ai fără rețea.
- Verdict nou în „Învață scena”: „∅ Nimic nou (doar fundal)”, pe Telegram și în fișă; „copac”
  în vocabularul implicit ca nume neutru.
- Întrebările în limbaj natural („when did the garbage truck come?”) merg și în engleză, cu
  răspunsuri în limba interfeței.
- `RECORDINGS_DIR`, `ARHIVA_DIR`, `BACKUP_DIR`: înregistrările, nivelul de arhivă și copiile
  nocturne pot sta pe alt disc decât restul datelor (bind-uri separate).

### Reparat
- Modelul de limbaj (descrieri de persoane/vehicule, analiza „ceva nou”) pica în imaginea
  Docker cu „Failed to find C compiler”: torch 2.13 trecea unele operații prin nuclee Triton,
  care cer un compilator. `TORCH_DISABLE_NATIVE_JIT=1` în imagine ține torch pe nucleele clasice.
- După descărcarea modelului de limbaj, torch rămânea cu ~8 GB de VRAM rezervați dar
  nefolosiți (fragmentare), iar următoarea încărcare pica cu „VRAM insuficient”.
  `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` în imagine (se pierduse la mutarea de pe
  systemd). Raportul `/capabilities` al nvr-ai arată acum VRAM-ul pe modele și fragmentarea.

- Sidecar-ul cu chenare al pozelor de detecție (`<poză>.json`, folosit de „găsește-l pe toate
  camerele” ca să taie omul exact din chenarul momentului) nu se scria niciodată: lipsea un
  import în `snaps.py`, iar eroarea era doar în jurnal („sidecar nescris: name 'store' is not
  defined”). Test de regresie.

## [1.0.0] — 2026-10-26

Prima versiune publicată.

### Adăugat
- Release-uri cu imagine publicată în GitHub Container Registry (`ghcr.io/sm26449/openvigil:1.0.0`,
  `:latest`; `:edge` din `main`); `install.sh` trage imaginea în loc să o construiască
  (`--build` pentru construcție locală). Versiunea apare în Setări → Stare, în meniul de pe
  telefon, în `/api/me` și `/healthz`.
- Versiune de schemă în bazele SQLite (`app/schema.py`, tabela `schema_versiuni`): migrările
  viitoare rulează o singură dată, în ordine. Secretele din `.env` pot veni din fișiere
  (`NVR_PASS_FILE`, `TELEGRAM_TOKEN_FILE`).
- Interfață multilingvă: română și engleză. Limba urmează browserul, poate fi fixată pe cont
  (Contul meu → Limba) sau din `?lang=`; `NVR_LANG` dă limba serverului (notificări, rezumat).
  Cheia unui text e textul românesc (`t()` în Python și JavaScript, `app/i18n.py`), pagina
  e tradusă pe server; dicționare JSON în `app/static/i18n/`, suprascrieri în `data/i18n/`;
  `scripts/i18n_check.py` verifică acoperirea.
- Mod demo: patru camere sintetice (vendor `demo`, go2rtc `ffmpeg:virtual`), din Setări → Unelte;
  fără acțiuni, fără rețea, înregistrate doar prin go2rtc.
- Site-ul proiectului: [openvigil.dev](https://openvigil.dev), servit de pe mașina noastră publică
  (`deploy/site/`), nu de pe GitHub Pages.
- CI pe GitHub Actions: pytest fără GPU (`requirements-ci.txt`), verificarea modulelor ES,
  shellcheck, validarea fișierelor Compose, căutarea de secrete; build-ul imaginii cu testele
  rulate în container (săptămânal și la schimbări de Dockerfile/lock). Template-uri de issue și PR.
- `docs/operare.md`: jurnale, actualizare, copii, porturi, GPU, dezinstalare; secțiuni de
  depanare în README.
- Repo-ul public OpenVigil, pornit de la zero din instalația de origine: configurare prin
  variabile de mediu (`config.py` + `.env`), o singură imagine Docker pentru web, record, ai
  și go2rtc, `docker-compose.yml` (+ suprapunerea `docker-compose.gpu.yml`), `install.sh`
  cu provizionarea Docker și a nvidia-container-toolkit, descărcarea modelelor la prima
  pornire (`scripts/modele.py`), licență AGPL-3.0 și `NOTICE.md` cu licențele modelelor.

### Schimbat
- Lanțul de custodie memorează căi relative la rădăcina proiectului (rândurile vechi se migrează).
- Imaginea Docker se construiește într-o singură etapă, cu lock-ul instalat `--no-deps`; testele
  sunt în imagine (`docker compose run --rm --no-deps web test`).
- Numele sistemului e o setare (`NVR_NAME`), nu un text fix.
- Criptarea pe disc nu mai e gestionată din aplicație: se face la nivelul gazdei (LUKS /
  fscrypt). Aplicația doar sesizează și așteaptă când directorul de înregistrări nu e scriibil.
- `ai_service` pornit implicit: inferența rulează în containerul `ai`.
