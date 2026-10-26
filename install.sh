#!/usr/bin/env bash
# OpenVigil — instalare pe o gazda Linux cu Docker.
#
#   sudo ./install.sh            # instaleaza Docker si (daca e placa NVIDIA) toolkit-ul,
#                                # creeaza .env, data/, models/, trage imaginea publicata
#                                # (ghcr.io/sm26449/openvigil, ~11 GB) si porneste
#   sudo ./install.sh --build    # construieste imaginea local, din codul de aici (~20 min, ~6 GB)
#   sudo ./install.sh --no-gpu   # ignora placa video chiar daca exista
#   sudo ./install.sh --update   # git pull + imaginea noua (sau rebuild) + repornire, cu .env-ul existent
#
# Scriptul explica fiecare pas si nu atinge nimic in afara acestui director, Docker si,
# optional, nvidia-container-toolkit. Se poate rula de mai multe ori.
set -euo pipefail

AICI="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$AICI"
GPU=auto
UPDATE=0
BUILD=0
for arg in "$@"; do
  case "$arg" in
    --no-gpu) GPU=no ;;
    --gpu) GPU=yes ;;
    --update) UPDATE=1 ;;
    --build) BUILD=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "argument necunoscut: $arg"; exit 2 ;;
  esac
done

spune() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
gata() { printf '   \033[32m%s\033[0m\n' "$*"; }
avert() { printf '   \033[33m%s\033[0m\n' "$*"; }

if [ "$(id -u)" -ne 0 ]; then
  echo "Ruleaza cu sudo: instalarea Docker si a toolkit-ului NVIDIA cer root." >&2
  exit 1
fi
UTILIZATOR="${SUDO_USER:-root}"
if [ "$UTILIZATOR" = root ]; then
  # rulat direct ca root (VPS): fisierele nu pot fi ale lui uid 0 in container (useradd -u 0
  # esueaza), deci folosim un utilizator de sistem dedicat pe gazda
  # uid 1000 daca e liber: imaginea publicata ruleaza cu 1000:1000 (alt uid cere --build)
  if ! id openvigil >/dev/null 2>&1; then
    if ! getent passwd 1000 >/dev/null; then
      groupadd -g 1000 openvigil 2>/dev/null || true
      useradd -u 1000 -g 1000 --no-create-home --shell /usr/sbin/nologin openvigil
    else
      useradd --system --no-create-home --shell /usr/sbin/nologin openvigil
    fi
  fi
  UTILIZATOR=openvigil
fi
PUID_IMPLICIT="$(id -u "$UTILIZATOR")"
PGID_IMPLICIT="$(id -g "$UTILIZATOR")"

# ---------------------------------------------------------------- 1. Docker + Compose
spune "1/5 Docker"
if ! command -v docker >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    avert "Docker lipseste: il instalez din pachetele distributiei (docker.io, docker-compose-v2)."
    apt-get update -q
    apt-get install -y -q docker.io docker-compose-v2 curl ca-certificates
    systemctl enable --now docker
  else
    echo "Docker lipseste si distributia nu e bazata pe apt. Instaleaza Docker Engine + Compose v2" >&2
    echo "(https://docs.docker.com/engine/install/) si ruleaza din nou." >&2
    exit 1
  fi
fi
if ! docker compose version >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y -q docker-compose-v2
  else
    echo "Lipseste pluginul 'docker compose' (v2)." >&2; exit 1
  fi
fi
gata "$(docker --version) · $(docker compose version | head -1)"
if [ "$UTILIZATOR" != "root" ] && ! id -nG "$UTILIZATOR" | grep -qw docker; then
  usermod -aG docker "$UTILIZATOR"
  avert "$UTILIZATOR a fost adaugat in grupul docker (activ de la urmatoarea autentificare)."
fi

# ---------------------------------------------------------------- 2. GPU NVIDIA (optional)
spune "2/5 Placa video"
FISIERE_COMPOSE=(-f docker-compose.yml)
ARE_GPU=0
if [ "$GPU" != "no" ] && command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  ARE_GPU=1
  gata "NVIDIA: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -1)"
  if ! docker info 2>/dev/null | grep -q "Runtimes:.*nvidia"; then
    if command -v apt-get >/dev/null 2>&1; then
      avert "nvidia-container-toolkit lipseste: il instalez din depozitul NVIDIA (pasii oficiali)."
      curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
        | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg --yes
      curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
        | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g' \
        > /etc/apt/sources.list.d/nvidia-container-toolkit.list
      apt-get update -q
      apt-get install -y -q nvidia-container-toolkit
      nvidia-ctk runtime configure --runtime=docker
      # runtimes din daemon.json se reincarca cu SIGHUP; restart-ul ar opri TOATE containerele gazdei
      systemctl reload docker
      sleep 2
      docker info 2>/dev/null | grep -q nvidia || systemctl restart docker
    else
      avert "nvidia-container-toolkit lipseste si nu-l pot instala automat; continui FARA GPU."
      ARE_GPU=0
    fi
  fi
  if [ "$ARE_GPU" = 1 ]; then
    gata "runtime-ul nvidia e configurat in Docker"
    FISIERE_COMPOSE+=(-f docker-compose.gpu.yml)
  fi
elif [ "$GPU" = "yes" ]; then
  echo "--gpu cerut, dar nvidia-smi nu merge." >&2; exit 1
else
  avert "fara placa NVIDIA: transcodare pe CPU (libx264), inferenta YOLO pe CPU, fara VLM."
fi

# ---------------------------------------------------------------- 3. .env, data/, models/
spune "3/5 Configurare"
if [ ! -f .env ]; then
  cp .env.example .env
  TZ_GAZDA="$(timedatectl show -p Timezone --value 2>/dev/null || cat /etc/timezone 2>/dev/null || echo UTC)"
  # limba implicita (notificari + interfata cand browserul nu alege): din LANG-ul gazdei
  LIMBA_GAZDA="en"; case "${LANG:-}" in ro*) LIMBA_GAZDA="ro";; esac
  sed -i "s#^TZ=.*#TZ=${TZ_GAZDA}#; s#^PUID=.*#PUID=${PUID_IMPLICIT}#; s#^PGID=.*#PGID=${PGID_IMPLICIT}#; s#^NVR_LANG=.*#NVR_LANG=${LIMBA_GAZDA}#" .env
  gata ".env creat din .env.example (TZ=${TZ_GAZDA}, NVR_LANG=${LIMBA_GAZDA}, PUID=${PUID_IMPLICIT}, PGID=${PGID_IMPLICIT})"
  avert "Deschide .env daca vrei alt port, alt nume sau alt director de date, apoi ruleaza din nou."
else
  gata ".env exista, il pastrez"
fi
# citim doar cheile de care avem nevoie (valorile pot avea spatii, nu „sourcing")
env_val() { sed -n "s/^$1=//p" .env | tail -1 | tr -d '\r'; }
DATA_DIR="$(env_val DATA_DIR)"; MODELS_DIR="$(env_val MODELS_DIR)"
PUID="$(env_val PUID)"; PGID="$(env_val PGID)"; WEB_PORT="$(env_val WEB_PORT)"
DATA_DIR="${DATA_DIR:-./data}"; MODELS_DIR="${MODELS_DIR:-./models}"
mkdir -p "$DATA_DIR" "$MODELS_DIR"
chown "${PUID:-$PUID_IMPLICIT}:${PGID:-$PGID_IMPLICIT}" "$DATA_DIR" "$MODELS_DIR"
chown "$UTILIZATOR" .env; chmod 600 .env
gata "date in $DATA_DIR, modele in $MODELS_DIR (ale lui uid ${PUID:-$PUID_IMPLICIT})"

# ---------------------------------------------------------------- 4. build + pornire
spune "4/5 Construire si pornire"
if [ "$UPDATE" = 1 ] && [ -d .git ]; then
  # fara acces la depozit (privat, fara credentiale pentru acest utilizator) nu ne oprim:
  # construim din codul local si spunem clar ce s-a intamplat
  PROPRIETAR="$(stat -c %U .git)"
  if ! sudo -u "$PROPRIETAR" git -c safe.directory="$PWD" pull --ff-only; then
    avert "git pull a esuat (depozit inaccesibil sau modificari locale): construiesc din codul de pe disc"
  fi
fi
# imaginea publicata ruleaza ca 1000:1000; alt PUID/PGID inseamna constructie locala
if grep -qE '^OPENVIGIL_IMAGE=openvigil$' .env; then BUILD=1; fi
if [ "$BUILD" = 0 ] && { [ "${PUID:-1000}" != 1000 ] || [ "${PGID:-1000}" != 1000 ]; }; then
  avert "PUID/PGID=${PUID:-1000}/${PGID:-1000} nu sunt 1000: imaginea publicata nu se potriveste, construiesc local"
  BUILD=1
fi
if [ "$BUILD" = 1 ]; then
  grep -qE '^OPENVIGIL_IMAGE=' .env || printf 'OPENVIGIL_IMAGE=openvigil\nOPENVIGIL_VERSION=local\n' >> .env
  export OPENVIGIL_BUILD_VERSION="$(git describe --tags --always --dirty 2>/dev/null || echo local)"
  spune "construiesc imaginea local (versiunea $OPENVIGIL_BUILD_VERSION)"
  docker compose "${FISIERE_COMPOSE[@]}" up -d --build --remove-orphans
else
  docker compose "${FISIERE_COMPOSE[@]}" pull
  docker compose "${FISIERE_COMPOSE[@]}" up -d --remove-orphans
fi
printf 'COMPOSE_FILE=%s\n' "$(IFS=:; echo "${FISIERE_COMPOSE[*]//-f /}")" > .compose-files
chown "$UTILIZATOR" .compose-files 2>/dev/null || true
gata "containerele ruleaza: $(docker compose "${FISIERE_COMPOSE[@]}" ps --services --status running | tr '\n' ' ')"

# ---------------------------------------------------------------- 5. verificare
spune "5/5 Verificare"
PORT="${WEB_PORT:-8079}"
for _ in $(seq 1 60); do
  if curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then break; fi
  sleep 3
done
if curl -sf "http://127.0.0.1:${PORT}/healthz" >/dev/null 2>&1; then
  IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
  gata "interfata raspunde: http://${IP:-<ip-server>}:${PORT}"
  echo
  echo "   Primul cont se creeaza din browser, la prima deschidere (/setup)."
  echo "   Jurnal:      docker compose ${FISIERE_COMPOSE[*]} logs -f"
  echo "   Oprire:      docker compose ${FISIERE_COMPOSE[*]} down"
  echo "   Actualizare: sudo ./install.sh --update"
else
  avert "interfata nu a raspuns in 3 minute; vezi: docker compose ${FISIERE_COMPOSE[*]} logs web"
  exit 1
fi
