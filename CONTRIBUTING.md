# Contribuții

OpenVigil a fost scris pentru o singură instalație și e publicat așa cum e, cu tot
istoricul deciziilor în `docs/ro/manual.md`. Contribuțiile sunt binevenite, cu câteva reguli
simple ca lucrurile să rămână coerente.

## Limba

- Codul, comentariile și commit-urile sunt în **română**; textul sursă al interfeței e tot
  în română (cu diacritice), iar cheile JSON și identificatorii rămân ASCII.
- Interfața e multilingvă (`app/i18n.py`, `t()` în `00-core.js`): **cheia unui text e
  textul românesc**. Orice text nou vizibil utilizatorului se scrie `t("Text românesc")` în
  Python sau `t('Text românesc')` în JavaScript, cu parametri `{x}` (nu f-string, nu bucăți
  lipite), și primește o intrare în `app/static/i18n/en.json`. Textul din `index.html` e
  tradus automat de server, nu are nevoie de marcaje. `python3 scripts/i18n_check.py`
  (rulat și de pytest) spune ce lipsește din dicționare.
- O limbă nouă = un fișier `app/static/i18n/<cod>.json` cu `_meta.nume` și toate cheile din
  `en.json`; glosarul de termeni e în `docs/i18n.md`.
- Documentația publică (`README.md`, acest fișier, `SECURITY.md`) e și în engleză.
- Issue-urile și pull request-urile pot fi scrise în engleză sau în română.

## Cum rulezi local

```bash
cp .env.example .env            # ajustează porturile dacă e nevoie
docker compose up -d --build    # sau cu -f docker-compose.gpu.yml pentru NVIDIA
docker compose run --rm --no-deps web test                    # pytest
# dezvoltare pe interfata: codul din repo montat in containerul web (schimbarile din app/static
# se vad la reincarcarea paginii; cele din Python dupa `docker compose restart web`)
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d web
cd tests && npm install && NVR_TEST_USER=admin NVR_TEST_PASS=... node ui-test.js   # browser
python3 scripts/module_check.py                     # modulele ES ale interfeței
```

Fără Docker (lock-ul trage PyTorch pentru CUDA 13 și onnxruntime-gpu, deci are sens doar pe o
mașină cu placă NVIDIA): `python3 -m venv venv && venv/bin/pip install -r requirements.lock`, apoi
`venv/bin/python app/server_main.py` (și `record_main.py`, `ai_main.py`, `gen_go2rtc.py` +
go2rtc) cu variabilele din `.env` exportate.

## Reguli de cod

- Python 3.12, fără framework-uri noi fără motiv; `requirements.lock` e sursa adevărului.
- O schimbare de tabelă SQLite nu se face în `CREATE TABLE IF NOT EXISTS`, ci ca migrare
  numerotată în `MIGRARI` din modulul respectiv (`app/schema.py` le aplică o singură dată,
  în ordine); schema inițială rămâne cea completă, pentru bazele noi.
- Interfața e în module ES simple (`app/static/js/*.js`), fără pas de build. Orice
  `innerHTML` nou trece prin `html\`\`` (escapare + CSP strict). `scripts/module_check.py`
  trebuie să afișeze `ok`.
- Rutele lungi sunt joburi (`app/jobs.py`), nu cereri care țin conexiunea.
- Niciun secret în cod, în teste sau în capturi: parolele camerelor stau în `data/`, contul
  de test vine din variabile de mediu.
- Testele însoțesc schimbarea: `tests/test_*.py` pentru backend, `tests/ui-test.js` pentru
  ce vede utilizatorul.

## Commit-uri

[Conventional Commits](https://www.conventionalcommits.org/) în română:
`feat(inregistrare): ...`, `fix(stare): ...`, `docs: ...`. Un commit = o schimbare cu sens.

## Pull request

1. Descrie problema, nu doar soluția.
2. Spune cum ai testat (comenzile de mai sus, pe ce hardware).
3. Dacă schimbi ceva din `docs/ai-contract.md` (contractul web ↔ ai), spune explicit.

Pentru probleme de securitate vezi `SECURITY.md`, nu deschide un issue public.
