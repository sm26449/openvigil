# Interfata OpenVigil — un singur sistem pentru toate camerele

> **Document istoric.** Scris pe parcurs, in timp ce sistemul a fost construit pentru instalatia
> de origine (18 camere Hikvision, un NVR Hikvision, servicii systemd, criptare fscrypt pe
> gazda). Varianta publica ruleaza in Docker: vezi [README](../../README.ro.md) si
> [docs/operare.md](../operare.md). Sectiunile „Servicii (systemd)”, „criptare” si referintele
> la NVR-ul Hikvision, la Traefik sau la cai de pe gazda descriu etape vechi si nu se aplica
> instalarii din Docker; sunt pastrate ca jurnal al deciziilor.

**Serverul e recorderul principal**: toate cele 18 camere sunt inregistrate local, iar
live-ul si inregistrarea se trag **direct de pe camere**, prin go2rtc (`camN` = substream,
`camN_main` = mainstream brut; parolele camerelor stau doar in data/go2rtc.yaml, 0600, nu
in linia de comanda a vreunui ffmpeg). Daca go2rtc nu raspunde, nvr-record cade pe camera
directa. **NVR-ul Hikvision e scos din functiune** (Setari -> Sistem -> „NVR retras”, din
2026-10-25): nu mai e supravegheat si nimic nu mai incearca sa-l contacteze; comutatorul e
reversibil, caz in care playback-ul cade pe el pentru zilele dinainte de migrare (2026-08-03).
Ritm de scriere masurat: ~303 GB/zi toate 18 (mainstream copiat,
fara transcodare) -> retentie setata pe doua niveluri din Setari (acum 7 zile
normale / 7 zile cu evenimente) cu garda de spatiu automata.

## Acces
- Interfata web: **http://<ip-server>:8079** (LAN) sau **https://nvr.exemplu.ro** prin
  Traefik-ul stack-ului fotovoltaic (certificat Let's Encrypt, nume rezolvat la IP-ul
  privat — nimic expus public). Vezi „Acces din afara casei" mai jos.
- Tab **Live** = grila celor 18 camere (substream H.264, usor)
- Tab **Inregistrari** = alegi camera + data, apesi *Cauta*, vezi timeline-ul cu
  segmentele inregistrate (motion) si dai click oriunde ca sa redai de la acel moment.
- Buton **➕ Adauga camera** = camere Hikvision accesate **direct pe IP** (nu prin NVR).
  Completezi IP/port/user/parola; backend-ul testeaza conexiunea, citeste modelul si daca
  are stocare (playback). Camerele standalone apar in grila cu badge (IP) + buton de stergere.
  Persistate in `data/cameras.json` (chmod 600, contine parole), id-uri de la 101 in sus.
  Live = substream H.264 102 (copiat); HD = mainstream transcodat la H.264, ca la NVR.
  Playback doar daca au SD/HDD propriu (cele fara stocare apar "fara inregistrari").

## Actiuni camere standalone (microfon, difuzor, sirena, stroboscop)
La adaugare (si o singura data pentru cele existente), backend-ul detecteaza prin ISAPI ce
stie camera (`features` in `data/cameras.json`) si o pregateste automat: porneste audio pe
streamuri (AAC unde se poate) si linkage-ul stroboscopului pe IO-1. Butoane pe tile
(camerele standalone) si in bara HD:
- **🔈 asculta** microfonul camerei (unmute; AAC vine prin MSE, G.711 prin WebRTC)
- **🎤 vorbeste** (tine apasat) — merge prin go2rtc backchannel `isapi://` in difuzorul camerei
- **📢 sirena** — reda sunetul de alarma o data (in HD alegi sunetul + volumul; atentie:
  setarile raman si pentru alarma declansata de evenimente)
- **💡 stroboscop** on/off — nu exista endpoint de test in firmware; folosim trucul standard:
  linkage whiteLight pe IO-1 + comutarea intrarii de alarma (nefolosita) NO<->NC prin ISAPI.
  Siguranta: backend-ul il stinge singur dupa 60s.
- **⚡ releu** (doar in HD) — iesirea de alarma a camerei
- **badge rosu pe tile** la evenimente active (miscare, intruziune, linie trecuta etc.) —
  un thread per camera tine deschis `alertStream` si frontend-ul face polling la `/api/events`.

API: `POST /api/cam/<id>/siren|strobe|output|volume`, `GET /api/events`.
Camerele de pe NVR (1..16) nu au aceste actiuni (NVR-ul nu le expune direct).

## Detectie animale/persoane (YOLO)
Buton **🐾** pe orice tile (si in bara HD): porneste/opreste detectia pe camera respectiva.
Un worker per camera ia un cadru din go2rtc (`/api/frame.jpeg`, substream 640px) la ~1.5s si
ruleaza modelul din slotul activ `data/yolo11n.pt` — **yolo11m** (COCO) din 2026-08-15,
ales dupa masuratoare pe 240 de cadre proprii (a eliminat fantoma "person" de pe stalpul
portii vazuta 3 nopti la rand de yolo11n, plus ursii inexistenti; 19ms/cadru pe GPU,
revert intr-un click din Setari → Unelte). Detectiile apar
ca badge verde pe tile: om, caine, pisica, pasare, cal, oaie, vaca, urs, plus
vehicule (masina, motocicleta, autobuz, camion) pentru LPR.
**Gainile si porumbeii apar amandoi ca "pasare"** — modelul standard nu distinge specii;
pentru asta ar trebui un model fine-tuned (posibil pas viitor).
Stare persistata in `data/detect.json`; model in `data/yolo11n.pt`;
API: `POST /api/cam/<id>/detect {on, auto_siren}`, etichete live in `GET /api/events`.

### Sirena automata la caine (🚨)
Buton **🚨** pe camerele cu sirena: cand e pornit **si** detectia 🐾 e activa, un caine
detectat declanseaza sirena (3 repetari, cooldown 60s). Etichetele care declanseaza:
`AUTO_SIREN_LABELS` in `app/detect.py` (implicit doar `dog`).

## Tabul ⚙ Setari (totul din UI, fara terminal)
- **Notificari Telegram + Email (SMTP)**: completezi datele, buton "Trimite test" pe
  fiecare canal. Persistate in `data/settings.json` (chmod 600); `config.py` ramane doar
  fallback. Mesaj cu poza la detectiile alese + text cand o camera standalone cade/revine.
- **Ce detectii notifica** (implicit caine/pisica/om/urs — NU pasare, spam la gaini) si
  **ce detectii declanseaza sirena automata** (implicit caine) — checkbox-uri, fara restart.
- **Model / antrenament**: vezi cate cadre curate s-au strans si cate sunt in datasetul
  etichetat; "📥 Aplica ultimul model antrenat" — gaseste cel mai nou
  `best.pt` din `data/finetune/runs`, face backup la `data/yolo11n.pt.bak`, inlocuieste si face **hot-reload**
  (detectia foloseste noul model de la urmatorul cadru, fara restart). Butonul
  "↩ Revino la modelul anterior" (apare doar daca exista backup) **comuta** modelul
  activ cu backup-ul — apasat din nou, revii la cel nou; nimic nu se pierde.

## Recatalogare detectii (tabul Alarme)
In lightbox, sub poza: bara "Corecteaza" — click pe eticheta corecta (redenumeste
snapshot-ul, actualizeaza galeria si pill-urile) sau "🗑 Fals / sterge". Util si pentru
triajul pozelor inainte de antrenament.

## Inregistrare locala (camerele standalone, fara SD)
`app/record.py`: cate un ffmpeg per camera standalone — mainstream **copiat** (fara
transcodare, audio AAC) in segmente MP4 fragmentate de 5 min, in
`data/recordings/cam<id>/YYYY-MM-DD_HH-MM-SS.mp4` (ora locala). Retentie:
pe doua niveluri, configurata din Setari (`retention_days` / `retention_event_days`,
cu override per camera) + garda de spatiu (`retention_min_free_gb`, sterge cele mai vechi).
Playback-ul lor apare normal in tabul Inregistrari (segmente "continuous" pe timeline);
redarea face concat cu `inpoint` prin template-ul go2rtc `vodcat` + transcodare la nevoie.

## Export MP4
In tabul Inregistrari: interval **Export** (de la / pana la, max 30 min) + buton **⬇ MP4**.
Exportul (ca si fisa probatorie, clipurile, chenarele, ZIP-ul de dosar si adaugarea unei
camere) ruleaza in **coada de joburi** (`app/jobs.py`, 3 lucratori, tabelul `jobs` din
`data/events.db`): ruta raspunde 202 + job, interfata urmareste `/api/jobs/<id>` in tava
din dreapta-jos (progres din ffmpeg, anulare) si descarca fisierul din
`/api/jobs/<id>/fisier` (pastrat o ora). Firele web nu mai stau blocate dupa ffmpeg.
Click pe timeline pre-completeaza intervalul (+2 min). Local = taiere instant (`-c copy`);
NVR = pull RTSP in timp real (dureaza ~cat clipul; HEVC, tag hvc1). `GET /api/export`.

## Evenimente pe timeline
Punctele de pe bara de playback = detectiile (verde) si alarmele (rosu) din ziua cautata
(din snapshot-uri); click pe punct = redare de la acel moment.

**Marcaje (bookmarks):** butonul *Marcaj* (in Inregistrari, in timpul redarii, sau in
overlay-ul HD) pune un steag auriu cu eticheta ta la momentul redat; marcajele raman pe
timeline (ambele bare), au lista proprie in dreapta (click = redare, *dosar* = proba de
±15 s intr-un dosar de caz, ✕ = stergere) si stau in `data/events.db` (tabelul `marcaje`,
`app/marcaje.py`, API `/api/marcaje`). Operatorul le pune doar pe camerele lui.

### Tabul "Alarme" (snapshot-uri + clipuri)
La fiecare detectie YOLO (cu chenarele desenate) si la fiecare alarma de camera (miscare,
intruziune etc.) se salveaza o poza in `data/snapshots/YYYY-MM-DD/` (cooldown 60s per
camera+tip, stergere automata dupa `retention_snapshots_days`, implicit 90 zile).
Tabul **Alarme** din UI le arata pe zile,
cu filtru pe camera si **filtre pill pe tip** (cu contor); click pe poza = viewer integrat
(lightbox) cu navigare stanga/dreapta (sageti/swipe/Escape). Butonul **▶ Clip** din viewer
reda 20s de video in jurul evenimentului (`/api/clip`, -5s..+15s): instant din inregistrarile
locale (H.264 copiat / HEVC transcodat), mai lent la camerele NVR (pull RTSP + transcodare).
Clipurile se tin in cache in `data/tmp/`. Orele sunt ora Romaniei
(`Environment=TZ=Europe/Bucharest` in nvr-web.service).

## Identificare utila in practica: LPR cu alerta la masini straine, fete grupate pe persoana
(2026-10-23 seara; pornit de la constatarea ca Face ID avea 29 de recunoasteri in doua luni,
LPR 39 de citiri si zero numere cu nume — piesele existau, dar nu produceau.)
- **LPR, citiri confirmate**: `lpr_min_conf` e pragul de jos (acum 0,80), `lpr_sure_conf`
  pragul de sus (0,90). Peste cel de sus, o citire ajunge; intre praguri, numarul e acceptat
  doar daca a fost citit de doua ori pe aceeasi camera in 30 s (`plates.confirmata`). Asa
  pragul coborat prinde mai multe placute reale fara OCR-ul aiurea de pe o singura trecere.
- **Alerta la masini necunoscute** (`lpr_notify_unknown`, doar pe `lpr_alert_cams`; gol =
  toate): orice numar FARA nume -> Telegram „🚗⚠️ masina necunoscuta, numar X”, cu poza.
  Configurat pe camerele de poarta (13, 14), unde se si citeste. Masinile familiei se
  inroleaza din tabul AI -> LPR („Adauga” sau „da-i un nume” pe o citire) si tac.
- **Statistici** in acelasi ecran: citiri azi / 7 zile / fara nume (`plates.stats`), ca sa
  se vada daca pragul si camerele dau ceva.
- **Fete grupate pe persoana** (`faces.unknown_groups`, buton „✨ Grupeaza pe persoana”):
  fetele necunoscute capturate sunt grupate pe similaritate (acelasi prag ca recunoasterea),
  fiecare grup arata cateva mostre distribuite in timp, cate poze / in cate zile, si — daca
  seamana cu cineva din biblioteca — sugestia cu procent. Confirmi cu un click: „e X”,
  o persoana existenta din lista, un nume nou, sau „ignora”. Se inroleaza cel mult 8 poze
  (distribuite in timp), tot grupul iese din necunoscute. Nimic nu se inroleaza singur:
  inrolarea automata („Auto-detecteaza rezidenti”) a dublat poze si a amestecat persoane in
  septembrie, de aceea butonul ei a disparut (API-ul `/api/faces/auto-residents` ramane).
  Embedding-urile crop-urilor se tin in cache in nvr-ai (pe mtime), deci gruparea se poate
  cere oricand.
- **Necunoscutii ca persoane** (treapta 2, 2026-10-24): gruparea nu se mai cere, se face
  singura in nvr-ai la doua minute dupa orice fata noua (`faces.programeaza_regrupare`) si
  se scrie in `data/faces/grupuri.json` cu **id-uri stabile** intre regrupari
  (`potriveste_ids`: un grup nou ia id-ul grupului vechi cu centroidul cel mai apropiat,
  cosinus >= 0,60). In tabul AI -> Persoane apar ca „Necunoscut N": poze, de cate ori si in
  cate zile, ultima aparitie (zi, ora, camera), sugestia din biblioteca; ii dai nume, il
  unesti cu o persoana existenta sau il ignori. Pe cardurile persoanelor cunoscute apare
  „pare aceeasi cu X — uneste" cand doua persoane din biblioteca seamana peste 0,50
  (`perechi_persoane`; `POST /api/faces/person/<b>/uneste {in: a}` muta pozele si reface
  embedding-urile). Butonul „Alerta la fata necunoscuta cand sunt plecat" creeaza regula
  (declansator `persoana_necunoscuta`, modurile plecat + noapte, notificare, cooldown 2 min).
- **O singura cautare** (`app/static/js/48-cauta.js`, campul din antet sau tasta `/`): textul
  merge in paralel la cautarea semantica in poze (`/api/search`, cu perioada 7-90 de zile), la
  istoricul LPR (`/api/plates?q=`), la istoricul persoanei (`/api/faces/history?name=`) si la
  jurnalul de evenimente (ultimele 400, filtrate pe cuvinte, fara diacritice). Rezultatele vin
  intr-o singura grila, cele mai noi primele, cu chips care arata numarul pe sursa (Toate /
  Poze / Numere / Persoane / Jurnal); click pe un rezultat deschide fisa evenimentului.
- **Sanatatea camerei in lista** (tabul Camere): sub starea fiecarei camere, un rand mic
  din `/api/continuity?days=1`: „scrie, ultimul segment acum 12 s", reporniri, „2 goluri azi
  (17 min)" (galben). Un singur apel pentru toata lista, dupa ce randurile exista.
- **Reguli „smart"** (treapta 3): motorul de reguli are declansatoare noi — `trecere`
  (linie de numarare trecuta; valoarea: `poarta`, `poarta:intra`, `iese`, optional
  `@person` pentru eticheta), `intrare_zona` (numele zonei, optional `@eticheta`), `intrus`,
  `obiect_nou`, `obiect_disparut`, `parcare_inceput` (eticheta sau gol). Liniile si zonele se
  deseneaza ca inainte (Live → butonul de linii/zone al camerei; nvr-ai le urmareste cu
  directie), iar fiecare linie/zona din editor are „⚙ regula" care deschide AI → Reguli
  precompletat (`regulaDin` in 70-ai.js, folosit si de fisa evenimentului); formularul
  sugereaza liniile si zonele desenate, cu sens. Asta e echivalentul „Smart Event"
  (tripwire cu directie, intruziune) de la Hikvision/Dahua, dar legat de modurile casei.
- **„Gaseste-l pe toate camerele"** (AcuPick-ul nostru; `app/reid_arhiva.py`): fiecare
  esantion de corp pe care re-ID-ul il calculeaza oricum (la ~3 s, pe oamenii urmariti de pe
  camerele urmarite) se pastreaza pe disc, pe zile: embedding OSNet (512 x float16) + un crop
  de 128 px, in `data/reid/<zi>.jsonl` si `data/reid/<zi>/`. In fisa unui eveniment cu poza,
  „🔎 Gaseste-l pe toate camerele" taie cel mai mare om din poza (sau box-ul dat), il
  embed-uieste in nvr-ai (`POST /reid/cauta`) si intoarce aparitiile din ultimele 1 / 7 / 30
  de zile pe toate camerele vizibile contului, cu similaritatea (prag 0,50; pe aceeasi camera,
  intr-un minut, doar cea mai buna); click pe o aparitie deschide fisa/redarea, „cauta dupa
  acesta" continua lantul de la acea aparitie. Limita metodei: aceleasi haine — a doua zi, cu
  alta geaca, nu se mai regaseste. Retentia arhivei = cea a pozelor (curatare zilnica din
  `snaps.prune`). Doar camerele din `track_cams` alimenteaza arhiva.
- **Sabotaj pe camera** (obiectiv acoperit `shelteralarm`, `tamperdetection`, scena
  schimbata, defocalizare): evenimentele vin deja de pe camere (ISAPI); acum au categoria
  „Sabotaj" in flux (chip propriu), severitate „atentie" ziua (incident de triat) si „info"
  noaptea — cam3 si cam4 dau ~30 de „obiectiv acoperit" pe noapte din IR/insecte
  (2026-10-24), deci triajul nocturn ar fi fost zgomot; `/api/sabotaj` numara alarmele de
  sabotaj pe camera azi, iar lista de camere le arata (cu nota „probabil IR/insecte" peste 10);
  in reguli, `alarma_camera` sugereaza si `shelteralarm` / `tamperdetection` (cu cooldown).
  De reglat pe camerele cu zgomot: sensibilitatea „video tampering" din interfata camerei.
- **Biciclisti fara model nou**: pentru fiecare om urmarit (`obiect`), nvr-ai calculeaza
  viteza in inaltimi de corp pe secunda (`ai_analytics.inaltimi_pe_s`: px/s / inaltimea
  cutiei; omul e propria rigla, fara calibrare in metri). Peste 2,2 inaltimi/s cel putin o
  secunda (mersul ~0,8, alergarea ~1,8, bicicleta 2,4-4) evenimentul primeste `biciclist`
  (jurnal: „🚲 biciclist (3,1 inaltimi/s)"), iar regulile pe `obiect` accepta valoarea
  `biciclist`. Doar pe camerele urmarite (`track_cams`).
- **Revizia de cod a lotului** (2026-10-25, 21 de constatari, toate aplicate): golurile
  din lista de camere citeau cheia gresita (`goluri_azi`); listarile „cele mai noi primele" din
  Adnotare si din coada de nesigure sortau dupa id-ul camerei, nu dupa data; clientul nvr-ai
  intoarce acum si raspunsurile 4xx cu motivul lor (inainte „nvr-ai nu raspunde" pentru
  „ruleaza deja un job" sau „niciun om in poza"); unirea persoanelor nu mai cade pe o a doua
  rulare locala cand nvr-ai a picat la mijloc; jobul de antrenament nu mai ramane „activ"
  daca procesul nu porneste si oprirea nu mai apare ca esec; coada de nesigure are lacat intre
  procese (nvr-ai scrie, nvr-web scoate); programarea regruparii nu mai asteapta regruparea;
  regulile pe `obiect_nou`/`obiect_disparut` nu mai au valoare (evenimentele n-au eticheta);
  viewerii si operatorii pot folosi fluxul, cautarea, jurnalul, citirile LPR, istoricul
  persoanei si „gaseste-l pe toate camerele" (acum GET), toate filtrate pe camerele lor;
  „Regula din asta" asteapta formularul sa se incarce; reconstruirea datasetului inlocuieste
  split-urile (fara scurgere train/val) si pastreaza mereu cadrele verificate in esantion;
  arhiva re-ID adauga esantionul in cache in loc sa-l arunce; fluxul tine listarea zilei in
  cache pe mtime-ul directorului.
- **Rezumatul zilei pe Telegram** (2026-10-25): Setari -> Notificari -> „Rezumatul zilei":
  pornit/oprit si ora (implicit 21:00; `digest_telegram`, `digest_ora`); un fir din nvr-web
  (`digest.start`, ca la copia nocturna: la pornire nu trimite „pentru ca a trecut ora")
  trimite o data pe zi, pe canalele configurate, textul din `digest.text`: evenimente si
  incidente, interval de activitate si ora de varf, camerele cele mai active, cine a fost
  vazut (Face ID), masini necunoscute, goluri de inregistrare si camere care nu scriu, sabotaj
  raportat, titlurile zilei (criticele). Fiecare sursa e independenta: o eroare intr-una nu
  taie mesajul. „Trimite rezumatul acum" il trimite pe loc; `GET /api/digest/text?day=` il
  arata fara sa-l trimita.
- **Canalele de notificare, la vedere** (Setari -> Stare, randul „Notificari"; `/api/system`
  -> `notificari`): Telegram / email / push cu numarul de dispozitive; rosu cand nu exista
  NICIUN canal (atunci toate alertele raman doar in jurnal — asa a fost pana pe 2026-10-25,
  fara ca nimic sa semnaleze). Cand rezumatul zilnic e oprit, lunea la `digest_ora` pleaca o
  proba „canalul functioneaza", ca drumul sa fie verificat saptamanal.
- **Verificarea publica a probelor** (`/verifica`, fara login; `app/verificare.py`): urci un
  MP4 exportat (pana la 2 GB; amprenta se calculeaza pe flux, fisierul nu se pastreaza) sau
  lipesti SHA-256; raspunsul spune daca e un export produs de acest NVR (cand, de pe ce
  camera, ce interval, cine l-a cerut) sau un segment brut din lantul de custodie, si ca nu a
  fost modificat de atunci. Registrul exporturilor e `data/exporturi.jsonl` (scris la fiecare
  export, din 2026-10-25); segmentele se cauta dupa hash in `segmente.db` (`lant.cauta_hash`).
  Fiecare verificare publica intra in audit (doar primele 16 caractere ale amprentei).

## Fluxul de evenimente langa Live (treapta 1 din analiza de interfata, 2026-10-24)
In dreapta grilei Live (butonul „Flux" in bara; implicit pornit pe ecrane late, oprit pe
telefon; alegerea ramane in browser) stau ultimele cadre salvate azi — detectii, fete, numere,
alarme — cu miniatura, ce/unde/cand, si chips de filtru (Oameni, Masini, Animale, Fete,
Numere, Alarme). Click pe un cadru = redarea momentului (`#pb/cam/zi/sec`). Se
reimprospateaza la 10 s doar cat grila e vizibila si doar incremental (`/api/flux?dupa=`);
cadrele noi pulseaza o clipa. Miniaturile vin din `/api/flux/thumb/<zi>/<fn>` (200 px, cache
in memorie, aceleasi reguli de acces ca `/snapshots`); 40 de poze intregi ar fi fost ~4 MB.
Logica in `app/static/js/15-flux.js`, categoria in `routes_analytics._flux_categorie`.

**Fisa evenimentului** (`app/static/js/45-fisa.js`, dialog nativ `#fisa`): un singur loc pentru
orice eveniment, deschis din flux (click pe cadru), din jurnal si din incidente (butonul
„fisa") — poza, ce / unde / cand / cine, severitatea, si actiunile: „Reda momentul"
(`#pb/cam/zi/sec`), Clip, Export 30 s (MP4 cu filigran), La dosar, Confirmat / Fals (cand
evenimentul e incident), „Regula din asta" (deschide AI -> Reguli precompletat cu tipul,
valoarea si camera). Orice element cu `data-fisa='{json}'` o deschide (delegare pe document).

**Setari mai usor de parcurs** (aceeasi zi): camp de cautare in meniul Setarilor (arata, din
toate sectiunile, doar cardurile al caror titlu sau text contine cuvintele, fara diacritice)
si bifa „avansate" (implicit oprita, tinuta in browser) care ascunde cardurile rar atinse:
MQTT, ora camerelor, fluiditatea grilei, automatizarile si jetonul, detectia pe regiuni,
legaturile rapide (`data-avansat` pe card). Butonul „Stare" din antet e scurtatura catre
Setari -> Stare. Tabul AI: fiecare sectiune are intrebarea sub nume („cine intra?", „ce
masini vin?") si o fraza de stare din date (persoane, recunoasteri in 30 de zile, citiri
LPR, reguli active, cadre etichetate), ca sa vezi dintr-o privire ce produce fiecare.

## Recunoastere persoane (Face ID)
Cand detectia YOLO confirma un om, `app/faces.py` cere camerei un cadru la rezolutie
mare (ISAPI picture — pe substreamul de 640px fetele sunt inutilizabile) si cauta fete
cu InsightFace buffalo_l (SCRFD + ArcFace, ONNX local, pe GPU). Similaritate cosinus
fata de biblioteca `data/faces/persons/<nume>/`: peste prag (implicit 0,45) = persoana
recunoscuta — snapshot cu numele ca eticheta (apare ca pill in Alarme si badge live),
notificare cu nume, webhook `persoana`. Sub prag = crop-ul se strange in Setari →
Detectie ("Fete nerecunoscute"), unde etichetarea lui E inrolarea; o persoana devine
sigura de la 3-5 poze din unghiuri diferite. Totul se administreaza din cardul
"Recunoastere persoane": pornire, prag, notificari, persoane, upload de poze.
**Anti-spoofing** (pornit implicit): fiecare fata trece prin ansamblul MiniFASNet
V1SE+V2 (uniface, ONNX pe GPU) inainte de potrivire — o poza sau un ecran aratat
camerei e respins (nu se potriveste, nu se inroleaza) si emite evenimentul
`fata_falsa`, care poate declansa reguli (tentativa in sine e un semnal de alarma).
Respingerea cere ca AMBELE modele sa spuna "fals" cu incredere peste prag
(`face_antispoof_conf`, implicit 0,7) — o fata reala vazuta prost nu pierde
recunoasterea. Inrolarea din poze incarcate nu trece prin verificare (e legitima
prin natura ei).
`data/faces/` (biometrie) nu intra in git; modelul (~280 MB) se descarca singur la
prima folosire.

## Numere de inmatriculare (LPR)
Acelasi tipar ca Face ID, pentru vehicule (masina/motocicleta/autobuz/camion — acestea
se confirma dintr-un singur cadru, ca sa nu scape masinile in mers): la aparitia unui
vehicul, cadrul HD trece prin `app/plates.py` (fast-alpr: detector YOLOv9 de placute +
OCR CCT, ONNX local). Citirile peste pragul de incredere (implicit 0,85) intra in
jurnalul din Setari → Detectie, cu crop-ul placutei; acelasi numar nu se jurnalizeaza
de doua ori in 120s. Placutele pot primi nume ("duba lui Ion") — masinile cunoscute
notifica cu numele lor, si orice citire pleaca pe webhook-ul `numar` (Node-RED poate
deschide poarta). `data/plates/` nu intra in git.

## Cautare semantica in alarme
Fiecare poza din Alarme primeste o "amprenta" de sens (SigLIP base multilingual,
`app/semantic.py`, local, pe GPU) intr-un index pe zile (`data/semantic/<zi>.npy` +
meta JSON, sters automat odata cu pozele de retentie). Campul de cautare din tabul
Alarme gaseste poze dupa descriere libera **in romana** — "masina alba", "om cu
rucsac", "caine langa poarta" — ordonate dupa potrivire; click = acelasi vizualizator,
cu clip si "vezi in context". Pozele noi se indexeaza pe masura ce apar (coada
asincrona, detectia nu asteapta); istoricul se indexeaza in fundal la activare sau cu
butonul din Setari → Detectie. Merge si pentru vieweri (rezultatele se filtreaza pe
camerele lor).

## Tabul AI: persoane, masini, reguli, evenimente
Tab dedicat cu patru sectiuni (sidebar, ca la Setari):
- **Persoane** — biblioteca Face ID, etichetarea fetelor necunoscute, si „istoric” per
  persoana: toate aparitiile ei recunoscute (`data/faces/log.jsonl`) — raspunsul la
  „cand a mai venit X pe la noi?".
- **Masini** — LPR: masini cunoscute si jurnalul citirilor, plus **numararea exacta**
  (ByteTrack pe camerele alese: fiecare obiect primeste un ID urmarit la ~4 cadre/s
  din substream; la iesirea din cadru devine evenimentul `obiect` cu eticheta, durata
  si directia de mers — "cate masini au trecut" se numara bucata cu bucata).
- **Reguli** — motorul de reguli (`app/rules.py`), pe modelul familiar din NVR-uri
  (Detectie → Unde → Program → Iesire): declansator (persoana anume/necunoscuta,
  numar cunoscut/necunoscut/anume, detectie, alarma camera pe subtip, zgomot, camera
  cazuta) + unde (zone = grupurile de camere, si/sau camere individuale, combinate cu
  SAU) + program (interval orar cu trecere peste miezul noptii, zilele saptamanii,
  cooldown) + iesire (sirena, strobo, anunt TTS, releu, notificare, webhook „regula",
  mesaj MQTT pe orice topic). Actiunile ruleaza in thread separat si se jurnalizeaza
  cu rezultatul fiecareia; buclele sunt imposibile (evenimentul „regula" nu
  declanseaza reguli). Buton „testeaza" = ruleaza actiunile acum.
- **Evenimente** — jurnalul permanent (`data/events.db`, SQLite in mod WAL, retentie
  pe zile din Setari → Retentie, implicit un an): TOATE
  evenimentele trec prin `hooks.emit()` si ajung aici, cu filtre pe tip/camera/zi,
  descarcare CSV si legatura „clip" catre inregistrarea momentului. Vederea implicita
  e pe **episoade cu durata**: evenimentele de acelasi fel, pe aceeasi camera, la sub
  5 minute distanta (`incident_gap_sec`) devin un rand cu inceput/sfarsit/durata/de
  cate ori, iar butonul „MP4" descarca exact intervalul respectiv din inregistrare
  (`/api/journal?grup=1`, export -5s/+10s in jurul episodului). Butonul „raport"
  emite **fisa probatorie** (`/api/export/report`): metadate camera/interval/moment +
  **SHA-256** al fisierului MP4 — ce se preda politiei alaturi de video ca sa i se
  poata dovedi integritatea (verificare: `sha256sum`).

## Puntea MQTT
`app/mqtt.py` (Setari → Sistem → Puntea MQTT; broker implicit 192.168.1.2): toate
evenimentele se publica JSON pe `nvr/evenimente/<tip>` si `nvr/camera/<id>/<tip>`,
iar `nvr/stare` tine online/offline (retained, cu Last Will — brokerul anunta singur
caderea serverului). Actiunea „MQTT" a unei reguli publica orice mesaj pe orice topic
— ex.: „om necunoscut, zona Curte, 23–06 → topic `lumini/curte`, mesaj `ON`" aprinde
lumina din Home Assistant/Zigbee2MQTT/Tasmota. Conexiunea reconecteaza singura; un
broker cazut nu opreste nimic (evenimentele raman in jurnalul local).

## Dataset si antrenament (fine-tuning YOLO, local)
La fiecare detectie se salveaza si cadrul **curat** (fara chenare) in `data/dataset/`
(max 1/min per camera, nu se sterge automat) — materia prima pentru un model pe scenele
noastre. Fluxul e local, fara unelte externe:
1. `python scripts/finetune_dataset.py` : teacher-ul yolo11x eticheteaza
   automat cadrele, SigLIP desparte pasarile pe specii (gaina / porumbel / pasare), split
   stratificat 85/15 pe camera in `data/finetune/{images,labels}/{train,val}` + `dataset.yaml`;
   cadrele fara detectii raman cu etichete goale (fundalul care invata modelul sa nu vada fantome).
2. Antrenare cu ultralytics (in venv, pe RTX 2000 Ada), rezultatul in
   `data/finetune/runs/<nume>/weights/best.pt` — modelul activ `curte-v1` (11 clase) a fost
   facut asa pe 2026-08-15: yolo11m, 30 de epoci, 7.179 de imagini.
3. Setari -> Unelte -> Model: „Aplica ultimul model antrenat" (tranzactie verificata in nvr-ai,
   backup, revenire cu un click).

Pasii 1-2 se pornesc din acelasi card (2026-10-24): „Construieste datasetul etichetat" (cu un
plafon de cadre, esantion aleator cu seed fix; 0 = toate, dureaza ore la 100.000 de cadre) si
„Antreneaza un model nou" (N epoci; numele `curte-v<N+1>` se da singur). Ruleaza ca
subprocese in nvr-ai (`app/antrenare.py`: unul odata, progres citit din stdout, jurnal, oprire
cu `/api/train/stop`), pentru ca au nevoie de GPU; cat ruleaza, detectia e mai lenta — pornesc
noaptea. `scripts/antreneaza.py` e reteta lui curte-v1 (yolo11m din `data/yolo11m_stock.pt`,
imgsz 640, batch 10, patience 8) si tipareste o linie pe epoca.

**Adnotare** (tabul AI -> Adnotare, 2026-10-24, lotul 1): etichetele datasetului cadru cu
cadru — lista (train/val, doar neverificate, filtru pe camera), cadrul cu chenarele
teacher-ului colorate pe clasa, legenda, navigare cu sageti, „Confirma" (Enter) scrie
etichetele si marcheaza cadrul in `data/finetune/verificate.json`; reconstruirea datasetului
pastreaza etichetele cadrelor verificate de om (`finetune_dataset.py`). Rute:
`/api/adnotare`, `/api/adnotare/cadru/<split>/<fn>` (GET/POST), `/api/adnotare/img/...`;
logica in `app/adnotare.py`. Lotul 2 (aceeasi zi): editare direct pe canvas — tragere pe gol
= chenar nou cu clasa curenta, click = selectie, tragere din interior = mutare, din colt =
redimensionare, Delete = stergere, 1-9/0 = clasa (a selectiei sau cea curenta; si din
legenda), Ctrl+Z = anulare (50 de pasi), Esc = deselectare, Enter = salvare; cu modificari
nesalvate, trecerea la alt cadru cere confirmare. Lotul 3: **coada cadrelor nesigure** —
„Cauta cadre nesigure" pune modelul ACTIV (in nvr-ai) sa treaca prin cele mai noi N cadre
brute din `data/dataset` care nu sunt inca in dataset; cele cu chenare de incredere
0,30-0,55 intra in `data/finetune/nesigure.json`, pre-etichetate (chenarele cu procent,
cele sub prag cu linie intrerupta), cele mai nesigure primele. Sursa „coada: cadre nesigure"
din editor: corectezi/confirmi -> cadrul intra in `images/train` ca verificat de om si iese din
coada; „Ignora" il scoate fara sa intre. Asa corectezi 50 de cadre care conteaza, nu 7.000.
(`app/nesigure.py`; rute `/api/adnotare/nesigure/scaneaza|stare`, `.../cadru/nesigure/<fn>/ignora`.)

## Arhitectura
```
Browser  ──:8079── backend Flask (cautare ISAPI + inregistrare streamuri)
         ──:8076── releu autentificat (app/stream_proxy.py, in nvr-web; verifica sesiunea)
                        └── 127.0.0.1:1984 go2rtc (streaming MSE/WebRTC; transcodare
                                HEVC->H.264 cu ffmpeg) ── RTSP/ISAPI ──> camere / NVR
```
- Inregistrarile pe NVR sunt **HEVC/H.265 6MP** -> se transcodeaza la **H.264 720p** pt. browser.
- Live foloseste substream-ul **H.264** direct (copiat, fara transcodare).
- go2rtc e chiar motorul de streaming din **Frigate** — daca vrei vreodata detectie AI,
  piesa centrala e deja aici (zero munca aruncata).

## Servicii (systemd, pornesc la boot)
```
systemctl status nvr-go2rtc      # motor streaming  (127.0.0.1:1984, 127.0.0.1:8554, :8555)
systemctl status nvr-web         # interfata web    (:8079) + releul spre go2rtc (:8076)
systemctl status nvr-record      # inregistrare continua camere standalone (separat,
                                 #   ca restarturile interfetei sa nu taie inregistrarea)
journalctl -u nvr-web -f         # log live backend
```
Toate au `ExecStopPost=scripts/notify-fail.sh` -> alerta Telegram la orice cadere
(crash, OOM, semnal; nu la stop/restart manual), cel mult una la 10 min per serviciu.
Unitatile din `deploy/systemd/` sunt copiile celor din `/etc/systemd/system/`.
Watchdog-ul din nvr-web anunta si: camera standalone cazuta/revenita, NVR cazut/revenit,
disc aproape plin. Proiectul e in **git** (fara parole: config.py, cameras.json,
settings.json, go2rtc.yaml sunt in .gitignore; vezi config.example.py).

## Navigare (tranzitie live <-> inregistrari)
**In overlay-ul HD** (click pe orice camera): sub player e bara de **time-shift** —
`⏪ 30s / 5min / 30min` sar instant in inregistrarea aceleiasi camere, `🔴 Live` te
readuce, iar **mini-timeline-ul zilei** (segmente verzi + puncte de evenimente + marcaj
"acum" rosu) permite click oriunde = redare din acel moment. `↗ Deschide in Inregistrari`
duce camera in tabul mare.
**In tabul Inregistrari** (Etapa 2 din audit, 2026-10-23): timeline-ul se incarca singur la
schimbarea camerei sau a zilei; **zoom** 24h / 6h / 1h / 15 min (butoane, `+`/`−`, rotita pe
bara; Shift+rotita deruleaza; fereastra urmareste cursorul); **pauza** (Space/K), **cadru cu
cadru** (`,` `.`, pas de 1/25 s in buffer), sarituri instant in buffer-ul MSE (J/L, sageti,
Shift+sageti ±1 min) fara flux nou, viteze **½x / 1x / 2x / 4x / 8x** (`[` `]`; 4x si 8x
doar cu GPU, 8x masurat 0.995x realtime pe HEVC 2688x1520), **Link** copiaza adresa momentului
(`#pb/<cam>/<zi>/<sec>`), tragerea pe bara ofera direct „⬇ MP4" si „🔖 marcaj" pe interval.
**Un singur ecran „Evenimente"** (in loc de Incidente + Alarme + AI → Evenimente): sub-panourile
*De tratat* (triajul de incidente, cu badge pe tab), *Jurnal* (istoricul permanent, episoade,
CSV, clip/MP4/raport) si *Poze* (snapshot-urile cu filtre, cautarea semantica si intrebarile);
adresele `#ev/inc`, `#ev/jurnal`, `#ev/poze` (`#inc` si `#al` raman ca aliasuri). Setari →
Jurnal ramane separat: e istoricul modificarilor de configurare, nu evenimente.
Adresa paginii spune mereu unde esti (`#live`, `#ev/...`, `#pb/...`, `#hd/<cam>`): un link
primit pe Telegram deschide direct momentul (cu `web_url` din Setari → Notificari,
notificarile primesc legatura), iar Inapoi pe telefon inchide overlay-ul HD.
`⏮ Eveniment` / `Eveniment ⏭` sar din eveniment in eveniment,
`⏪10s/1min` si `10s/1min⏩` fac sarituri fine, cursorul **avanseaza in timpul redarii**
(pozitia curenta e afisata), iar **lista laterala de evenimente** (cu miniaturi) se
evidentiaza sincron cu redarea; click pe o linie = salt acolo.
**Din tabul Alarme**: butonul `↗ Vezi in context` din viewerul de poze deschide playback-ul
exact la secunda evenimentului.
**📸 Screenshot**: buton in bara HD si in playback — salveaza cadrul curent ca JPG
(rezolutia nativa a stream-ului, indiferent de zoom-ul digital).
**🔎 Zoom digital**: rotita mouse-ului (sau pinch pe telefon) pe orice player — HD sau
playback; tragi cu mouse-ul/degetul ca sa te plimbi, dublu-click sau Escape = revenire.
Max 6×, imaginea nu poate fi trasa in afara cadrului. Distinct de **🔍+/−** (zoom
OPTIC, doar pe cele 6 camere cu lentila motorizata — acela schimba ce inregistreaza
camera, zoomul digital e doar afisare).

## Tabul 📷 Camere (administrare)
Registrul unic al camerelor (`data/cameras.json`) — **NVR-ul nu mai e necesar** pentru
functionare, ramane doar arhiva istorica (campul `nvr_channel`). Pentru fiecare camera:
- **redenumire**, link direct la interfata ei, model si canal NVR (daca a fost importata);
- **capabilitati hardware detectate automat** (microfon, difuzor, sirena, stroboscop,
  zoom optic, releu) — butoanele apar in UI doar unde exista hardware-ul;
- **mod de inregistrare**: continuu / la miscare / oprit. "La miscare" inregistreaza
  continuu si sterge apoi segmentele fara evenimente (±30s) — asa nu se pierde niciodata
  inceputul unui eveniment, spre deosebire de pornirea/oprirea ffmpeg;
- **calitate/spatiu**: codec (H.264/H.265), bitrate si fps, cu estimare GB/zi. Se scrie
  direct in camera (ISAPI) — noi inregistram exact ce trimite ea, fara recodare;
- **detectii in camera** (firmware): miscare, trecere linie, intruziune, intrare/iesire
  zona, schimbare scena — comutabile individual.

**Scanner de retea**: buton "🔍 Scaneaza reteaua" — dai un interval (ex. `192.168.1.0/24`)
si credentiale, iar sistemul gaseste camerele (porturi 80/8000/554 + identificare ISAPI:
model, serial, firmware), marcheaza pe cele deja adaugate si le importa in masa pe cele
bifate, cu sondarea completa a capabilitatilor.

## Spatiu (Setari -> 💾)
Sumar ocupare/spatiu liber + optiunea **re-encodare inregistrari vechi** (AV1/HEVC,
doar cu encoder hardware; RTX 2000 Ada are av1_nvenc, deci optiunea e disponibila,
cu decodare tot pe placa). Castigul cel mai mare vine insa din tabul Camere: codec
H.265, bitrate/fps
rezonabile si modul "la miscare" pe camerele linistite.

## Acces din afara casei (HTTPS, push)
HTTPS nu e in nvr-web (waitress nu face TLS): il termina **Traefik**-ul din
`docker-setup` (pv-stack), care are deja certificate Let's Encrypt prin DNS-01 Cloudflare.
Nume: `nvr.exemplu.ro`, A record spre IP-ul LAN (ca `mbus`), fara proxy Cloudflare. Un
fisier dinamic `nvr.yml` in `/docker-storage/pv-stack/traefik/config/dynamic/` cu doua
rute pe `Host(nvr.exemplu.ro)`: `/api/ws` si `/api/frame.jpeg` -> `http://172.20.0.1:8076`
(releul autentificat; pe HTTPS browserul cere fluxurile pe aceeasi origine, `GO2RTC =
location.origin` in app.js), restul -> `http://172.20.0.1:8079`; `certResolver: cloudflare`.
In nvr-web: antetele `X-Forwarded-*` sunt crezute DOAR de la localhost si retelele Docker
(`TRUSTED_PROXIES` in config.py daca proxy-ul sta in alta parte); pe cereri HTTPS se
trimite HSTS si cookie `Secure`; CSP permite `wss://<host>`. Releul inchide conexiunea
dupa fiecare cerere HTTP simpla (`Connection: close`), ca proxy-ul sa nu refoloseasca o
conexiune verificata pentru alt client. Accesul din internet propriu-zis ramane decizia
retelei (VPN/WireGuard pe router sau Cloudflare Tunnel) — aplicatia e pregatita.

**Notificari push** (Web Push, `app/push.py`, `/api/push/*`): fiecare utilizator isi
aboneaza telefonul din „Contul meu" (cere HTTPS sau localhost si permisiunea de
notificari; pe iPhone, PWA instalata pe ecranul de start). Nivel: doar critic / atentie+critic.
Orice eveniment jurnalizat cu severitate >= atentie (`rules._journal`) ajunge la abonatii
cu nivelul potrivit si cu acces la camera (restrictia pe camere a contului), cu legatura
`/#pb/<cam>/<zi>/<sec>` direct in redare (click pe notificare = sare la moment in fereastra
deschisa). Cheile VAPID se genereaza singure (`data/push_vapid.json`, 0600); abonamentele
stau in `events.db` (tabelul `push_abonamente`); un endpoint care raspunde 404/410 se
sterge singur. Setari -> Notificari arata (adminului) toate dispozitivele abonate.
Telegram ramane canalul de rezerva, nu depinde de niciun telefon.

## Operare: mod acasa/plecat/noapte, harta, pop-up la critic, panou I/O
- **Modul sistemului** (`app/mod.py`, `data/mod.json`): comutatorul 🏠 Acasa / 🔒 Plecat /
  🌙 Noapte din antet (admin si operator; viewerul doar il vede). Regulile pot fi valabile
  doar in anumite moduri (pasul 3, „Doar in modul"), schimbarea modului e declansator
  (`mod`, valoare = modul nou) si actiune („trece sistemul in modul…"). Se comuta din:
  antet, Telegram (`/acasa`, `/plecat`, `/noapte`, `/stare` — pornit din Setari →
  Notificari, „Accepta comenzi", doar din chat-ul configurat), MQTT (`nvr/mod/set`, iar
  starea e publicata retained pe `nvr/mod`), API cu jeton (`GET/POST /api/v1/mod`) sau
  o regula. „Noaptea automat" (Setari) trece acasa → noapte la `night_start` si inapoi la
  `night_end`; „plecat" nu se atinge automat. Orice schimbare intra in jurnal (eveniment
  `mod`) si in audit, cu cine si de unde.
- **Harta** (tab „Harta", `app/harta.py`, `data/harta.json` + `data/harta/<plan>.png`):
  planul proprietatii (schita, captura din satelit; PNG/JPEG/WebP ≤ 8 MB) cu camerele
  asezate si conurile lor de vedere. Marcajele arata starea (verde = inregistreaza, gri =
  nu scrie, albastru = detectie, rosu pulsand = alarma; un incident critic face marcajul
  sa pulseze), click deschide HD-ul. Admin → „Editeaza": plan nou, incarca imaginea,
  click pe o camera neasezata o pune in mijloc, trage-o la loc, directia si deschiderea
  conului din panoul lateral. Mai multe planuri (curte, parter, etaj) — selectorul apare
  cand sunt cel putin doua. Un cont restrans vede pe harta doar camerele lui.
- **Pop-up la critic** (bara Live): la un incident critic nou, dala camerei se mareste
  8 secunde si se aduce in ecran (ca „layout switch on event").
- **Panoul I/O** (butonul „I/O" din bara Live, admin si operator): sirena, stroboscop,
  releu si anunt vocal pentru toate camerele care le au, intr-un singur panou, cu textul
  anuntului scris o data sus.

## Analitica retroactiva: regiune, harta de caldura, time-lapse, masca
Toate din tabul Inregistrari (`app/analiza.py`, `app/routes_analiza.py`), pe camera si ziua alese:
- **Cauta pe regiune**: tragi un dreptunghi pe cadru si vezi toate detectiile zilei ale caror
  chenare l-au atins (lista in dreapta, puncte galbene pe timeline, click = reda). Chenarul
  normalizat (`chenar` = [x, y, w, h]) se ataseaza la emitere evenimentelor `detectie`
  (substream si regiune HD), `intrus` si `obiect` (ultimul chenar al track-ului) — deci
  cautarea acopera evenimentele de dupa 2026-10-23, nu si istoricul vechi.
- **Harta de caldura**: traseele oamenilor (`traseu` din episoade) adunate pe o grila 64x36
  peste cadru, pe ziua aleasa, 7 sau 30 de zile.
- **Time-lapse**: ziua intreaga intr-un minut (un cadru pe minut, 720p, MP4 descarcat), ca
  job in coada (o zi intreaga dureaza cateva minute: ~0,5 s per cadru, 4 in paralel).
  Cadrele se extrag prin seek din fiecare segment, in paralel — NU prin
  citirea intregii zile (zeci de GB de HEVC, chiar cu `-skip_frame nokey` ffmpeg tot le
  citeste; prima varianta mergea 10+ minute).
- **Masca de confidentialitate** (HD → butonul lacat, admin; `app/masca.py`, `mask` in
  cameras.json): dreptunghiuri innegrite pe live, HD, redare (orice viteza), clipuri,
  exporturi, fisa probatorie si time-lapse. Inregistrarea pe disc ramane intreaga. In go2rtc
  masca merge prin template-uri per camera (`m<id>`, `m<id>s2`...) generate de
  gen_go2rtc.py, pentru ca go2rtc refuza sursele cu spatii; substreamul unei camere mascate
  se transcodeaza pe NVENC (nu se mai copiaza), iar camera mascata se reda la rezolutie
  nativa (treapta 720/1080 nu i se aplica). Pozele de detectie pe HD raman nemascate
  (interne).

## Date care supravietuiesc: arhiva, copie nocturna criptata, lant de custodie
- **Nivel de arhiva** (Setari → Inregistrare, `arhiva_dir`; `record.py`): cand expira
  retentia obisnuita (`retention_days`), segmentele CU evenimente se MUTA pe al doilea
  disc in loc sa se stearga (copie + verificare SHA-256 fata de amprenta de la inchidere +
  stergere de pe primar); traiesc acolo `arhiva_zile` (implicit 180), cu garda de spatiu
  `arhiva_min_free_gb`. Garda de spatiu a discului principal muta si ea segmentele cu
  evenimente in arhiva in loc sa le stearga. `record._files()` vede ambele niveluri, deci
  redarea, exportul, acoperirea si time-lapse-ul nu stiu diferenta. Oprit cand directorul
  e gol. Nota: discul de 7,3 TB de pe /srv/nfs/disc2_8tb e NTFS montat read-only si tine
  alte date — ca sa fie folosit, trebuie remontat rw (sau alt disc), decizie de luat
  separat; Setarile arata daca directorul exista si se poate scrie.
- **Copia nocturna criptata** (Setari → Unelte; `app/backup_nocturn.py`): zilnic la
  `backup_ora`, un tar.gz criptat (Fernet, cheia din `data/backup_key`, 0600) cu tot ce nu
  se poate reface: configuratia, conturile, regulile, incidentele, dosarele, modul, harta,
  jurnalul de evenimente si amprentele segmentelor (copii SQLite consistente), biblioteca
  de fete (persoane + embeddings, fara modelele descarcabile), audit, cheia de sesiune si
  cheile push. NU intra: inregistrarile, pozele, dataset-ul, indexul semantic (se reface).
  ~35 MB, ~6 s. Local in `backup_dir` (implicit `data/backup`, ultimele `backup_pastreaza`),
  apoi in afara casei prin `backup_remote` (rsync la `user@host:/cale` cu cheia SSH a lui
  openvigil, sau un director montat); daca trimiterea pica, Telegram anunta. „Fa o copie
  acum" ruleaza ca job. **Cheia** se arata din Setari → Unelte si trebuie notata in alta parte —
  fara ea copia e inutila. Refacere: `python scripts/restaureaza-backup.py
  <fisier.tar.gz.enc> <destinatie> [--cheie ...]` (cu serviciile oprite, destinatia `data/`).
  Testul de restaurare ruleaza in pytest (`tests/test_backup_nocturn.py`: creare → refacere
  → comparare, cheie gresita refuzata).
- **Lant de custodie** (`app/lant.py`, `data/segmente.db`): nvr-record calculeaza SHA-256
  pentru fiecare segment la un minut dupa inchidere (cele mai noi primele, 30 pe minut,
  doar ultimele 6 ore ca restanta) si tine amprenta cu momentul calcularii. Fisa
  probatorie (`/api/export/report`) listeaza segmentele-sursa ale clipului cu hash-ul de la
  inchidere si daca sunt neatinse ACUM; `/api/segmente?channel&day` da lista,
  `/api/segmente/verifica?channel&nume` recalculeaza. Mutarea in arhiva se verifica prin
  aceeasi amprenta.

## Robustete si operare (lotul de dupa audit)
- **Spool pentru evenimentele AI**: cand nvr-web e oprit (restart dupa setari, deploy),
  nvr-ai pune evenimentele in `data/ai_spool.jsonl` si le livreaza in ordine la revenire
  (cu `moment_spool` in date). Nicio detectie nu se mai pierde in minutul de restart.
- **Fara modele locale in web cand nvr-ai e configurat**: cu `ai_service` pornit, nvr-web
  NU mai incarca YOLO/SigLIP/VLM/Face ID/LPR local daca nvr-ai nu raspunde (ar fi umplut
  VRAM-ul si le-ar fi doborat pe ambele); rutele raspund cu eroare pana revine nvr-ai.
- **Scrieri atomice peste tot** (`store.atomic_write_json/text`): detect.json, embeddings,
  plates/known.json, go2rtc.yaml, refacerea din backup, tăierea jurnalelor. **Lock** pe orice
  read-modify-write al registrului de camere (`EXT_LOCK`). **ID-uri monotone** de camera
  (`data/camera_ids.json`): o camera noua nu mosteneste id-ul (deci istoricul) uneia sterse.
- **Recorderul reporneste** cand se schimba conexiunea camerei (IP, port, user, parola,
  vendor), nu doar cand se schimba lista de camere.
- **Scoping pe camere intr-un singur loc** (`app/scoping.py`): id-ul in cale
  (`/api/cam/<id>`, `/api/thumb/<id>`, `/api/cameras/<id>`), `channel`/`cam`/`camera` in
  query sau in corpul JSON; un test parcurge toate rutele cu camera in cale.
- **Jetonul API doar in antet** (`X-Token`); `?token=` nu mai e acceptat (ajungea in loguri).
  Login-ul esuat nu mai tine firul un secunda (ramane limita de 8/15 min per IP).
- **`/healthz`** (fara autentificare): 200 cand go2rtc raspunde, jurnalul se citeste si
  inregistrarea scrie; altfel 503 cu detalii. **Watchdog systemd**: nvr-web si nvr-record
  sunt `Type=notify` cu `WatchdogSec` (180 s / 120 s) si trimit READY/WATCHDOG prin
  `app/sdnotify.py` (fara biblioteci) — un proces viu dar intepenit e repornit, nu lasat ore.

## Ce a mai ramas deschis dupa audit: loguri, mesaje, accesibilitate, criptare
- **Loguri cu niveluri** (`app/logare.py`): nvr-web, nvr-ai si nvr-record nu mai scriu cu
  `print`, ci prin `logging` (logger „nvr", pe stderr, preluat de journald). Textul
  mesajelor e neschimbat (eticheta `[record] ...` ramane), deci `journalctl -u nvr-web`
  se citeste la fel; in plus fiecare mesaj are nivel. `Environment=NVR_LOG_LEVEL=WARNING`
  in unitatea systemd (`systemctl edit nvr-web`) arata doar problemele; `DEBUG` arata tot.
- **Mesajele serverului au diacritice**: erorile JSON (`error`, `mesaj`, `hint`, `motiv`),
  textele joburilor, notificarile si intrarile de audit sunt in romana corecta, ca
  interfata. Cheile JSON si valorile „de cod" (stari, tipuri) raman ASCII.
- **Accesibilitate**: toate campurile si listele din interfata au `aria-label`; bara de
  timp din Inregistrari si cea din HD sunt `role="slider"` cu `aria-valuenow`/`aria-valuetext`
  (ora curenta) si se pot focaliza din tastatura; punctele de pe bara (evenimente/detectii/
  reguli) au contrast suficient pe fundalul inchis.
- **Criptare la rest** (doar instalarea clasica; in varianta Docker criptarea se face la nivelul gazdei,
  cu LUKS sau fscrypt pe directorul `data/`, iar cardul din Setari si scripturile de mai jos nu exista)
  **: fscrypt pe tot `data/`, cu cheie-master si pornire automata**
  (2026-10-23, cu pasul intermediar „parola dupa reboot" inlocuit in aceeasi seara, dupa
  decizia: dupa o pana de curent totul trebuie sa revina singur). Nu in aplicatie: ffmpeg are
  nevoie de MP4-uri citibile, iar producatorii (Hikvision, Dahua, UniFi) nu cripteaza deloc
  sau tin cheia pe aparat. Cu fscrypt (ext4 `encrypt`, politica v2, AES-256-XTS) tot ce e in
  `data/` — inregistrari, poze, `cameras.json` cu parolele camerelor, `users.json`,
  `events.db`, fete, placute, `go2rtc.yaml` (mutat aici din `go2rtc/`) — e criptat
  transparent; procesele vad totul in clar cat timp cheia e in kernel.
  - **Cheia-master** `/etc/nvr/master.key` (root, 0600, 32 de octeti aleatori) e protectorul
    `nvr-auto`; `nvr-unlock.service` (oneshot, inainte de nvr-go2rtc/web/record/ai, care au
    drop-in `After=/Wants=`) deblocheaza `data/` la boot. **Cine ia discul cu tot cu masina
    citeste datele** (cheia e pe el): protectia e pentru copii/clone ale partitiei si pentru
    fisiere citite fara root, acelasi compromis ca la producatori. O copie a cheii pe un
    stick USB tinut in alta parte te scapa daca discul de sistem moare.
  - **Parola de rezerva** (protector cu parola, nu sta pe masina): deblocheaza manual cand
    cheia lipseste (`sudo fscrypt unlock data`), sau din banner-ul galben al interfetei. Se
    seteaza/inlocuieste din **Setari -> Sistem -> Criptare pe disc**, cu parola contului de
    admin (re-autentificare); cardul arata si starea: criptat, deblocat, cheie, unitate.
  - **Asistentul** `sudo scripts/criptare.sh` (interactiv: explica fiecare pas, intreaba,
    face) activeaza criptarea pe o instalare noua: fscrypt, feature-ul ext4, cheia,
    protectorul, mutarea lui `data/`, unitatile. Interfata ruleaza acelasi script prin sudo
    (`/usr/local/sbin/nvr-criptare`, copie root, singura comanda din
    `/etc/sudoers.d/nvr-criptare`) pentru `stare`, `parola`, `deblocheaza`. Activarea NU se
    face din interfata: muta chiar directorul din care ruleaza serverul.
  - **Daca deblocarea automata esueaza** (cheia lipsa, unitate stricata): nvr-record nu crapa
    (`record.blocat()` = creare de fisier da ENOKEY; anunta pe Telegram, READY + watchdog,
    porneste singur dupa deblocare), `/healthz` da 503 cu `blocat`, iar nvr-web porneste doar
    dupa deblocare (are nevoie de `users.json`): deblochezi prin SSH cu parola de rezerva.
  - **Migrarea**: `scripts/fscrypt_migrare.sh` (inregistrarile, camera cu camera, cu
    inregistrarea pornita) si `scripts/fscrypt_date.sh` (restul lui `data/`, 4-5 minute fara
    servicii; inregistrarile se muta prin redenumire, aceeasi politica). Hash-urile din
    lantul de custodie raman valabile.
- **Deconectarea e sigura** (`SESSION_REFRESH_EACH_REQUEST=False`): cookie-ul de sesiune se
  trimite doar cand sesiunea se schimba. Inainte, Flask il retrimitea la orice raspuns, iar un
  raspuns intarziat la o cerere pornita inainte de `/logout` reinstala sesiunea veche (gasit
  de testul de 2FA dupa ce banner-ul de deblocare a adaugat o cerere la incarcarea paginii).
  Expirarea de 30 de zile curge de la login, nu glisant.

## Interfata: polling pe tab, grila oprita sub HD, Esc, taste PTZ, telefon
- Starile de alarma/detectie (`/api/events`) se cer doar cand se vad (Live, Harta, HD) —
  nu si din Setari sau Evenimente. Sub overlay-ul HD, fluxurile din grila se opresc
  (`gridPause`) si repornesc la inchidere; HD-ul isi tine propriul substream dedesubt.
- **Esc** inchide HD-ul si orice overlay vizibil (masca, regiune, harta de caldura, zona,
  linii, adaugare camera, dosare, rezumat, clip, poza) dintr-un singur loc.
- **PTZ de la tastatura** in HD (admin/operator): sagetile rotesc, `+`/`-` (sau PageUp/
  PageDown) fac zoom, cu oprire la ridicarea tastei si opritorul de siguranta de 4 s —
  doar pe camerele care chiar se rotesc / au lentila motorizata.
- Toast-ul are `role=status`/`aria-live`; incidentele critice si erorile sunt `role=alert`.
- Pe ecrane tactile (`pointer: coarse`) butoanele de navigare, bara Live, controalele de
  redare si ale camerei au minimum 44 px.
- **Diacritice** in tot textul vizibil (pagina, mesaje, paginile de login); cheile de cod
  trimise serverului raman ASCII.
- **Interfata e in module ES adevarate**, pe ecrane, in `app/static/js/`: `00-core`
  (html``, toast, sesiunea, joburi, tabelele de etichete), `05-navigare`, `10-live`, `20-hd`,
  `25-harta`, `30-redare`, `40-evenimente`, `50-camere`, `60-setari`, `70-ai` si `state.js`.
  `app/static/app.js` e doar intrarea: importa modulele in ordine si porneste aplicatia
  (`loadCameras().then(routeHash)`). Reguli: nucleul nu importa din niciun ecran (se
  evalueaza primul, deci `$`, `html`, `ME` exista cand ecranele isi ruleaza codul de la
  incarcare); ecranele se pot importa incrucisat (legaturi vii: un `let` exportat se vede
  cu valoarea curenta); variabilele SCRISE din mai multe module stau in `state.js` ca
  `S.<nume>` (`S.cameras`, `S.currentView`, `S.pbExtra`, `S.pendingProba`, `S.popIgnora`),
  pentru ca ES nu lasa un modul sa atribuie un import. `scripts/module_check.py` (si
  `tests/test_module_check.py`) verifica importuri/exporturi, regula nucleului si sintaxa.
  Cand adaugi o functie folosita din alt ecran: o pui in `export { ... }` la finalul
  modulului si o imporți explicit.
- Nefacute, cu motiv: criptarea inregistrarilor pe disc (ar rupe concat/seek-ul ffmpeg si
  redarea directa; copia de siguranta e criptata, iar discul e in casa). (Rezolvata
  ulterior prin fscrypt.)

## Multi-vendor: drivere hik / onvif
Fiecare camera are `vendor` in registru (`hik` implicit, `onvif` pentru restul), iar tot ce
depinde de producator trece prin `app/drivers.py`: URL-urile RTSP (inregistrare, grila, HD,
verificarea de sanatate), poza la rezolutie mare (Face ID, confirmarea de intrus),
identificarea si ping-ul (watchdog), capabilitatile, PTZ + presetari, releul, ora si
evenimentele. `hik` deleaga la `camctl.py` (ISAPI, neschimbat); `onvif` foloseste
`app/onvif.py`, un client SOAP minimal (WS-Security UsernameToken, apoi HTTP Digest) fara
biblioteci noi: GetDeviceInformation, GetServices/GetCapabilities, GetProfiles +
GetStreamUri (profilul cel mai mare = main, urmatorul = sub), GetSnapshotUri, PTZ
ContinuousMove/Stop, presetari, relee (DeviceIO), ora. URL-urile aflate la adaugare se
tin in `onvif: {main, sub, poza, port}` pe camera.
Ce NU exista pe ONVIF (si interfata nu arata butoanele): sirena, stroboscop, difuzor
(two-way audio ISAPI), setarea calitatii fluxului, sincronizarea orei, proba de rotire,
evenimentele proprii (alertStream) — detectia AI pe flux acopera oricum. Adaugare: butonul
„Adauga camera" → „Alt producator (ONVIF)" + portul HTTP; scannerul identifica ONVIF cu
aceleasi credentiale si le importa cu `vendor: onvif`. Cu masca de confidentialitate si
restul functiilor de redare, o camera ONVIF se comporta identic (fluxul e tot RTSP).
Testat pe un server SOAP fals (`tests/test_onvif.py`); pe o camera reala, primul lucru de
verificat e ca GetStreamUri intoarce un RTSP accesibil de pe server.

## Login, zone de detectie, PWA, statistici
- **Login obligatoriu, cu conturi si roluri** (`data/users.json`, chmod 600, parole
  hash-uite): la prima pornire /setup creeaza contul de administrator. Rolul
  **viewer** vede doar Live/Inregistrari/Alarme pentru camerele sau grupurile alese
  (Setari -> Sistem -> Utilizatori); serverul ii refuza orice POST/DELETE (403), nu
  doar ascunde butoanele. Rolul **operator** (paznicul) vede la fel SI poate actiona
  live pe camerele lui — sirena, vorbit, stroboscop, releu, PTZ, pozitii (fara
  stergere) — dar nicio configurare: lista alba `OPERATOR_ACTIONS` din server.py. Protectii: CSRF pe toate modificarile (antet X-CSRF),
  cookie HttpOnly+SameSite, rate limiting la login (8 esecuri/15 min per IP),
  jurnalizarea login-urilor in audit.
  XSS: tot ce intra in innerHTML trece prin `html\`\`` (app.js), care scapa automat orice
  valoare interpolata; serverul trimite Content-Security-Policy (scripturi doar ale noastre,
  fluxuri doar prin releul :8076), X-Frame-Options, nosniff. API-ul /api/v1 ramane pe jetonul lui X-Token.
  go2rtc asculta doar pe localhost: fluxurile ajung in browser prin releul :8076 din
  nvr-web, care cere aceeasi sesiune (viewerii restransi vad doar camerele lor).
- **Contul meu** (click pe numele utilizatorului, sus; orice rol): autentificare in doi
  pasi, notificari push pe dispozitivul curent, deconectarea celorlalte dispozitive.
  - **2FA (TOTP)**: QR scanat in Google Authenticator/Aegis/1Password, confirmat cu un cod;
    8 coduri de recuperare de unica folosinta (aratate o singura data). La login, dupa
    parola, /login/2fa cere codul (5 minute, acelasi rate-limit). Codurile deja folosite
    se refuza (anti-replay). Adminul poate scoate 2FA de pe orice cont (Setari ->
    Utilizatori -> „scoate 2FA"). Implementare in `app/totp.py`, fara dependente.
  - **Sesiuni revocabile**: cookie-ul poarta versiunea contului (`sess_ver` in users.json);
    schimbarea parolei, „deconecteaza toate dispozitivele" sau „deconecteaza" din lista de
    conturi o incrementeaza si TOATE sesiunile vechi mor. Sesiunea tine 30 de zile de la
    ultima folosire. Pe HTTPS cookie-ul primeste `Secure` (per cerere, LAN-ul pe HTTP merge
    in continuare).
  - **Push**: vezi „Notificari push" mai jos.
- **Jurnalul de modificari** (Setari -> Jurnal) retine utilizatorul (nu IP-ul; IP-ul apare
  la hover) si, ca plasa de siguranta, orice POST/PUT/PATCH/DELETE reusit pe care ruta nu
  l-a auditat explicit („cerere POST /api/..." cu corpul, fara secrete). 20.000 de intrari.
- **Zone de detectie**: butonul ▣ (in bara HD a camerei) deschide editorul — desenezi
  poligonul pe un cadru live; doar detectiile cu centrul in zona conteaza.
- **PWA**: pe telefon "Add to Home Screen" -> aplicatie standalone cu iconita.
- **Statistici**: mini-histograma pe ore in tabul Alarme (respecta filtrele active).
- **⛏ Extrage cadre din inregistrari** (Setari): 1 cadru/min din toate inregistrarile
  locale -> data/dataset, pentru antrenament.
Config NVR (IP/user/parola, port web, inaltime playback): `config.py` (chmod 600).
Dupa ce schimbi `config.py`: `systemctl restart nvr-go2rtc nvr-web`.

## GPU (accelerare transcodare)
**Activ**: tot lantul video ruleaza pe GPU (RTX 2000 Ada: decode NVDEC + encode
h264_nvenc) — vezi `journalctl -u nvr-web | grep hwaccel` ("DA"). Fara placa,
sistemul cade automat pe CPU (libx264, redare accelerata limitata la 2x).
