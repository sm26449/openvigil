# Test functional al interfetei

Parcurge interfata intr-un browser real (Chromium prin Playwright) si verifica ce vede
utilizatorul: navigarea intre taburi, sectiunile din Setari, bara de salvare, lista de
camere si panoul de detaliu, filtrele, galeria de alarme, timeline-ul, densitatea grilei,
legaturile, accesibilitatea de la tastatura si aspectul pe telefon.

Testeaza si autentificarea: login-ul obligatoriu, apoi un cont viewer de test
(restrans la 2 camere) — ca UI-ul ascunde ce trebuie si, mai important, ca serverul
refuza cu 403 orice incercare de modificare. Contul de test se sterge singur la final.

## Rulare
```bash
cd tests && npm install
NVR_BASE=http://127.0.0.1:8079 NVR_TEST_USER=admin NVR_TEST_PASS=parola-ta node ui-test.js
```
Iese cu cod 1 daca ceva a picat, deci se poate folosi si automat.

Testele de backend (conturi, roluri, restrictia pe camere):
```bash
python -m pytest tests/ -q
```

## Ce a prins pana acum
- sectiunea Setari ramanea vizibila sub celelalte pagini (`#set` cu `display:grid`,
  selector de id, invingea `.hidden` care e clasa)
- antetul si barele de filtre ieseau 170px in afara ecranului pe telefon
- aceeasi capcana de specificitate se repeta in regula pentru ecrane inguste
