# OpenVigil

[![CI](https://github.com/sm26449/openvigil/actions/workflows/ci.yml/badge.svg)](https://github.com/sm26449/openvigil/actions/workflows/ci.yml)
[![Imagine Docker](https://github.com/sm26449/openvigil/actions/workflows/imagine.yml/badge.svg)](https://github.com/sm26449/openvigil/actions/workflows/imagine.yml)

NVR auto-găzduit pentru camere IP (Hikvision în primul rând, ONVIF în al doilea): înregistrează
totul local, rulează AI pe GPU-ul tău și nu vorbește cu niciun cloud. Un singur stack Docker,
o singură comandă de instalare.

**Site: [openvigil.dev](https://openvigil.dev)** · *English: [README.md](README.md) · manualul complet: [docs/ro/manual.md](docs/ro/manual.md)*

> **Stare.** Scris pentru o instalație reală (18 camere Hikvision, un RTX 2000 Ada, în
> funcțiune non-stop din 2026) și publicat așa cum e, sub AGPL-3.0. E un sistem care merge,
> nu un produs: pe alt hardware pot apărea colțuri netăiate. Licențele modelelor descărcate
> sunt în [NOTICE.md](NOTICE.md).

## Ce face

- **Înregistrează continuu** fluxul mare al fiecărei camere în segmente MP4 de 5 minute, cu
  retenție pe zile și pe spațiu liber, mod „doar la mișcare” per cameră și lanț de custodie
  (SHA-256 pe segment) pentru probe.
- **Live** în browser prin go2rtc (MSE/WebRTC, audio bidirecțional unde camera are difuzor),
  cu un releu autentificat în față: go2rtc nu ascultă niciodată pe LAN.
- **Redare** cu timeline cu zoom, marcaje de evenimente, viteze 0,5–8× (pe GPU), exporturi cu
  chenare, time-lapse, heatmap, căutare pe regiune.
- **AI local**: detecție YOLO11 (persoane, animale, vehicule, cu fine-tuning pe cadrele tale),
  recunoaștere de fețe, re-identificare după corp, numere de înmatriculare, descrieri în
  limbaj natural (Qwen2.5-VL) și căutare semantică (SigLIP). Fără GPU rămâne detecția.
- **Operare**: moduri acasă/plecat/noapte cu reguli, alerte Telegram și rezumat zilnic, Web
  Push, punte MQTT (Home Assistant, Node-RED), webhook-uri, hartă, sirenă/strobo/difuzor pe
  camerele care le au, verificarea sănătății camerelor.
- **Conturi**: admin și vizitatori restrânși pe camere, 2FA, sesiuni revocabile, CSRF, CSP
  strict, audit, pagina publică `/verifica` pentru confirmarea unei probe după amprentă.

## Instalare

```bash
git clone https://github.com/sm26449/openvigil.git /opt/openvigil
cd /opt/openvigil
sudo ./install.sh
```

Scriptul explică fiecare pas: Docker, toolkit-ul NVIDIA dacă există placă, `.env` (fus orar,
porturi, directorul de date, utilizatorul care deține fișierele), apoi construirea și
pornirea. Prima construire aduce ~6 GB de biblioteci Python (PyTorch cu CUDA). Când interfața
răspunde, deschide `http://<ip-server>:8079`, creează primul cont pe `/setup` și adaugă
camerele din **Camere → Adaugă camera** (există și un scanner de rețea).

Ce se schimbă de mână stă în `.env` (vezi `.env.example`); restul se setează din interfață și
ajunge în `data/settings.json`.

```bash
docker compose logs -f web            # pe gazde cu GPU adaugă -f docker-compose.gpu.yml
sudo ./install.sh --update            # git pull, rebuild, repornire
docker compose run --rm --no-deps web test      # pytest
```

## Acces din afară

Ține interfața în LAN și pune-o în spatele unui reverse proxy cu TLS (Traefik, Caddy, nginx),
cu `HTTPS=true` și `TRUSTED_PROXIES` în `.env`. Proxy-ul trimite `/api/ws` și
`/api/frame.jpeg` spre `STREAM_PORT`, restul spre `WEB_PORT`.

## Criptare, copii, date

`data/` e un director al gazdei: fă-i backup, iar dacă vrei criptare la rest pune-l pe un
volum LUKS sau fscrypt. Copiile nocturne (configurație, baze de date) sunt criptate cu cheia
din `data/backup_key`; fără ea nu valorează nimic, noteaz-o în altă parte.

## Depanare

- **Primul build durează sau cade cu „no space left”**: imaginea are ~11 GB, build-ul descarcă
  ~6 GB. Ține 20 GB liberi unde stă Docker (`docker system df`).
- **`hwaccel GPU (h264_nvenc): NU` în jurnalul web sau `torch cuda: False`**: containerele nu
  văd placa. `nvidia-smi` pe gazdă, apoi `docker info | grep -i runtimes` trebuie să conțină
  `nvidia`; rulează din nou `sudo ./install.sh` și pornește cu `-f docker-compose.gpu.yml`.
- **Banner galben „nu se poate scrie în directorul de înregistrări”**: `DATA_DIR` nu e al lui
  `PUID:PGID` din `.env` (`chown`) sau volumul e read-only.
- **Port ocupat**: schimbă `WEB_PORT`, `STREAM_PORT`, `AI_PORT`, `WEBRTC_PORT` sau adresele
  go2rtc în `.env`, apoi `docker compose up -d`.
- **Camera adăugată, dar cadru negru / miniatură 502**: gazda nu ajunge la RTSP-ul camerei
  (port 554) sau contul nu are drept de RTSP; `docker compose logs go2rtc` arată eroarea.
  Camerele Hikvision au nevoie și de ISAPI pornit pentru evenimente, sirenă, difuzor.
- **Lipsesc funcțiile AI**: fețele, descrierile și căutarea semantică cer profilul GPU; pe CPU
  rulează doar detecția. Re-ID cere fișierul OSNet (vezi NOTICE.md).

Operarea de zi cu zi (jurnale, actualizare, copii, porturi, dezinstalare): [docs/operare.md](docs/operare.md).

## Documentație

[docs/ro/manual.md](docs/ro/manual.md) este manualul complet, scris pe măsură ce sistemul a
fost construit. Apoi [docs/nvr-ai.md](docs/nvr-ai.md), [docs/ai-contract.md](docs/ai-contract.md),
[docs/node-red.md](docs/node-red.md), [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md).

## Licență

AGPL-3.0 ([LICENSE](LICENSE)). Componentele terțe și licențele modelelor, inclusiv condițiile
necomerciale ale modelelor de fețe InsightFace, sunt în [NOTICE.md](NOTICE.md). Proiectul nu
este afiliat cu Hikvision, NVIDIA sau ONVIF.
