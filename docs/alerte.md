# Alerte prin serviciul extern de alerte

OpenVigil își trimite singur notificările pe Telegram și email. Opțional, alertele de sistem pot
trece printr-un serviciu extern de alerte care primește `POST /v1/fire` (contractul de mai jos),
iar acela decide canalele (Telegram, SMS, email, ntfy…), pauzele, orele de liniște, escaladarea
și confirmarea.

Se configurează în **Setări → Notificări → Serviciul de alerte**: adresa (`…/v1/fire`) și o cheie
API cu dreptul `fire`. Cu cheia goală, totul rămâne ca înainte (doar botul propriu). Butonul
**Testează serviciul de alerte** trimite o alertă de test (`test: true`, trece de pauze) și arată
ce a răspuns serviciul.

## Ce pleacă unde

| Tip (`type`) | Când | Episod (`idempotency_key`) |
|---|---|---|
| `nvr_camera_cazuta` → `nvr_camera_revenita` | camera nu răspunde ~2 min / revine; mementouri (fără episod) la 4, 8, 16, 32 min, 1–16 h, apoi zilnic | `openvigil:cam-<id>` |
| `nvr_nvr_cazut` → `nvr_nvr_revenit` | NVR-ul nu răspunde / revine | `openvigil:nvr` |
| `nvr_disc_plin` → `nvr_disc_ok` | spațiu liber sub 2× pragul de ștergere / din nou peste | `openvigil:disc` |
| `nvr_inregistrare_oprita` → `nvr_inregistrare_reluata` | în directorul de înregistrări nu se poate scrie | `openvigil:rec-blocat` |
| `nvr_inregistrare_problema` | problemă de continuitate nouă (o dată); „nu mai scrie” se închide cu „scrie din nou” | `openvigil:mut-<id>` |
| `nvr_inregistrare_bucla` | înregistrarea unei camere pică de 5 ori sub 60 s | — |
| `nvr_spatiu_insuficient` | s-au șters segmente înainte de retenție | — |
| `nvr_curatare_esuata` | curățarea orară a eșuat | — |
| `nvr_backup_esuat` | copia de siguranță a eșuat / n-a plecat în afară | — |
| `nvr_fluxuri_lipsa`, `nvr_flux_regenerat` | camere fără flux în go2rtc | — |
| `nvr_zgomot` | alarma de zgomot | — |
| `nvr_regula` | acțiunea „notificare” a unei reguli | — |
| `nvr_rezumat` | rezumatul zilei, proba săptămânală, testul din Setări | — |
| `nvr_intrus` | intrus confirmat (om) sau persoană necunoscută, cu modul **plecat**/**noapte** | — |
| `nvr_intrus_acasa` | același, critic (noaptea), cu modul **acasă** | — |
| `nvr_cadere` | posibilă cădere a unei persoane (setarea `alert_service_type`) | — |
| `nvr_container_cazut` | paznicul gazdei: un container a căzut sau stă blocat | — |

## Contractul

`POST <adresa>` cu antetul `X-API-Key: <cheia>` și corpul JSON:

```json
{"type": "nvr_camera_cazuta", "source": "openvigil",
 "context": {"text": "⚠️ Camera OFFLINE: Poarta (10.0.0.3)", "link": "", "cam": "Poarta", "ip": "10.0.0.3"},
 "idempotency_key": "openvigil:cam-3", "resolved_type": "nvr_camera_revenita"}
```

La revenire, același `idempotency_key` cu `"status": "resolved"`. OpenVigil citește din răspuns
`status` (`sent`, `partial`, `pending`, `failed`, `suppressed`; când nicio notificare nu descrie
apelul, chiar valoarea episodului), `suppressed_reason` și `episode` (`opened`, `repeat`,
`resolved`, `not_found`); `test: true` vine doar de la butonul de test. Un răspuns care nu e un
obiect JSON sau are altă stare e tratat ca eșec: alerta pleacă și pe botul propriu.

## Contextul

Contextul fiecărei alerte are `text` (mesajul gata compus, în limba instalației) și `link`
(gol sau legătura spre redare), plus după caz `cam`, `ip`, `liber`, `ora`, `serviciu`. Un șablon
minim: titlu după gust, mesaj `{text}{link}`.

Detecțiile cu poză (YOLO, Face ID, numere, „Învață scena”) rămân pe botul propriu, cu butoanele
de verdict; serviciul nu primește imagini. Intrușii primesc o **copie** (istoric, confirmare,
SMS cu casa goală), fără rezervă pe bot — poza a plecat deja acolo.

## Camera căzută: mementouri

Prima alertă pleacă la cădere (după două verificări eșuate la rând, ~2 minute), apoi câte un
memento când camera e căzută de 4, 8, 16, 32 de minute, 1, 2, 4, 8, 16 ore, 1 zi și apoi o dată
pe zi, până revine. Mementourile sunt fire fără `idempotency_key` (un episod deschis le-ar trata
ca `repeat`). La revenire episodul se închide, cu durata căderii în text. Programul ține peste
reporniri (`data/camere_cazute.json`); o cameră deja căzută la pornire e anunțată cu durata din
jurnal, iar una revenită cât serverul era oprit primește mesajul de revenire.

## Rezerva

Dacă serviciul nu răspunde, dă eroare HTTP, nu cunoaște tipul (`unknown_type`) sau toate
canalele lui au eșuat (`failed`), mesajul pleacă pe botul propriu, ca înainte. O suprimare voită
(`throttled`, ore de liniște, amânare) nu cade pe rezervă: e decizia serviciului. `pending` e
livrare în curs, nu eșec.

## Șabloanele în serviciu

Serviciul trebuie să cunoască tipurile de mai sus (altfel răspunde `unknown_type` și alerta pleacă
pe rezervă). Un exemplu de șabloane și rute, în formatul de import YAML al unui serviciu de tip
alertd (canalele se numesc după instalație):

```yaml
version: '1.0'
templates:
- type: nvr_camera_cazuta
  severity: warning
  category: Cameras
  title_template: 'Cameră căzută — {cam}'
  message_template: '{text}{link}'
  enabled: true
- type: nvr_camera_revenita
  severity: info
  category: Cameras
  title_template: 'Cameră revenită — {cam}'
  message_template: '{text}{link}'
  enabled: true
routes:
- template: nvr_camera_cazuta
  channel: telegram
  severity_min: warning
  enabled: true
- template: nvr_camera_revenita
  channel: telegram
  severity_min: info
  enabled: true
```

Restul tipurilor se adaugă la fel. Ce canal primește SMS e alegerea fiecărei instalații (în mod
obișnuit: `nvr_inregistrare_oprita`, `nvr_intrus`, `nvr_container_cazut`, `nvr_cadere`).
