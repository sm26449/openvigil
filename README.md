# OpenVigil

[![CI](https://github.com/sm26449/openvigil/actions/workflows/ci.yml/badge.svg)](https://github.com/sm26449/openvigil/actions/workflows/ci.yml)
[![Imagine Docker](https://github.com/sm26449/openvigil/actions/workflows/imagine.yml/badge.svg)](https://github.com/sm26449/openvigil/actions/workflows/imagine.yml)

A self-hosted NVR for IP cameras (Hikvision first, ONVIF second) that records everything
locally, runs its own AI on your GPU and never talks to a cloud. One Docker stack, one
install command.

*Română: [README.ro.md](README.ro.md) · manual detaliat: [docs/ro/manual.md](docs/ro/manual.md)*

> **Status.** Built for one real installation (18 Hikvision cameras, an NVIDIA RTX 2000 Ada,
> running 24/7 since mid-2026) and published as is, under AGPL-3.0. The user interface and
> the code comments are in Romanian. It is a working system, not a product: expect rough
> edges outside the hardware it was built on, and read [NOTICE.md](NOTICE.md) for the
> licences of the models it downloads.

## What it does

- **Records continuously** from every camera's main stream into 5-minute MP4 segments,
  with retention by days and by free disk space, motion-only mode per camera, and a
  chain of custody (SHA-256 per segment) for evidence.
- **Streams live** in the browser through go2rtc (MSE/WebRTC, two-way audio where the
  camera has a speaker), with an authenticated relay in front: go2rtc itself never
  listens on the LAN.
- **Playback** with a zoomable timeline, event markers, 0.5–8× speed (GPU), exports with
  burned-in boxes, time-lapse, heatmaps, region search.
- **Local AI**: YOLO11 detection (people, animals, vehicles, with fine-tuning on your own
  frames), face recognition, body re-identification, licence plates, natural-language
  descriptions (Qwen2.5-VL) and semantic search (SigLIP). All on the host GPU; CPU-only
  mode keeps detection and drops the heavy models.
- **Operations**: home/away/night modes with rules, Telegram alerts and a daily digest,
  Web Push, MQTT bridge (Home Assistant, Node-RED), webhooks, map view, siren/strobe/talk
  on supported cameras, camera health and tamper checks.
- **Accounts**: admin and per-camera viewers, 2FA (TOTP), revocable sessions, CSRF, strict
  CSP, audit log, public `/verifica` page to verify an exported clip by hash.
- **Resilience**: recorders fall back from go2rtc to the camera directly and come back on
  their own; nightly encrypted backup of configuration and databases; everything in
  `data/` is plain files and SQLite you can read without the app.

## Architecture

```
cameras (RTSP/ISAPI/ONVIF)
        │
        ▼
   ┌─────────┐  rtsp://127.0.0.1:8554/camN_main   ┌──────────┐
   │  go2rtc │ ─────────────────────────────────▶ │  record  │ ─▶ data/recordings/camN/*.mp4
   │ (host)  │                                    └──────────┘
   └────┬────┘   /api/ws (relay :STREAM_PORT)     ┌──────────┐
        └────────────────────────────────────────▶│   web    │ ◀── browser (:WEB_PORT)
                                                  │ Flask    │ ─▶ data/*.db, settings.json
                                                  └────┬─────┘
                                                       │ /detect, /faces, /describe (localhost)
                                                  ┌────▼─────┐
                                                  │    ai    │  YOLO11 · InsightFace · OSNet
                                                  │ (GPU)    │  Qwen2.5-VL · SigLIP
                                                  └──────────┘
```

All four containers share one image and the host network. Only `WEB_PORT` (UI) and
`8555/udp+tcp` (WebRTC media) are meant to be reachable from the LAN.

## Requirements

- Linux host with Docker Engine and Compose v2 (`install.sh` installs them on Debian/Ubuntu).
- Disk for recordings: plan ~1 GB per camera per hour at 4 MP H.265, less with H.265+.
- Optional but recommended: an NVIDIA GPU (driver ≥ 550). `install.sh` installs
  `nvidia-container-toolkit` when it finds `nvidia-smi`. Without a GPU you get libx264
  transcoding and CPU detection; faces, re-ID and descriptions stay off.
- Cameras on a LAN the host can reach. Hikvision cameras need ISAPI enabled and an
  account with RTSP access; ONVIF cameras get live/record/snapshots but no siren, strobe,
  talk or event hooks.

## Install

```bash
git clone https://github.com/sm26449/openvigil.git /opt/openvigil
cd /opt/openvigil
sudo ./install.sh
```

The script explains each step: Docker, GPU toolkit, `.env` (time zone, ports, data
directory, the uid that owns the files), then `docker compose up -d --build`. The first
build pulls ~6 GB of Python wheels (PyTorch with CUDA) and takes a while; later builds
reuse the layers. When the UI answers, open `http://<server-ip>:8079`, create the first
account on `/setup`, then add cameras from **Camere → Adaugă camera** (there is a LAN
scanner).

Everything you may want to change by hand is in `.env` (see `.env.example`). Everything
else is set from the UI and lives in `data/settings.json`.

Day-to-day:

```bash
docker compose logs -f web            # add -f docker-compose.gpu.yml on GPU hosts
sudo ./install.sh --update            # git pull, rebuild, restart
docker compose run --rm --no-deps web test      # pytest (350+ tests)
```

## Reverse proxy and remote access

Keep the UI off the public internet; put it behind Traefik, Caddy or nginx with TLS and
set in `.env`:

```
HTTPS=true
TRUSTED_PROXIES=127.0.0.0/8,::1/128,172.16.0.0/12
```

The proxy must forward `/api/ws` and `/api/frame.jpeg` to `STREAM_PORT` (the video relay)
and everything else to `WEB_PORT`. `/api/auth/forward` is a forward-auth endpoint if you
want other internal tools behind the same login.

## Data layout

```
data/
  recordings/camN/YYYY-MM-DD/HH-MM-SSZ.mp4   segments (UTC names)
  events.db, segmente.db, settings.json      journal, chain of custody, settings
  cameras.json, users.json, go2rtc.yaml      cameras (with passwords, mode 600), accounts
  faces/, plates/, reid/, dataset/, finetune/ AI data and training material
  backup/                                    nightly Fernet-encrypted backups (key in data/backup_key)
models/                                      Hugging Face cache (Qwen, SigLIP)
```

`data/` is a bind mount: back it up, and if you want encryption at rest put it on a LUKS
or fscrypt volume on the host. The app only notices when the directory is not writable.

## Troubleshooting

- **First build takes long / fails with "no space left"**: the image is ~11 GB and the build
  downloads ~6 GB of wheels. Keep 20 GB free on the Docker data root (`docker system df`).
- **`hwaccel GPU (h264_nvenc): NU` in the web log, or `torch cuda: False`**: the containers
  don't see the GPU. Check `nvidia-smi` on the host, then `docker info | grep -i runtimes`
  must list `nvidia`; rerun `sudo ./install.sh` to install the toolkit, and make sure you start
  the stack with `-f docker-compose.gpu.yml`.
- **Yellow banner "nu se poate scrie în directorul de înregistrări"**: the `DATA_DIR` on the
  host is not writable by `PUID:PGID` from `.env` (`chown`), or the volume is read-only.
- **Port already in use**: change `WEB_PORT`, `STREAM_PORT`, `AI_PORT`, `WEBRTC_PORT` or the
  go2rtc addresses in `.env`, then `docker compose up -d`.
- **Camera added but black tile / 502 thumbnail**: the host can't reach the camera's RTSP
  (port 554) or the credentials lack RTSP rights; `docker compose logs go2rtc` shows the
  dial error. Hikvision cameras also need ISAPI enabled for events, siren and talk.
- **AI features missing**: faces, descriptions and semantic search need the GPU profile;
  on CPU only detection runs. Re-ID needs the OSNet ONNX file (see NOTICE.md).
- **Logs**: `docker compose logs -f web record ai go2rtc`; passwords never appear in them.

Day-to-day operations (logs, updates, backups, ports, uninstall): [docs/operare.md](docs/operare.md) (Romanian).

## Tests

- `tests/test_*.py`: unit and API tests. Run inside the image with `docker compose run --rm --no-deps web test`,
  or on a plain machine with `pip install -r requirements-ci.txt && python -m pytest -q tests`
  (the AI-dependent tests skip themselves). This is what CI runs on every push.
- `tests/ui-test.js`: a Playwright walk through the real UI (needs a running stack and
  `NVR_TEST_USER` / `NVR_TEST_PASS` for an admin account of a test installation).
- `scripts/module_check.py`: static check of the ES modules.

## Documentation

- [docs/ro/manual.md](docs/ro/manual.md): the full manual, in Romanian, written while
  the system was built. The authoritative description of every feature and decision.
- [docs/nvr-ai.md](docs/nvr-ai.md), [docs/ai-contract.md](docs/ai-contract.md): the AI
  service and the web ↔ ai contract.
- [docs/node-red.md](docs/node-red.md): webhooks and MQTT with an example flow.
- [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md), [NOTICE.md](NOTICE.md).

## Licence

AGPL-3.0, see [LICENSE](LICENSE). Third-party components and model licences, including the
non-commercial terms of the InsightFace face models, are listed in [NOTICE.md](NOTICE.md).
Not affiliated with Hikvision, NVIDIA or ONVIF.
