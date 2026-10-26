# Automatizari cu Node-RED

Sistemul nu incearca sa fie el motor de reguli. Da afara evenimentele intr-o forma stabila
si primeste comenzi inapoi; ce se intampla intre ele decizi in Node-RED.

## Ce pleaca de la noi

Fiecare eveniment ajunge ca `POST` JSON la adresele din **Setari → Detectie →
Automatizari**. Livrarea se face pe un fir separat, cu trei incercari (pauze de 1 si 2
secunde). Un Node-RED oprit nu incetineste niciodata detectia sau inregistrarea.

| Eveniment | Cand se trimite |
|---|---|
| `detectie` | inteligenta artificiala a gasit un obiect (om, caine, pisica, pasare...) |
| `alarma_camera` | camera a raportat ceva (miscare, linie trecuta, intrare in zona) |
| `zgomot` | s-a depasit pragul de zgomot |
| `camera_offline` / `camera_online` | o camera a picat sau a revenit |
| `gol_inregistrare` | s-a pierdut inregistrare, sau retentia nu mai incape pe disc |
| `sirena` | s-a declansat sirena (automat sau din API) |

Forma mesajului:

```json
{
  "id": "8cf1086df9234860",
  "eveniment": "detectie",
  "moment": "2026-08-04T10:33:46+0300",
  "sursa": "openvigil",
  "camera": { "id": 4, "nume": "Curte", "grup": "Intrari", "ip": "192.168.1.105" },
  "eticheta": "person",
  "eticheta_ro": "om"
}
```

Numele evenimentelor si cheile raman stabile: adaugam campuri, nu le redenumim. Fiecare
livrare are `id` unic — daca reincercam dupa o eroare de retea, poti ignora duplicatul.

### Verificarea semnaturii

Daca ai pus un secret, fiecare cerere poarta antetul `X-Semnatura: sha256=<hex>`, calculat
cu HMAC-SHA256 peste corpul brut. In Node-RED, intr-un nod `function` pus imediat dupa
`http in` (cu **Body: a parsed JSON object** dezactivat, ca sa ai corpul brut):

```javascript
const crypto = global.get('crypto') || require('crypto');
const astept = 'sha256=' + crypto.createHmac('sha256', 'SECRETUL_TAU')
                                 .update(msg.req.rawBody).digest('hex');
if (msg.req.headers['x-semnatura'] !== astept) return null;  // nu e de la noi
msg.payload = JSON.parse(msg.req.rawBody);
return msg;
```

## Ce putem primi

Comenzile merg pe `/api/v1/...` cu jetonul din aceeasi pagina, trimis ca antet `X-Token`.
Jetonul e separat de parola interfetei si il poti schimba oricand — fluxurile care il
foloseau pe cel vechi se opresc, ceea ce e exact ce vrei daca a scapat pe undeva.

| Comanda | Corp | Ce face |
|---|---|---|
| `POST /api/v1/camera/<id>/sirena` | `{"sunet":1,"volum":100,"repetari":1}` | porneste sirena |
| `POST /api/v1/camera/<id>/strobo` | `{"secunde":10}` | aprinde stroboscopul |
| `POST /api/v1/camera/<id>/releu` | `{"pornit":true,"port":1}` | comuta iesirea de releu |
| `POST /api/v1/camera/<id>/anunt` | `{"text":"Buna ziua","repetari":1}` | rosteste textul prin difuzor |
| `GET /api/v1/camere` | — | lista camerelor si ce poate fiecare |
| `GET /api/v1/stare` | — | cine inregistreaza, detectii active, spatiu, zgomot |
| `GET /api/v1/camera/<id>/poza` | — | cadrul curent, ca JPEG |

Fiecare camera raspunde doar la ce stie sa faca: o comanda de sirena catre o camera fara
sirena intoarce `400` cu motivul, nu esueaza in tacere. Ia `poate` din `/api/v1/camere` si
stii dinainte ce ai voie sa ceri.

## Trei fluxuri de pornire

Importa `flux-exemplu.json` din acest director (Node-RED → meniu → Import), apoi
inlocuieste `JETONUL_TAU` si adresa serverului. Contine:

1. **Salut la poarta** — la `detectie` cu `eticheta: person` pe camera de la intrare,
   trimite un anunt vorbit. Are o pauza de 60 de secunde per camera, altfel cine sta la
   poarta e salutat la fiecare cadru.
2. **Lumina la miscare in spate** — la `alarma_camera` pe camerele din grupul „Spate",
   comanda releul. Filtrarea se face pe `camera.grup`, nu pe id-uri: adaugi o camera in
   grup si intra automat in flux.
3. **Anunt cand apare un animal** — la `detectie` cu eticheta de animal, ia poza prin
   `/poza` si o trimite mai departe.

## De ce nu MQTT (deocamdata)

Node-RED consuma HTTP nativ, deci webhook-urile nu cer niciun serviciu in plus. Un broker
MQTT ar aduce reconectare automata si mesaje retinute — utile daca fluxurile ajung sa fie
multe sau daca vrei integrare directa cu Home Assistant. Se poate adauga peste ce exista
acum, fara sa schimbam forma mesajelor.
