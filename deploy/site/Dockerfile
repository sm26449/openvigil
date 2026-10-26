# openvigil.dev — site/ intr-un nginx neprivilegiat (port 8080), in spatele Traefik-ului de
# pe masina publica (TLS acolo). Contextul de build e radacina depozitului:
#   docker compose -f deploy/site/docker-compose.yml up -d --build
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY deploy/site/nginx.conf /etc/nginx/conf.d/default.conf
COPY site/ /usr/share/nginx/html/
# imaginile se schimba rar, dar Cloudflare le tine 7 zile: adresa lor poarta o amprenta a
# continutului (img/x.png?v=abcd1234), deci o captura noua e vazuta imediat, fara purge
USER root
RUN v=$(cat /usr/share/nginx/html/img/* | md5sum | cut -c1-8) \
 && sed -i "s#src=\"img/\([^\"?]*\)\"#src=\"img/\1?v=$v\"#g" /usr/share/nginx/html/index.html
USER nginx
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s CMD wget -qO- http://127.0.0.1:8080/healthz >/dev/null || exit 1
