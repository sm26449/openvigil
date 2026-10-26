# openvigil.dev

Pagina din `site/` rulează pe mașina publică a proiectelor noastre, în spatele Traefik-ului
ei (TLS Let's Encrypt prin DNS-01 Cloudflare, Cloudflare proxy în față), ca un container
nginx neprivilegiat pe rețeaua Docker `web`, fără porturi publicate.

Actualizare, pe mașină:

```bash
cd /home/website/sites/openvigil
git pull --ff-only
docker compose -f deploy/site/docker-compose.yml up -d --build
```

Verificare: `curl -sI https://openvigil.dev | head -5` și `/healthz`.
