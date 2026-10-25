---
name: Problemă / Bug
about: Ceva nu merge cum scrie în documentație
labels: bug
---

**Ce se întâmplă**
Descrie clar ce ai făcut, ce te așteptai și ce s-a întâmplat.

**Cum reproduc**
1.
2.

**Mediul**
- Versiunea OpenVigil (commit sau tag):
- Instalare: Docker (`install.sh`) / clasică
- Gazdă: distribuție + versiune, Docker `docker --version`, GPU (`nvidia-smi`, sau „fără”)
- Camere: model + firmware (ex. DS-2CD2T65FWD-I8, V5.6.2), Hikvision sau ONVIF

**Jurnale**
```
docker compose logs --since 30m web record ai go2rtc
```
Scoate parolele și adresele pe care nu vrei să le publici; `rtsp://user:parola@` nu trebuie să apară.
