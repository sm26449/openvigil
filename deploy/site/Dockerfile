# openvigil.dev — site/ intr-un nginx neprivilegiat (port 8080), in spatele Traefik-ului de
# pe masina publica (TLS acolo). Contextul de build e radacina depozitului:
#   docker compose -f deploy/site/docker-compose.yml up -d --build
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY deploy/site/nginx.conf /etc/nginx/conf.d/default.conf
COPY site/ /usr/share/nginx/html/
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
