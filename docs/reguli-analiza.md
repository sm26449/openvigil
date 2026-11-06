# Reguli de analiză (RFC, v1) — prima regulă: căderea unei persoane

> Ideea: perceția (ce e în cadru) o avem deja — YOLO + track + scenă + VLM. Ne
> lipsește un strat subțire de **reguli**: „ce înseamnă și pe cine anunț". O
> regulă nu e un model nou, ci un modul mic cu patru pași peste ce există:
>
> **percepție → candidat → confirmare → politică → escaladare.**
>
> Fiecare regulă viitoare (viteză, flacără, loitering) se lipește pe același
> schelet, fără să rescriem nimic. Prima regulă validează scheletul: **căderea
> unei persoane**, cu alertă escaladată către un superior.

## Scop și non-scop

**Scop (v1):** o persoană care cade și **rămâne jos** este detectată, confirmată
și escaladată, cu fals-pozitive ținute sub control. Pornire per cameră, praguri
reglabile din UI.

**Non-scop:** NU e un sistem de siguranța vieții (nu înlocuiește un buton de panică
sau un senzor medical) — e o alertă asistivă. Viteza vehiculelor și flacăra/fumul
sunt reguli ulterioare, pe același schelet (vezi „Următoarele reguli").

## Unde trăiește (respectă `ai-contract.md`)

| Pas | Proces | De ce |
|-----|--------|-------|
| Percepție + candidat (pose, semnalele de cădere) | **nvr-ai** | cere model (pose) + VLM — o singură instanță, în VRAM |
| Confirmare (VLM „persoană căzută?") | **nvr-ai** | Qwen e deja încărcat acolo |
| Politică (zonă, cooldown, mod) + jurnal + escaladare | **nvr-web** | cooldown-urile și regulile au UN singur stăpân (ca restul) |

nvr-ai emite un eveniment `cadere` prin `emit_fn` (ca `scena_candidat`); nvr-web
aplică politica în `rules._journal` și decide escaladarea. Cârligul din nvr-ai e
**exact acolo unde se cheamă acum `faces.process`** (detect.py, când YOLO confirmă
un `person`): refolosim cadrul HD deja adus pentru fețe, deci costul e per
eveniment, nu per cadru.

## Căderea: cum o detectăm (3 filtre în serie, din ieftin spre scump)

1. **Semnal de pose (ieftin, pe cadrul HD).** YOLO-pose pe decupajul persoanei →
   17 keypoints COCO. Calculăm un *scor de cădere* din semnale geometrice, pure și
   testabile (`reguli.scor_cadere`):
   - unghiul trunchiului (linia umeri→șolduri) aproape de **orizontală**;
   - **raportul bbox** devine lat (lățime > înălțime);
   - capul coborât aproape de nivelul șoldurilor / jos în cadru.
   Sub prag → nimic (nu mai cheltuim nimic).
2. **Persistență (dwell).** Un scor mare o singură dată = aplecat/ghemuit/căzut pe
   scaun. Cerem ca persoana să fie **jos și aproape nemișcată ≥ `cadere_dwell_s`**
   (implicit 6 s), urmărită prin track-ul existent. Dacă se ridică în fereastră →
   anulat. Aici se taie 90% din fals-pozitive.
3. **Confirmare VLM (scumpă, o singură dată).** Pe cel mai bun cadru, o întrebare
   la Qwen: „este o persoană căzută / întinsă pe jos?". Dacă `cadere_vlm` e pornit
   și VLM infirmă → nu escaladăm (dar jurnalizăm, ca semnal de reglaj).

## Politică + escaladare

- **Pornire per cameră** (`cadere` în detect.json, ca `scena_noapte`): o cameră de
  stradă nu vrea asta; una de interior/curte da. Respectă zona de detecție a camerei.
- **Severitate `critic`** → incident în feed (pop-up la critic există deja) +
  notificare Telegram cu cel mai bun cadru + link în redare.
- **Escaladare „superior" = un `POST /v1/fire` către serviciul extern de alerte (alertd).**
  Nu reimplementăm SMS/Telegram: nvr-web trimite `{type: "nvr_cadere", context:{nume_cam, ora, link}}`
  prin `escaladare.escaladeaza`, iar alertd face fan-out pe canalele lui (Telegram + SMS + email),
  cu throttling/retry/audit. Setări: `alert_service_url`, `alert_service_key` (gol = oprit),
  `alert_service_type`. Decuplat și opțional — OpenVigil rămâne de sine stătător; alerta primară
  (Telegramul propriu + incident + pop-up) pleacă oricum, chiar dacă alertd nu răspunde.
- **Cooldown** per cameră (`cadere_cooldown_s`, implicit 120) ca o cădere să nu
  trimită zece mesaje.

## Praguri (implicite, toate reglabile din UI)

| Setare | Implicit | Rol |
|--------|----------|-----|
| `cadere_pose_conf` | 0.5 | încredere minimă pe keypoints |
| `cadere_scor_prag` | 0.6 | pragul scorului geometric de cădere |
| `cadere_dwell_s` | 6 | cât trebuie să rămână jos |
| `cadere_vlm` | true | confirmare VLM înainte de escaladare |
| `cadere_cooldown_s` | 120 | între două alerte pe aceeași cameră |
| `cadere_persoana_min_px` | 60 | ignoră persoanele prea mici (departe) |

## Performanță / VRAM

Pose rulează **doar când YOLO a confirmat deja un `person`** (de câteva ori/oră pe
cameră, ca fețele), pe decupaj, cu un model mic (yolo*-pose, zeci de MB). VLM-ul e
deja încărcat. Impact pe GPU: neglijabil față de ce rulează acum.

## Plan de test

- **Unitar (fără model):** `reguli.scor_cadere(keypoints, bbox)` pe cazuri
  (în picioare, aplecat, întins) + mașina de stări dwell (`reguli.Cadere`): intră în
  „jos", se anulează la ridicare, declanșează după dwell, respectă cooldown. VLM și
  pose se monkeypatch-uiesc.
- **Live:** cameră de interior, o cădere simulată controlat; verificăm candidat →
  dwell → VLM → incident critic → escaladare, și că așezatul/aplecatul NU declanșează.

## Faze de livrare

1. **Schelet + logica pură + teste** (`app/reguli.py`, `tests/test_reguli.py`) — cod
   pur, zero model, zero risc. *(Aceasta e livrabilă acum.)*
2. **Pose în nvr-ai + cârlig în `detect.py`** (cere restart ai + descărcarea modelului,
   ~2 min fără detecție — se anunță).
3. **Confirmare VLM + escaladare + UI (comutator per cameră, praguri) + i18n.**
4. **Reglaj live** pe praguri.

## Următoarele reguli (același schelet)

- **Viteza vehiculului:** track pe vehicule → px/timp; fără calibrare = „rapid/normal",
  cu calibrare per-cameră (2 repere + distanță) ≈ km/h. Cere întâi metadate de
  geometrie per cameră. Cârlig lângă `plates.process`.
- **Flacără/fum:** detector dedicat (antrenat de la zero pe dataset propriu) +
  confirmare VLM multi-cadru. Feature de siguranță → disclaimer explicit, nu înlocuiește
  senzorii.
- **Loitering / linie trecută / obiect abandonat:** reguli simple pe track + zone.
