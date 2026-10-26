# Schimbări

Formatul urmează [Keep a Changelog](https://keepachangelog.com/) și
[Conventional Commits](https://www.conventionalcommits.org/).

## [Nepublicat]

### Adăugat
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
- Site-ul proiectului: [openvigil.dev](https://openvigil.dev).
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
