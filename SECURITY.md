# Securitate

## Raportare

Dacă găsești o vulnerabilitate, **nu deschide un issue public**. Scrie la adresa din profilul
GitHub al proprietarului repo-ului sau folosește „Report a vulnerability” din tab-ul Security
al repo-ului. Primești răspuns în cel mult 7 zile; corecțiile se publică cu o notă în
`CHANGELOG.md`.

## Modelul de amenințare, pe scurt

OpenVigil e un NVR de casă: stă în LAN, iar accesul din afară se face doar printr-un reverse
proxy cu TLS. Ce e proiectat să reziste:

- **go2rtc nu e expus**: API-ul și RTSP-ul ascultă pe `127.0.0.1`; browserul primește
  fluxuri doar prin releul autentificat al aplicației (`STREAM_PORT`), cu sesiune și cu
  lista de camere permise contului.
- **Conturi cu roluri** (admin / viewer restrâns pe camere), parole cu hash, 2FA TOTP,
  sesiuni revocabile, CSRF pe orice mutație, limitare de rată la login, audit cu utilizator.
- **CSP strict**, fără script inline; orice HTML generat trece prin escapare.
- **Parolele camerelor** nu apar în procesele `ps`: înregistrarea citește de la go2rtc,
  iar erorile ffmpeg sunt curățate de `user:parolă@` înainte de jurnal.
- **Probe verificabile**: exporturile au amprentă SHA-256 într-un registru, segmentele brute
  intră într-un lanț de custodie; `/verifica` confirmă fără cont.

Ce **nu** acoperă și rămâne în seama ta:

- Criptarea pe disc. În varianta Docker se face la nivelul gazdei (LUKS sau fscrypt pe
  directorul `data/`); aplicația doar sesizează când directorul nu e scriibil.
- Rețeaua camerelor. Camerele vorbesc RTSP/ISAPI necriptat; ține-le într-un VLAN fără
  acces la internet.
- Reverse proxy-ul. Pune `HTTPS=true` și `TRUSTED_PROXIES` corect, altfel adresa clientului
  din audit poate fi falsificată.

## Dependențe

`requirements.lock` fixează versiunile. Actualizările se fac deliberat, cu testele trecute;
nu există auto-update în containere.
