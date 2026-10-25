# OpenVigil — o singura imagine pentru toate serviciile (web, record, ai, go2rtc).
# Straturile grele (venv cu torch/CUDA) sunt comune; comanda de pornire alege serviciul.
# O singura etapa, intentionat: venv-ul de ~10 GB nu se copiaza intre etape, deci build-ul
# cere pe disc doar imaginea finala, nu de doua ori atat.
#
#   docker compose build            # sau: docker build -t openvigil .
#
FROM ubuntu:24.04
ARG GO2RTC_VERSION=1.9.14
ARG PUID=1000
ARG PGID=1000
ENV DEBIAN_FRONTEND=noninteractive PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC HF_HOME=/home/openvigil/.cache/huggingface \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,video
WORKDIR /opt/openvigil

# ffmpeg din Ubuntu are h264_nvenc/hevc_nvenc si -hwaccel cuda: bibliotecile driverului vin
# de la nvidia-container-toolkit la rulare. Fara GPU raman libx264 si decodarea pe CPU.
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3 python3-venv ffmpeg curl ca-certificates tzdata libglib2.0-0 libgomp1 smartmontools \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSL -o /usr/local/bin/go2rtc \
        "https://github.com/AlexxIT/go2rtc/releases/download/v${GO2RTC_VERSION}/go2rtc_linux_amd64" \
    && chmod +x /usr/local/bin/go2rtc \
    && (userdel -r ubuntu 2>/dev/null || true) \
    && (getent group "${PGID}" >/dev/null || groupadd -g "${PGID}" openvigil) \
    && useradd -m -u "${PUID}" -g "${PGID}" -s /bin/bash openvigil

# venv-ul: exact lock-ul (`pip freeze` complet), cu --no-deps ca pip sa nu adauge dependente
# nepinuite (ex. onnxruntime CPU peste onnxruntime-gpu, care pierde providerul CUDA).
# Uneltele de compilare se scot in acelasi strat, ca sa nu ramana in imagine.
COPY requirements.lock .
RUN apt-get update && apt-get install -y --no-install-recommends python3-dev build-essential git \
    && python3 -m venv venv && venv/bin/pip install --upgrade pip wheel \
    && venv/bin/pip install --no-deps -r requirements.lock \
    && apt-get purge -y --auto-remove python3-dev build-essential git \
    && rm -rf /var/lib/apt/lists/* /root/.cache

COPY --chown=${PUID}:${PGID} app app
COPY --chown=${PUID}:${PGID} scripts scripts
COPY --chown=${PUID}:${PGID} docs docs
COPY --chown=${PUID}:${PGID} tests tests
COPY --chown=${PUID}:${PGID} config.py gen_go2rtc.py requirements.txt LICENSE NOTICE.md ./
COPY docker/entrypoint.sh /usr/local/bin/openvigil
RUN chmod +x /usr/local/bin/openvigil && mkdir -p data /home/openvigil/.cache \
    && chown -R "${PUID}:${PGID}" data /home/openvigil
USER openvigil
VOLUME ["/opt/openvigil/data", "/home/openvigil/.cache"]
ENTRYPOINT ["/usr/local/bin/openvigil"]
CMD ["web"]
