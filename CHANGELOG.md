# Schimbări

Formatul urmează [Keep a Changelog](https://keepachangelog.com/) și
[Conventional Commits](https://www.conventionalcommits.org/).

## [Nepublicat]

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
  auditează.
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
