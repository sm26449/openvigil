# Schimbări

Formatul urmează [Keep a Changelog](https://keepachangelog.com/) și
[Conventional Commits](https://www.conventionalcommits.org/).

## [Nepublicat]

### Adăugat
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
