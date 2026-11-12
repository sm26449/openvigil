# Teste de interfață

Două suite Playwright, cu scopuri diferite.

## 1. Regresie izolată — `ui-regresie.js` (rulează în CI)

Pornește un server static pentru `app/static` și interceptează tot `/api/*` cu date
sintetice (`ui-fixtures.js`). **Fără instanță live, bază de date, GPU sau camere** — merge pe
orice runner cu Chromium. Testează modulele JS reale (nu o machetă): pagina e `index.html`
brut, iar cheia fiecărui text e textul românesc însuși (`app/i18n.py`), deci sursa e RO validă.

Reproduce exact stările pe care al cincilea audit le-a semnalat ca periculoase și pe care
corecturile din 2.0.16 le-au rezolvat — ca să rămână rezolvate la fiecare schimbare:

| Scenariu | Ce verifică |
|---|---|
| A1 | config AI nu confirmă „Salvat ✓" după HTTP 500 |
| A2 | editarea unei reguli **oprite** nu o reactivează (`activ` rămâne `false`) |
| S1 | o secțiune neîncărcată **blochează** salvarea globală (nu scrie default-uri peste config) |
| S2 | `0 dBFS` se trimite ca `0`, nu înlocuit cu `-25` |
| C2 | lipsa telemetriei = „se verifică", niciodată verde „în regula" |
| L2 | Enter pe un control din dală **nu** deschide HD (doar dala însăși, cu focus) |
| T1 | „Contul meu" e buton real, focusabil de la tastatură |
| C4 | lista de camere la 390px nu depășește ecranul |

```bash
cd tests
npm ci
npx playwright install --with-deps chromium
npm run test:ui      # === node ui-regresie.js
```
Gazda e Node 18, dar Playwright cere Node ≥20 — rulează în container:
```bash
docker run --rm -v "$PWD/..":/app -w /app/tests \
  -v ov-pw-cache:/ms-pw -e PLAYWRIGHT_BROWSERS_PATH=/ms-pw \
  node:24-slim bash -c \
  'npx --yes playwright@1.65.0 install --with-deps chromium && node ui-regresie.js'
```
Iese cu cod ≠0 dacă vreun scenariu pică. Scenariile noi se adaugă în `ui-regresie.js`;
formele de răspuns `/api/*` stau în `ui-fixtures.js` (`apiDefaults`), suprascrise per test.

## 2. Verificare pe instanță reală — `ui-test.js` (manual)

Parcurge o **instalare live** într-un browser real: navigarea între taburi, Setări, bara de
salvare, lista de camere, filtrele, timeline-ul, accesibilitatea de la tastatură, aspectul pe
telefon. Testează și autentificarea: login obligatoriu, apoi un cont *viewer* de test
(restrâns la 2 camere) — că UI-ul ascunde ce trebuie și că serverul refuză cu 403 orice
modificare. Contul de test se șterge singur la final.

```bash
cd tests && npm install
NVR_BASE=http://127.0.0.1:8079 NVR_TEST_USER=admin NVR_TEST_PASS=parola-ta npm run test:ui-live
```

## Testele de backend
```bash
python -m pytest tests/ -q
```

## Ce au prins până acum
- secțiunea Setări rămânea vizibilă sub celelalte pagini (`#set` cu `display:grid`,
  selector de id, învingea `.hidden` care e clasă)
- antetul și barele de filtre ieșeau 170px în afara ecranului pe telefon
- aceeași capcană de specificitate se repeta în regulă pentru ecrane înguste
- (izolat) confirmări false de salvare după 500 și verde fals fără telemetrie — vezi tabelul
