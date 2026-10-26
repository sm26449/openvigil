# Operare de zi cu zi (instalarea Docker)

Toate comenzile se dau din directorul instalării (unde e `docker-compose.yml`). Pe o gazdă cu
placă NVIDIA adaugă mereu `-f docker-compose.yml -f docker-compose.gpu.yml`; mai jos scriem
`dc` pentru asta:

```bash
alias dc='docker compose -f docker-compose.yml -f docker-compose.gpu.yml'   # cu GPU
alias dc='docker compose'                                                    # fără GPU
```

## Stare, jurnale, repornire

```bash
dc ps                          # cele patru servicii și starea lor (healthy / starting)
dc logs -f web                 # interfața + API; la fel: record, ai, go2rtc
dc logs --since 1h record      # ce a făcut înregistrarea în ultima oră
dc restart ai                  # repornește doar inferența (modelele se reîncarcă, ~1 min)
dc down && dc up -d            # tot stack-ul (înregistrarea se oprește ~20 s)
curl -s http://127.0.0.1:8079/healthz   # {"ok": true, ...} când totul e în ordine
```

Serviciile sunt `restart: unless-stopped`: după un reboot al gazdei pornesc singure.

## Actualizare

```bash
sudo ./install.sh --update
```
Face `git pull`, reconstruiește imaginea (prima dată ~15 min, apoi doar straturile schimbate)
și recreează containerele. Înregistrarea se oprește cât durează recrearea (sub 30 s).

Revenire la o versiune anterioară: `git checkout <tag sau commit>` și din nou
`sudo ./install.sh` (fără `--update`, ca să nu facă `git pull`).

## Date și copii

- `data/` (sau `DATA_DIR` din `.env`) conține tot: înregistrări, baze de date SQLite, setări,
  camere cu parole, conturi, modele AI locale. Backup-ul lui `data/` fără `recordings/` e mic
  și suficient pentru a reface instalarea.
- Copia nocturnă (Setări → Sistem) scrie în `data/backup/` o arhivă criptată cu cheia din
  `data/backup_key`. Fără cheie copia e inutilă: noteaz-o într-un manager de parole.
- Restaurare pe o mașină nouă: instalează, oprește stack-ul, apoi
  `docker compose run --rm web venv/bin/python scripts/restaureaza-backup.py <arhiva> data --cheie <cheie>`
  și pornește din nou.
- Spațiul pe disc: `RECORD_MIN_FREE_GB` din `.env` e garda sub care se șterg cele mai vechi
  segmente; `RECORD_KEEP_DAYS` e retenția dorită. Setări → Înregistrări arată cât încape de fapt.

## Porturi și acces

| Port | Serviciu | Expus în LAN? |
|---|---|---|
| `WEB_PORT` (8079) | interfața și API-ul | da |
| `STREAM_PORT` (8076) | releul video autentificat (WS/MSE) | da, doar prin interfață |
| `WEBRTC_PORT` (8555) | media WebRTC | da (UDP+TCP) |
| 1984, 8554 | go2rtc API și RTSP | nu, doar 127.0.0.1 |
| `AI_PORT` (8077) | nvr-ai | nu, doar 127.0.0.1 |

Din afară: reverse proxy cu TLS în fața lui `WEB_PORT` și `STREAM_PORT`, cu `HTTPS=true` în
`.env` (vezi README). Nu publica niciodată 1984/8554.

## Limba

`NVR_LANG=ro|en` în `.env` (apoi `docker compose up -d`) e limba serverului: notificările,
rezumatul zilnic și interfața când browserul nu cere o limbă disponibilă. Browserul și
preferința contului (Contul meu → Limba) au prioritate. Un text care nu-ți place se
suprascrie fără a atinge aplicația: `data/i18n/en.json` (sau `ro.json`) cu
`{"textul românesc": "textul tău"}`; cheile sunt textele din `app/static/i18n/en.json`.
După ce schimbi fișierul: `docker compose restart web`.

## Camere

- Adăugare: Camere → Adaugă camera (IP, cont, parolă) sau scanner-ul de rețea. Parola rămâne
  în `data/cameras.json` și `data/go2rtc.yaml` (0600), nu apare în jurnale sau `ps`.
- Un cont dedicat pe cameră (operator cu RTSP + ISAPI) e mai sigur decât `admin`.
- Când camera e indisponibilă, înregistrarea reîncearcă singură (go2rtc, apoi direct) și revine.

## GPU

```bash
docker run --rm --runtime=nvidia -e NVIDIA_VISIBLE_DEVICES=all openvigil:local \
  venv/bin/python -c "import torch; print(torch.cuda.is_available())"
```
`True` înseamnă că driverul și toolkit-ul sunt în regulă. În jurnalul `web` caută
`hwaccel GPU (h264_nvenc): DA`, în `ai` caută `yolo pe GPU`.

## Dezinstalare

```bash
dc down
docker rmi openvigil:local
rm -rf <directorul instalării>      # data/ e înăuntru, dacă nu ai mutat DATA_DIR
```
