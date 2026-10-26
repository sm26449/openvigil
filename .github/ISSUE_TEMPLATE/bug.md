---
name: Bug / Problemă
about: Something does not work as documented · Ceva nu merge cum scrie în documentație
labels: bug
---

*English or Romanian, as you prefer. · Scrie în engleză sau în română, cum îți e mai ușor.*

**What happens · Ce se întâmplă**
What you did, what you expected, what happened instead.

**Steps to reproduce · Cum reproduc**
1.
2.

**Environment · Mediul**
- OpenVigil version (commit or tag):
- Install: Docker (`install.sh`) / manual
- Host: distribution + version, `docker --version`, GPU (`nvidia-smi`, or "none")
- Cameras: model + firmware (e.g. DS-2CD2T65FWD-I8, V5.6.2), Hikvision or ONVIF
- Interface language: en / ro

**Logs · Jurnale**
```
docker compose logs --since 30m web record ai go2rtc
```
Remove passwords and addresses you do not want public; `rtsp://user:password@` must not appear.
