*English or Romanian. Code comments and commit messages stay in Romanian (see CONTRIBUTING.md).*

**What it changes · Ce schimbă**


**Why · De ce**


**How it was tested · Cum am testat**
- [ ] `python -m pytest -q tests` (or `docker compose run --rm --no-deps web test`)
- [ ] `python scripts/module_check.py` (if the interface changed)
- [ ] `python scripts/i18n_check.py` (if user-visible text changed: new strings need an `en.json` entry)
- [ ] on a real installation: which hardware, which cameras

**For the reviewer · De verificat de cel care citește**
- [ ] no secret, address or installation name in code, tests or screenshots
- [ ] `docs/ai-contract.md` unchanged, or the change is described here
