# Schimbări

Formatul urmează [Keep a Changelog](https://keepachangelog.com/) și
[Conventional Commits](https://www.conventionalcommits.org/).

## [Nepublicat]

### Schimbat
- **Live cu puține camere arată mai bine pe telefon** — când ai una sau două camere (în modul
  „Auto"), grila le afișează acum pe **o singură coloană, cu dale mari**, în loc de două dale mici
  cu un gol negru dedesubt. La mai multe camere, grila umple oricum ecranul (nimic schimbat). Poți
  oricând alege explicit 2 coloane din selectorul de densitate.
- **Dale de Live curate pe telefon** — pe ecrane mici, fiecare cameră din grilă arată acum **doar
  imaginea live** (plus numele și punctul de stare), fără butoanele de acțiune (sirenă, strobo,
  vorbit, zoom, captură, AI) și fără controalele native de video. Toate acțiunile și mișcarea
  camerei (PTZ) sunt pe camera deschisă în HD — atingi dala și le ai acolo. Pe desktop nimic nu se
  schimbă.

## [2.1.0] — 2026-11-12

**Highlights (English)** — OpenVigil is now an installable mobile app with push notifications, and
the whole interface has been reworked for phones.
- **Install it like an app, with push.** Add OpenVigil to your home screen (Android or iPhone) and
  it opens full-screen like a native app. Event notifications now show a camera thumbnail, a
  “View” button that jumps straight to the moment, and an app-icon badge. (On iPhone, push works
  once the app is added to the Home Screen — iOS 16.4+.)
- **Rebuilt for phones.** Every screen puts the content first and tucks power tools behind a “⋯”:
  Recordings (player and timeline above the fold), Live (cameras first), Settings and the AI tab
  (sub-tabs wrap instead of being cut off), Cameras and Events. Text fields no longer make iOS
  zoom when you tap them.
- **Single-camera view.** Swipe left/right to move between cameras, swipe down to go back; edge
  arrows “‹ ›” show you can. The close button no longer hides under the iPhone notch.
- **Large dialogs scroll correctly** on phones (Schedule & automation, Health center, etc.).

### Adăugat
- **Aplicație instalabilă pe mobil, cu notificări push** — interfața se poate adăuga acum pe
  ecranul de start (telefon sau desktop) și pornește ca o aplicație de sine stătătoare, pe tot
  ecranul. Pe Android/Chrome apare un buton **„Instalează aplicația"**; pe iPhone/iPad
  (iOS 16.4+) notificările push merg **după** ce o adaugi pe ecranul de start din Safari
  (Partajează → „Adaugă la ecranul de start") — cardul de notificări explică pașii. Notificările
  push arată acum **miniatura camerei**, un buton **„Vezi"** care sare direct la moment, și un
  **contor pe iconița aplicației**. (Manifest cu scurtături Live / Evenimente / Înregistrări și
  iconiță „maskable" pentru Android.)

### Schimbat
- **Înregistrări, mult mai ușor de folosit pe telefon** — pe ecrane mici, uneltele de
  export/analiză (Descarcă MP4, Exporturile mele, Caută pe regiune, Harta de căldură, Time-lapse)
  se pliază sub un buton **„Unelte"**, iar controalele secundare de redare (cadru cu cadru,
  eveniment anterior/următor, Captură, Marcaj, Link, calitate) sub **„⋯"**. Rămân la vedere
  esențialele — Pauză, ±10s, viteza — așa că **player-ul și linia timpului se văd fără derulare**.
  Pe desktop nimic nu se schimbă. (Suită de audit mobil Playwright + regresie în CI.)
- **Live, camerele primele pe telefon** — pe ecrane mici, acțiunile secundare (Previzualizare,
  Mod perete, I/O, Pop-up la critic, Flux) se pliază sub **„⋯"**, iar bara de vizualizări salvate
  (feature de desktop) se ascunde. Rămâne vizibil doar selectorul de câte camere pe rând +
  căutarea, așa că **grila de camere urcă sus**. Pe desktop nimic nu se schimbă.
- **Setări, toate secțiunile vizibile pe telefon** — pe ecrane mici, sub-taburile (Stare,
  Înregistrare, Detecție, Notificari, Utilizatori, Audio, Sistem, Unelte, Jurnal) se **așază pe
  mai multe rânduri** în loc să se taie la dreapta fără niciun indiciu; căutarea stă pe propriul
  rând, lățime completă. (Același lucru și pentru filele din tabul AI.)
- **Fără zoom enervant pe iPhone** — când atingeai un câmp (dată, căutare, setări), Safari mărea
  automat pagina (fiindcă fontul era sub 16px). Pe telefon câmpurile de text au acum 16px, așa că
  pagina **nu mai sare la zoom** când scrii.
- **Camere și Evenimente, mai curate pe telefon** — la Camere, acțiunile de administrare (Scanează
  rețeaua, Selectează tot ce se vede) trec sub **„⋯"**, așa că rămân în față căutarea și filtrul pe
  grupuri, iar lista de camere urcă. La Evenimente, Rezumat și Dosare trec sub **„⋯"**, ca filtrul
  (Netratate / Toate / Critice) și lista de incidente să fie primele. Pe desktop nimic nu se schimbă.
- **Vizualizarea unei camere pe telefon: ieși ușor și treci între camere** — butonul „✕ Închide" nu
  mai ajungea sub bara de stare / notch pe iPhone (când aplicația e instalată pe ecranul de start),
  așa că părea că nu mai poți reveni la grilă; acum respectă marginea sigură și e la îndemână. În
  plus, pe cameră poți da **swipe stânga/dreapta ca să treci la camera următoare/anterioară** și
  **swipe în jos ca să închizi** și să revii la grilă. Pe margini apar **săgeți „‹ ›"** care arată
  că poți schimba camera (și sunt și ele tap-abile). (Antetul aplicației respectă marginea de sus
  pe iPhone.)
- **Ferestrele mari se derulează corect pe telefon** — la „Program & automatizare" și „Centru de
  sănătate" (și celelalte ferestre pe tot ecranul), conținutul mai înalt decât ecranul nu se mai
  taie: acum se derulează de sus în jos, butonul „Închide" rămâne la îndemână sus, iar bara lor
  respectă marginea de sus pe iPhone.

## [2.0.26] — 2026-11-08

### Adăugat
- **Starea canalului pe camere, în Centrul de sănătate** — fiecare cameră arată acum și dacă
  watchdog-ul o vede **deconectată** (insignă roșie „deconectat" + rând evidențiat), nu doar
  înregistrarea/golurile/sabotajul. Starea vine din watchdog-ul care deja monitoriza camerele
  (ISAPI/ONVIF) și alerta pe Telegram; acum se vede și pe ecran, per cameră (`/api/pulse` →
  câmp `online`).
- **Panoul „Configurare" în tabul AI** — toată configurarea AI (Face ID, LPR, semnalmente,
  căutare semantică: pornit/oprit, praguri, camere de alertă) stă acum într-un singur loc, cu
  **bara de salvare** de jos (modificările se adună; salvezi o dată, ca în Setări — se trimit
  doar grupurile schimbate). Panourile *Persoane* și *Mașini* rămân curate, doar cu conținutul
  (biblioteca, fețele/numerele necunoscute, jurnalul). Căutarea semantică s-a mutat aici din
  Setări → Detecție. (Înainte fiecare comutator se salva imediat, separat.)

## [2.0.25] — 2026-11-08

### Adăugat
- **Pagina „Program & automatizare"** (tab nou, doar admin) — două lucruri într-un loc:
  - **Program moduri** — un orar săptămânal de moduri (timeline): pe fiecare zi pui schimbări
    „la ora X → modul Y" (acasă / plecat / noapte). La ora fiecărei schimbări, sistemul comută
    automat; între schimbări, o comutare manuală rămâne până la următoarea (edge-triggered, cu
    recuperare după o repornire). Înlocuiește, când e activ, vechea „fereastră de noapte" unică
    (`mod_auto_noapte`), care rămâne ca rezervă. „Plecat" nu se mai setează singur din fereastra
    de noapte, dar îl poți pune explicit în orar.
  - **Reguli** — lista regulilor cu pornit/oprit pe loc și **„ultima declanșare"** (până acum
    nicăieri în interfață); „editează" duce la formularul din AI → Reguli.

## [2.0.24] — 2026-11-08

### Schimbat
- **Dosarele de caz trec în PostgreSQL** (`dosare` + `dosar_probe`), din fișierul
  `data/cases.json`. La fel ca jurnalul, marcajele și probele, dosarele intră acum în **copia
  nocturnă criptată** (dump-ul logic al bazei) și beneficiază de scrieri atomice reale — gata cu
  citește-modifică-scrie peste un fișier JSON. Cazurile vechi din `cases.json` se **importă o
  singură dată** la pornire (fișierul e redenumit `.migrat`), fără pași manuali. Comportamentul
  vizibil e neschimbat: aceleași dosare, aceleași probe, același export ZIP verificabil.

## [2.0.23] — 2026-11-08

### Adăugat
- **„Păstrează ca probă"** — selectezi un interval pe timeline (în Înregistrări) și apeși
  *🔒 probă*: intervalul e **scutit de curățare**. Retenția nu-l mai șterge — nici după vârstă,
  nici garda de spațiu când discul se umple (atunci se **alarmează** în loc să-l taie). Probele
  se văd ca o bandă verde cu lacăt pe timeline, au listă proprie (click = redare, ✕ = ridică
  protecția) și pot avea un termen (altfel pe termen nedefinit). Footage-ul adăugat la un
  **dosar de caz se auto-protejează**, așa că un dosar nu-și mai pierde propriul material la
  curățare. Probele stau în PostgreSQL (`app/probe.py`, API `/api/probe`); operatorul le pune
  doar pe camerele lui. Dacă baza nu răspunde, retenția **amână** curățarea o oră în loc să
  riște o probă.

## [2.0.22] — 2026-11-08

### Adăugat
- **Centru de sănătate** (tab-ul „Stare" din antet) — un singur loc pentru „merge totul?": serverul
  (procesor, memorie, disc liber, mărimea înregistrărilor, trafic rețea, uptime), canalele de alertă
  (Telegram / e-mail / push, cu avertisment când nu există niciunul), subsistemele AI (serviciu, GPU,
  fețe / LPR / descriere / semantic — pornit vs. model încărcat, MQTT, reguli active), starea NVR-ului
  și o grilă cu fiecare cameră (ultima imagine, insigne REC / alarmă / mișcare, scrie? / goluri azi? /
  sabotaj?). Rândurile problematice ies în evidență. Patru apeluri în paralel, redesenare la cerere;
  nimic nu se salvează. Închidere cu Esc.

## [2.0.21] — 2026-11-08

### Adăugat
- **Pagina „Exporturile mele"** (din Înregistrări) — istoricul persistent al exporturilor (MP4, clip,
  dosar ZIP, fișă, time-lapse): tip, oră, interval, cameră, stare (în lucru / gata / eroare /
  „parțial X/Y"), cu **descărcare din nou** cât timp fișierul e pe server (~1 oră). Nu mai depinzi de
  tava tranzitorie din colț sau de un refresh pentru a regăsi un export (audit R4).

## [2.0.20] — 2026-11-08

### Reparat / Îmbunătățit
- **Navigare și accesibilitate (auditul 5, Lot A).**
  - **T5** — subpaginile AI (Persoane / Mașini / Reguli) se încarcă lazy, doar cea activă, nu toate
    deodată la intrarea în AI.
  - **T2** — subnavurile AI și Setări au acum model de taburi ARIA (role=tablist/tab/tabpanel,
    aria-selected, aria-controls) — citibile de cititoarele de ecran.
  - **T3** — subpaginile AI și Setări au adresă proprie (`#ai/<secțiune>`, `#set/<secțiune>`): un
    link direct deschide exact secțiunea, iar navigarea e previzibilă.

## [2.0.19] — 2026-11-07

### Adăugat
- **Versiunea care rulează, vizibilă în antet** (lângă „OpenVigil") — utilă mai ales în dezvoltare,
  ca să știi pe ce build ești.

### Reparat
- **Căutare (auditul 5, E1–E5).**
  - Căutarea semantică din galerie respectă filtrul de cameră și spune explicit domeniul temporal
    („toate camerele, ultimele 30 de zile"), ca să nu pară că investighezi doar ziua/camera selectată.
  - Căutarea globală: rezultatele parțiale sunt semnalate („rezultate parțiale — indisponibile: …")
    în loc să pară complete când o sursă pică; aceeași perioadă se aplică tuturor surselor (numere și
    persoane erau nefiltrate temporal); la filtrul „Poze" ordinea e după relevanță, nu cronologică.
  - Refresh-ul incidentelor urmărește și severitatea + detaliul afișat, nu doar id/stare/operator.
- **Camere.** Panoul de administrare al camerei se închide când părăsești secțiunea Camere;
  „selectează tot ce se vede" selectează doar pagina curentă, nu și camerele de pe alte pagini.
- **Adnotare.** Scurtăturile de tastatură se suspendă când e deschis un dialog (nu mai confirmă cadrul
  din fundal).
- **Setări.** Avertisment la închiderea/reîmprospătarea paginii dacă există modificări nesalvate.
- **Live.** Previzualizările (imagini reîmprospătate) nu mai consumă trafic cât ești pe alt ecran.

## [2.0.18] — 2026-11-07

### Reparat
- **Utilizabilitate: hartă, listă de camere, tastatură (auditul 5, Top-5 #4).**
  - **Harta:** tragerea unei camere salvează acum poziția (nu se mai reconstruia SVG-ul în timpul
    tragerii, ceea ce rupea gestul și pierdea salvarea); o eroare de încărcare se distinge de „harta
    neconfigurată" și oferă „Reîncearcă"; marcajele și camerele neplasate se pot deschide/poziționa
    de la tastatură (focus + Enter/Space + nume accesibil).
  - **Lista de camere pe mobil:** layout dedicat pe două rânduri (miniatură + nume, apoi sănătate +
    mod de înregistrare) — nu se mai înghesuie zece coloane pe un ecran de telefon; `Enter` pe rândul
    evidențiat deschide camera (lipsea, deși era promis).
  - **Live:** `Enter`/`Space` pe un control din dala camerei nu mai deschide și HD pe lângă acțiunea
    lui; „Contul meu" din antet e acum buton (focusabil, cu tastatura).
  - **Robustețe:** opritoarele comenzilor PTZ ținute apăsat nu mai acumulează listeneri `window.blur`
    la fiecare reconstruire a controalelor — un singur manager comun le oprește la pierderea focusului.

## [2.0.17] — 2026-11-07

### Reparat
- **Corectitudinea stării afișate și a salvărilor (al cincilea tur de audit).** Interfața nu mai
  poate confirma o salvare eșuată, afișa datele altei camere sau sugera că sistemul funcționează
  când telemetria lipsește:
  - **Reguli:** modificarea unei reguli **oprite** nu o mai reactivează (ar fi putut reporni sirene,
    relee sau notificări) — starea „activă" se păstrează, nu se forțează la editare.
  - **Salvări verificate:** configurarea AI (fețe/plăci) și comutatoarele din panoul camerei
    (detecție, anunț automat, alertă la cădere) confirmă „Salvat" **doar** după un răspuns valid și
    revin la starea dinainte la eșec; controlul e blocat cât e cererea în curs.
  - **Setări:** dacă o secțiune nu s-a încărcat, salvarea globală e **blocată** (altfel ar fi scris
    valori implicite peste configurația reală); pragul `0 dBFS` nu mai e înlocuit cu `-25`.
  - **Context cameră/zi/moment:** la Înregistrări, un răspuns întârziat de la camera selectată
    anterior nu mai scrie peste timeline-ul (segmente, evenimente, marcaje) camerei curente;
    trecerea HD → Înregistrări păstrează și secunda vizionată; adnotarea nu mai înlocuiește cadrul
    curent cu un răspuns întârziat.
  - **Sănătate reală:** punctul de stare din Live e verde doar când fluxul chiar dă cadre (altfel
    „se conectează"); în lista de camere, lipsa telemetriei apare ca „se verifică", nu verde; panoul
    de stare din Setări marchează datele drept învechite când actualizarea eșuează.

## [2.0.16] — 2026-11-07

### Reparat
- **Cădere — negațiile contractate în confirmarea VLM.** Un răspuns ca „the person hasn't fallen"
  (sau „isn't on the ground") mai ieșea pozitiv, fiindcă se potrivea cuvântul „fallen" iar negația
  contractată „n't" nu era recunoscută. Acum negațiile prind și contracțiile engleze și formele
  scrise, deci asemenea răspunsuri nu mai declanșează o escaladare falsă.

## [2.0.15] — 2026-11-07

### Reparat
- **Integritate și concurență (al treilea tur de audit).** Bug-uri de consistență și de integritate
  a probelor, cu teste de regresie:
  - **Spool AI:** la livrarea întârziată a evenimentelor către nvr-web, rescrierea cozii pornea
    dintr-un instantaneu vechi și putea pierde evenimentele apărute cât se trimitea. Acum coada se
    reciteşte sub lock și se înlocuiește atomic — niciun eveniment pierdut.
  - **Dosare de caz:** două adăugări de probă în același timp puteau lăsa o singură probă (fără
    eroare). Operația citire-modificare-scriere e acum indivizibilă.
  - **Export dosar:** un dosar al cărui clip(uri) nu s-au putut reda nu mai produce un ZIP „gata" gol
    — eșuează explicit, iar când doar o parte reușesc starea spune „parțial — X din Y probe".
  - **Cădere:** confirmarea VLM nu mai interpretează o negație („nu, persoana nu este căzută") drept
    confirmare — parser strict da/nu, ca să nu escaladeze fals.
  - **Jurnal filtrat pe camere:** restricția de vizibilitate se aplică în interogare (înainte de
    limită), ca un utilizator cu acces parțial să nu primească o listă goală când evenimentele
    recente sunt de la camere fără acces.
  - **Joburi:** același export cerut de doi utilizatori nu mai întoarce jobul altuia (pe care accesul
    i l-ar refuza apoi) — deduplicare per solicitant.
  - **Interfață — curse „ultima acțiune câștigă":** redarea (schimbare rapidă de cameră/moment) și
    căutarea globală nu mai amestecă rezultate din cereri vechi; salvarea din Setări nu mai declară
    salvată o editare făcută în timpul salvării; iar `Tab` funcționează corect când „Contul meu" e
    deschis peste meniul „Mai mult".

## [2.0.14] — 2026-11-07

### Reparat
- **Cazuri-limită din auditul de frontend (al doilea tur).** Auditul de urmărire a confirmat că
  remediile din 2.0.13 țin; acest lot închide cinci cazuri-limită rămase:
  - **HD, redare istorică:** dacă închizi HD-ul în prima jumătate de secundă după ce ai cerut o
    înregistrare, playerul nu mai reapare într-un overlay ascuns (timer-ul verifică „generația" și
    se anulează la închidere/schimbare).
  - **„Contul meu" din meniul „Mai mult" (mobil):** dialogul nu mai rămâne inert — inertizarea
    fundalului sare peste `<dialog>`-urile native, care își fac singure modalitatea.
  - **Taste-săgeți în HD live:** rotesc camera doar când focusul e „neutru" (pe overlay), nu când
    ești pe un buton/slider/timeline — nu mai fură tastatura controlului focusat.
  - **Setări:** draftul nu se mai pierde la ieșirea și reintrarea în tab; formularul se reîncarcă
    doar la „Renunță" sau după salvare.
  - **Oprirea fluxului:** un player scos din DOM care avea deja pornit timer-ul lung (90 s, de la un
    tab ascuns) se oprește imediat, nu după expirarea lui.

## [2.0.13] — 2026-11-07

### Adăugat
- **Reguli de analiză + prima regulă: alertă la căderea unei persoane.** Strat subțire peste
  percepție (percepție → candidat → confirmare → politică → escaladare, vezi `docs/reguli-analiza.md`).
  Pe camerele cu „Alertă la cădere" pornit (comutator în fișa camerei), pentru fiecare persoană
  urmărită rulează pose (`yolo11n-pose`) → scor geometric de cădere (orizontalitatea trunchiului +
  raport bbox + poziția capului) → dacă rămâne jos ≥ `cadere_dwell_s` (persistență) → confirmare VLM
  (Qwen, în fundal, ca să nu blocheze detecția) → eveniment `cadere` (severitate `critic`). nvr-web
  ridică un incident critic + pop-up + Telegram propriu și **escaladează** printr-un `POST /v1/fire`
  către serviciul extern de alerte (fan-out Telegram + SMS + email), decuplat și oprit fără cheie —
  alerta primară pleacă oricum. Praguri reglabile (`cadere_*`), pose doar când o persoană e prezentă
  (cost per eveniment). Logica pură e testată fără model (keypoints/VLM injectate).

### Reparat
- **Audit de frontend — 20 de corecții de corectitudine și UX** (raport independent), pe șase loturi:
  - **Player/HD:** fără cursă la deschiderea HD (două click-uri rapide nu mai amestecă fluxurile sub
    titlul altei camere); zoomul digital și captura lucrează pe videoul vizibil, nu pe substream-ul de
    sub HD; tastele-săgeți rotesc camera doar în HD **live**, nu cât te uiți la o înregistrare; cursorul
    de redare nu mai alunecă după ceas peste o imagine înghețată/căzută; playerele scoase din DOM se
    opresc imediat (nu mai țin fluxul și transcodarea HD ~90 s degeaba).
  - **Roluri și export:** operatorul își păstrează acțiunile (I/O, mod, triaj de incidente) și pierde
    doar configurarea; exportul de probă din fișă gravează filigranul (parametrul `wm` era ignorat de
    server) și durează 30 s, cât spune eticheta.
  - **Stare care nu se mai pierde:** polling-ul din Setări nu mai suprascrie câmpurile editate
    nesalvate; joburile în lucru revin în tavă după un refresh și se descarcă la final; triajul de
    incidente nu se mai reconstruiește (și nu mai pierde scroll-ul/paginarea) când nimic nu s-a schimbat.
  - **Navigare și căutare:** deep-link-urile (`#set`, `#pb`, `#ai`, `#cams`) inițializează ecranul, nu
    doar îl afișează gol; o căutare picată spune „nu a mers", nu „nimic găsit".
  - **Accesibilitate și vizual:** overlay-urile proprii + HD + foaia „Mai mult" au capcană de focus,
    fundal inert și revenirea focusului; câmpul de căutare nu mai dispare la 761–800 px; imaginea scenei
    nu mai e redimensionată de două ori pe telefon; textul verde de stare are contrast suficient (AA).
  - **Semantica acțiunilor:** butonul care doar șterge un snapshot spune „Șterge poza" (nu „Fals", care
    amesteca triaj/feedback/ștergere); comenzile optimiste (sirenă, strobo, releu, detecție, I/O) revin
    la starea corectă dacă cererea pică; listenerii video nu se mai adună la reconstruirea grilei.

## [2.0.12] — 2026-11-06

### Adăugat
- **Butoane de verdict ✓/✕ pe notificările Telegram de FAȚĂ și NUMĂR** (ca cele de la detecții),
  cu acțiuni reale. Pentru o față recunoscută (`👤 <nume>`): „✓ e <nume>" (semnal de încredere) și
  „✕ nu e <nume>" — crop-ul feței, salvat acum la notificare (`faces.save_recognized`, în
  `data/faces/recent/`), **se mută în `data/faces/unknown/`** pentru re-etichetare (fața rămâne pentru
  re-ID, dar fără eticheta greșită; o reașază următoarea regrupare). Pentru un număr citit
  (`🚗 <placă>`): „✓ corect" și „✕ greșit" — citirea **iese din `data/plates/log.jsonl`** (nu mai
  contează ca trecere reală) și crop-ul ei se șterge. Fiecare verdict se jurnalizează
  (`data/faces/feedback.jsonl`, `data/plates/feedback.jsonl`). Module noi `fata_feedback.py` (`fv|`) și
  `numar_feedback.py` (`nv|`), rutate în `telegram_bot` lângă `dt|` (detecții) și `sc|` (scenă).
- **Verdictele pe detecții în fișa camerei**: verdictele date pe detecțiile YOLO de pe Telegram
  (sursă „detectie") apar acum ca niște carduri cu verdict în secțiunea „Învață scena" a fișei camerei,
  lângă cele de scenă (decupajul era deja salvat).

## [2.0.11] — 2026-11-06

### Reparat
- **Antetul — corectie**: compactarea nav-ului din 2.0.10 nu strangea efectiv butoanele (padding-ul
  pus era identic cu cel implicit), asa ca pe desktop nav-ul tot derula orizontal. Acum butoanele nav se
  string realmente sub 1920px (padding mai mic + gap mai mic) — toate tab-urile incap fara scroll
  (verificat 1440-1920px pe instanta live).

## [2.0.10] — 2026-11-06

### Reparat
- **Modalele sunt dialoguri accesibile**: toate ferestrele suprapuse (mască, heatmap, regiune, zonă,
  numărare, dosare, rezumat, clip, poză, adaugă cameră, HD) au acum `role="dialog"`, `aria-modal="true"`
  și un nume accesibil (`aria-labelledby` la titlul lor), iar la deschidere focusul intră în dialog —
  cititoarele de ecran le anunță corect, iar navigarea cu tastatura începe în modal (Esc le închidea deja).
- **Accesibilitate + UX (audit UI)**: thumbnail-urile de eveniment din Redare și poster-ul fișei HD au
  acum `alt` (erau fără atribut → cititoarele de ecran citeau URL-ul); câmpurile fără etichetă vizibilă
  au `aria-label` (nume la înrolarea feței `#fc-name` + „cine e?", căutarea din Jurnal `#log-q`); iar
  `/api/thumb/<cam>` pentru o cameră offline întoarce un placeholder SVG „fără semnal" (200) în loc de
  502 — gata cu imaginea stricată și eroarea din consolă. (Audit Playwright: 8 ecrane principale + 9
  panouri Setări + AI + fișă × desktop/mobil; zero overflow, zero erori de consolă.)
- **Antetul încape fără scroll orizontal pe desktop**: pe lățimi sub ~1920px, antetul se compactează ușor
  (butonul „Adaugă cameră" rămâne doar „+", căutarea se plafonează, bara de puls dispare) ca toate
  tab-urile principale (Live…Stare) să fie vizibile fără a derula nav-ul; compactarea mai strânsă începe
  acum de la 1550px (de la 1500px).
- **Cursa de import `transformers` — rezolvare completă**: numele lazy se rezolvă acum în firul
  PRINCIPAL, înainte de pornirea serverului HTTP al nvr-ai (deci înainte ca primul enqueue de la web
  să poată porni worker-ul de indexare). Fix-ul din 2.0.9 o făcea în firul de modele, concurent cu
  worker-ul, și încă lăsa o apariție `cannot import name 'AutoModel'` la pornire. Benign (auto-reluat),
  dar acum eliminat la sursă.

## [2.0.9] — 2026-11-06

### Schimbat
- **Mai puține cereri de verdict „necunoscut" pe Telegram**: candidatul „necunoscut" (o cerere de
  verdict, nu o alarmă) pleacă acum pe un cooldown separat, mai lung (`scena_telegram_cooldown_necunoscut`,
  implicit 900 s), nu pe cel al detecțiilor numite (300 s) — mai puțin spam, fără a întârzia alarmele
  numite (person/car/…). (Neutrele — ploaie/umbră/frunze — tot nu pleacă; „necunoscut" noaptea/pe ploaie tot e sărit.)
- **Mai puțin zgomot de scenă pe vreme rea + noapte**: candidații de scenă clasificați „necunoscut"
  (mai ales artefacte de ploaie/reflexe) nu se mai jurnalizează și nu se mai notifică în acel regim —
  doar se numără (`necunoscut_tacut` în `stare.json`). Clasificarea rămâne, deci orice iese real
  (person/car/…) trece normal, iar YOLO prinde oamenii independent de scene-change. (cam14 făcea
  ~1400 „necunoscut"/noapte-ploaie direct în jurnal.)

### Reparat
- **Cursa de import `transformers` la pornirea nvr-ai**: numele lazy (`AutoModel`, `AutoProcessor`,
  `AutoModelForImageTextToText`, CLIP) se rezolvă acum eager, într-un singur fir, înainte de firele
  care le importă concurent (worker-ul de indexare semantică, describe, scena_lume) — elimină
  `cannot import name 'AutoModel' from 'transformers'`. Era benign (reluat de 4× și auto-recuperat),
  dar amâna ~10s indexarea semantică a unui lot la restart. Doar clase, fără greutăți → nu atinge
  ordinea bf16 de încărcare a modelelor.

## [2.0.8] — 2026-11-06

### Reparat
- **Golurile de înregistrare nu mai ascund marginea live** (Lot C C3): `goluri()` taie acum capătul
  segmentului în curs de scriere la `now` (simetric cu `coverage()`), nu la `t + SEGMENT` (în viitor) —
  un interval cerut dincolo de prezent nu mai pare acoperit, iar filigranul/timeline-ul nu se mai
  desincronizează peste marginea live.
- **Cadrele invalide de la go2rtc nu mai intră în detecție** (Lot C C4): `_fetch_frame` întoarce un
  cadru doar dacă e decodabil și de dimensiune rezonabilă (≥ 32×32). go2rtc poate răspunde 200 cu un
  corp non-JPEG/trunchiat când fluxul e picat (cam6 își pierde mainstream-ul); înainte un astfel de
  „cadru" ajungea mai departe la detecție/LPR/fețe.

## [2.0.7] — 2026-11-06

### Reparat
- **LPR (numere) se declanșează și din tiling, nu doar din substream**: camerele de stradă văd
  vehiculele mai ales prin calea de tiling (mici/departe în cadru), iar LPR pornea doar din `process`
  (substream), pe un vehicul proaspăt confirmat. De când detecțiile s-au mutat pe tiling (~1-2 nov),
  majoritatea mașinilor nu mai declanșau nicio citire — de aici „nu mai detectăm numere". Acum un
  vehicul proaspăt prins prin tiling trimite același job LPR (același throttle `ACTIVE_TTL`, partajat
  cu substream-ul → fără dublare; `plates.process` are cooldown propriu per placuță). Modelul ALPR și
  cadrul HD erau intacte (confirmat: 11/12 crop-uri istorice recitite) — regresia era pierderea
  oportunităților de citire.

## [2.0.6] — 2026-11-06

### Reparat
- **Memoria semantică a scorerului (B2) supraviețuiește verdictelor**: re-sincronizarea pozițională
  de după fiecare verdict (`scena_web._scorer_sync` → `incarca_din_feedback` fără `embed_fn`) păstrează
  acum embeddings-urile deja scrise (match pe `(cam, poza)`), în loc să le șteargă odată cu restul
  memoriei camerei. Fără asta, veto-ul semantic activat pe o cameră (`scorer_semantic`) redevenea inert
  — pur pozițional — la primul verdict următor. Un backfill de embeddings rămâne persistent; verdictele
  ulterioare actualizează doar partea pozițională, iar detecțiile noi fără embedding rămân poziționale.

## [2.0.5] — 2026-11-05

### Reparat
- **Scorerul de falsuri își urmărește feedback-ul** (B4/B5): după fiecare verdict, memoria
  (`vectori_fp`) se re-sincronizează din `feedback.jsonl` pentru acea cameră (în fundal); înainte
  nimic nu o actualiza automat. Dedup după `id` (ultimul verdict al unei detecții contează), ca
  verdictele repetate să nu umfle numărătoarea. Reîncărcarea e atomică (o tranzacție, rollback la
  eroare), nu ștergeri + inserări care puteau lăsa memoria goală.
- **Transportul de evenimente web↔AI e idempotent și corect datat** (Lot C): fiecare eveniment
  primește la sursă un id stabil (`_eid`), deci un retry din spool (sau un răspuns HTTP pierdut) nu
  mai duplică jurnalul și nu mai repetă acțiunile (sirenă/MQTT/webhook/push/Telegram). Ora originii
  (`_origine`) datează evenimentul la producere, nu la recepție — cele recuperate după o pană nu mai
  apar la ora revenirii, iar severitatea zi/noapte e corectă. Eroarea de persistență nu mai e
  înghițită tăcut.

### Documentație
- Sincronizare cu PostgreSQL: README, `docs/operare.md`, `docs/ai-contract.md`, `docs/nvr-ai.md` și
  manualele EN/RO nu mai descriu SQLite (`events.db`/`segmente.db`); `data/` are datadir-ul PG în
  `data/pg/`. `server_main.py`: fișierul e rulat de entrypoint-ul containerului (Docker Compose).

## [2.0.4] — 2026-11-05

### Reparat
- **Tiling-ul nu mai ocolește filtrele de eligibilitate** (scorer/detecție, Lot B): un singur gate
  comun (noapte/meteo/margine/**zonă**/**învățat**) pe box-uri normalizate în cadrul întreg, aplicat
  identic pe substream și pe calea de tiling (`sursa="regiune"`). Înainte, un fals suprimat pe
  substream — sau un obiect în afara zonei — tot producea eveniment + notificare prin tiling.
- **Un om real nou nu mai poate fi suprimat** într-o celulă IR învățată ca fals: suprimarea = candidat
  pozițional **și** fără veto semantic. Dovada că detecția e reală (seamănă cu un exemplar „ok", sau
  nu seamănă cu niciun fals învățat) **blochează** suprimarea (embedding SigLIP la live, în spatele
  flag-ului per-cameră `scorer_semantic`, implicit oprit). Invariantă: suprimarea nouă e un subset
  strict al celei vechi, deci nu se pot rata în plus detecții reale.
- **Scorerul nu mai întârzie detecția tuturor camerelor** când PostgreSQL e degradat: cache pe celule
  + circuit breaker + `statement_timeout` scurt; la eroare, fail-open (nu suprimă). Un „ok" proaspăt
  ridică imediat mutul (invalidarea celulei la scriere).

## [2.0.3] — 2026-11-05

### Reparat
- **Restore atomic** (`backup_nocturn`): refacerea validează toate CSV-urile întâi, apoi
  TRUNCATE + COPY într-o singură tranzacție — o cădere la mijloc sau un CSV invalid lasă baza
  neatinsă (rollback), nu pe jumătate restaurată.
- **Backupul eșuează explicit** la orice eroare PostgreSQL, în loc să rotească peste copiile bune
  o arhivă incompletă. Dump consistent dintr-un singur snapshot (`REPEATABLE READ READ ONLY`) și
  ordonat după cheia IDENTITY; manifestul notează rândurile per tabel (`pg_randuri`) și `complet`.
- **Lanțul de custodie rămâne valabil după re-encodare**: `lant` reține derivarea (coloane noi
  `sha256_orig` + `recodat`, migrarea 2), iar `reencode` actualizează lanțul *înainte* de a șterge
  originalul. La arhivare, registrul se scrie tot înainte de ștergerea sursei.
- **Un `users.json` corupt sau gol nu mai deschide `/setup`** (altfel oricine putea crea un admin
  fără autentificare): `setup_permis()` distinge „fișier absent” de „corupt” → blocaj 503, nu
  prima pornire.
- **Migrările de schemă nu se mai aplică concurent**: advisory lock PostgreSQL per modul, ca cele
  patru procese (go2rtc/web/record/ai) care pornesc deodată să nu ruleze aceeași migrare în paralel.

## [2.0.2] — 2026-11-04

### Reparat
- Testele rulate în imaginea Docker (pasul „Teste in imagine” din release, fără un server
  Postgres) se blocau ~30 s per test: `conftest` verifica PostgreSQL prin pool la fiecare test,
  iar `.connection()` aștepta timeout-ul pool-ului. Acum verificarea PG se face o singură dată
  pe sesiune (cache), iar `db.ping()` se conectează direct cu `connect_timeout` scurt (fail-fast);
  fără Postgres toată suita se sare în câteva secunde. Pool-ul primește și `connect_timeout=10`.

## [2.0.1] — 2026-11-04

### Reparat
- Imaginea Docker nu includea driverul PostgreSQL: `psycopg` / `psycopg-binary` / `psycopg-pool`
  și `pgvector` erau în `requirements.txt` și `requirements-ci.txt` (deci CI-ul trecea), dar
  lipseau din `requirements.lock`, din care se construiește imaginea (`--no-deps --require-hashes`).
  Adăugate cu hash-uri; fără ele, serviciile pe 2.0.0 nu porneau (`No module named 'psycopg_pool'`).

## [2.0.0] — 2026-11-04

### Adăugat
- Migrarea pe **PostgreSQL + pgvector** (2.0.0) — fundația (Faza 0): serviciul `db`
  (`pgvector/pgvector:pg16`, doar pe `127.0.0.1`, datadir sub `DATA_DIR`), modulul `app/db.py`
  (pool de conexiuni, `conn()`/`tx()`, `aplica()` în tiparul lui `schema.py`) și un Postgres în
  CI cu test de fum. Nu mută încă niciun tabel — plan complet în `docs/ro/postgres-migrare.md`.
- PostgreSQL, Faza 1: `push` (abonamente), `jobs` (coada) și `marcaje` (bookmarks) mutate din
  SQLite pe PostgreSQL prin `app/db.py` (nu mai împart baza `events.db` cu jurnalul). Script de
  migrare a datelor one-shot (`scripts/migrare_pg.py`, idempotent) și teste pe Postgres.
- PostgreSQL, Faza 2: **jurnalul de evenimente** (`app/jurnal.py`, tabelul `events`) mutat pe
  PostgreSQL. Contractul `query()`/`insert()` neschimbat pentru restul sistemului. Căutarea de
  text folosește acum `ILIKE` (în PostgreSQL `LIKE` e sensibil la litere mari), ordinea în
  aceeași secundă vine din coloana `seq` (IDENTITY) în locul `rowid`-ului, iar migrarea veche din
  `events.jsonl` a fost scoasă (datele istorice se mută cu `scripts/migrare_pg.py`, care acum
  include și `events`).
- PostgreSQL, Faza 3: **lanțul de custodie** (`app/lant.py`, tabelul `segmente`) mutat pe
  PostgreSQL — scris de nvr-record, citit de nvr-web, acum peste aceeași bază (fără WAL între
  procese). `scripts/migrare_pg.py` mută și `segmente` și **verifică** după migrare că fiecare
  amprentă SHA-256 a ajuns neschimbată (lanțul rămâne valabil); re-hash-ul fișierelor de pe disc
  rămâne disponibil prin `verifica()`/fișa probatorie.
- PostgreSQL, Faza 4a: **scorerul de falsuri** (`app/scorer_fp.py`) — memorie spațială + semantică
  a verdictelor, în `pgvector` (tabelul `vectori_fp`, SigLIP 768-dim). Scor cu doi termeni
  (rata de fals pe pătrățel+etichetă+regim + similaritate cosinus la exemplarele „greșit” vs „ok”),
  cu plasa de siguranță `ok==0`. Motorul + testele; cablarea în bucla de detecție și migrarea
  fețe/re-ID/scenă în pgvector urmează.
- PostgreSQL, Faza 4b: scorerul **cablat în bucla de detecție** (`detect.CamState._mut_invatat`,
  `filtru="invatat"` lângă filtrul de zonă) — opt-in per cameră (`scorer_fp` în config) și
  fail-safe (orice eroare → nu suprimă, alarma tot sună).
- PostgreSQL, Faza 5: **gata fără SQLite.** Zero `sqlite3` în codul aplicației — `rules` prinde
  erori generice de jurnal (nu `sqlite3.Error`), `backup_nocturn` scrie baza în arhivă ca
  `pg/<tabel>.csv` (dump logic prin `COPY`, fără `pg_dump`) și o reface cu `incarca_pg()`,
  `app/schema.py` (migratorul SQLite, nefolosit) a fost șters. Fețe/re-ID/scenă rămân pe fișiere
  (nu folosesc SQLite). Migrarea fețe/re-ID/scenă în `pgvector` (4c) rămâne opțională, pentru când
  apare nevoia de căutare vectorială la scară. Cutover-ul datelor: `scripts/migrare_pg.py`.

### Reparat
- Aplicarea unui model antrenat local din interfață (`/api/train/apply`) dădea 500: decoratorul
  de rută stătea pe funcția-ajutor `_swap_formal(sursa)`, nu pe handler. Mutat pe
  `api_train_apply`; revenirea (`/api/train/revert`) nu era afectată.

## [1.1.0] — 2026-11-02

**Highlights (English).** "Learn the scene" — per-camera change detection that learns each
camera's background and classifies only what is new, with an editable open vocabulary
(SigLIP + YOLO), on-demand deep analysis (YOLO-World + Qwen2.5-VL), and verdict buttons both
in the camera card and on Telegram — for scene candidates *and* for YOLO detections — that feed
the training set and build per-camera learned examples. Outdoor and camera-estimated **weather**
on every event, with a bad-weather detection threshold. **Per-camera recommendations** applied
with one click. A configurable **language model** (the local Qwen or an OpenAI-compatible
server) and an **MCP server** so an assistant can ask the system what happened. Recordings,
the archive tier and the nightly backups can each live on a separate disk. The Romanian
detail follows.

### Adăugat
- „Învață scena” (în probă, per cameră, din fișa camerei): camera își învață fundalul și
  clasifică doar ce e nou, pe decupaj din fluxul HD, cu vocabular deschis editabil din
  interfață (SigLIP) plus YOLO; candidații apar în fișă cu butoane ✓ / ✕ / ? / 🔍, iar
  verdictele se strâng în `data/scena/feedback.jsonl` pentru antrenamentul următor.
  Opțional, candidații pleacă pe Telegram ca poză cu butoane (✓ corect, ✕ ce e de fapt,
  🔍 reanalizează); 🔍 cere o analiză adâncă (YOLO la 640, tot vocabularul, modelul de limbaj
  Qwen2.5-VL) și răspunde în același chat. Evenimente noi: `scena_candidat`, `scena_analiza`.
  Vocabularul trece și prin YOLO-World (detector cu vocabular deschis, `yolov8m-worldv2`,
  descărcat la prima pornire în `data/modele/`): vede obiectul din decupaj, nu fundalul, și
  câștigă când YOLO-ul antrenat nu e sigur. Numele românești primesc forma engleză pentru
  CLIP din dicționarul interfeței, din „nume = english name” scris în vocabular sau printr-o
  traducere memorată; setarea `scena_lume` îl oprește. În analiza adâncă, eticheta modelului
  de limbaj e crezută doar dacă o confirmă YOLO-World, SigLIP sau YOLO-ul antrenat; altfel
  rămâne afișată ca neconfirmată și decide restul.
  Pe Telegram, „necunoscut” pleacă doar ziua și pe vreme bună (noaptea e granulație de
  infraroșu, pe ploaie sunt picături), iar noaptea pleacă doar etichetele date de un detector
  cu încredere de cel puțin 0,5; fiecare decizie de trimitere sau sărire e în jurnalul de
  sistem. Textul analizei adânci arată din vocabular doar numele care ies în față, nu
  trei nume oricum.
- Verdictele din „Învață scena” intră în datasetul de antrenament (Setări → Unelte → Model):
  decupajele confirmate devin exemple pozitive, cu chenarul obiectului pus de YOLO-World,
  iar „e doar ploaie / umbră / frunze” devin fundaluri fără obiecte; numele noi din vocabular
  cu destule exemple devin clase noi (`data/finetune/clase.json` ține id-urile stabile).
  Antrenamentul se poate porni pe yolo11m sau yolo11l și e evaluat automat pe clase, pe
  setul de validare fix (`scripts/evalueaza.py`, `runs/<nume>/evaluare.json`), iar
  rezultatele apar în cardul de antrenament.
- Recomandări per cameră, în fișa camerei, deduse din ce a văzut sistemul (jurnal, candidați,
  verdicte, harta de instabilitate) și aplicabile cu un clic: detecție pe regiuni HD când
  obiectele sunt mici, zonă de detecție sub banda instabilă (copaci, cer), prag de încredere
  mai mare noaptea când ai respins „om” de mai multe ori (reglaj nou, per cameră:
  `conf_noapte`), pornirea urmăririi sau a scenei, nume noi în vocabular. Aplicările se
  auditează. Recomandarea „zonă fierbinte” vede când candidații scenei se îngrămădesc
  într-un petic mic al cadrului (o tufă în vânt, o creangă în infraroșu, granulație pe
  gazon) și propune o zonă de detecție care îl lasă afară.
- Zona de detecție a camerei (desenată în fișa ei) e respectată și de „Învață scena”: ce e
  în afara ei nu devine candidat și nu consumă clasificări.
- Decupajele scenei intră în indexul căutării semantice, cu vectorul SigLIP deja calculat
  la compararea cu vocabularul (fără GPU în plus): „mașină albă” sau „om cu umbrelă” găsește
  și ce a văzut doar scena, nu și YOLO; în rezultate sunt marcate 🧩. Ploaia, umbrele și
  frunzele nu intră. Decupajele scenei au retenția pozelor (`retention_snapshots_days`),
  cu excepția celor cu verdict, care rămân pentru antrenament.
- Căutarea din antet caută și în text, fără diacritice: descrierile modelului de limbaj
  („mașină albă”), rezumatele episoadelor, nume, numere (`/api/journal?text=&zile=`).
  Verdictul omului pe un candidat ajunge și în indexul căutării (eticheta nouă, sau rândul
  iese dacă era doar fundal). Pragul de noapte al candidaților trimiși pe Telegram e setare
  (`scena_telegram_noapte`, Setări → Locație și vreme) și poate fi per cameră, din
  recomandări (`scena_noapte`), când ai respins de mai multe ori etichete de noapte. Pe
  vreme rea scena cere de trei ori mai multe cadre până la clasificare și înjumătățește
  bugetul de clasificări.
- Exemple învățate din verdicte, per cameră: orice verdict cu nume face din decupajul
  candidatului un exemplu al camerei (vectorul SigLIP, `data/scena/<cam>/fundal_invatat.*`).
  Numele de fundal („nimic”, „frunze”, „umbră”, „ploaie”, „copac”) se potrivesc la peste 0,85
  și bat orice etichetă: candidatul nu pleacă pe Telegram și nu intră în index. Numele de
  obiecte (✓ pe eticheta propusă sau ✕ cu numele adevărat: „găină”, „mașină”…) se potrivesc
  la peste 0,82 și înlocuiesc doar „necunoscut” și scorurile mărunte ale vocabularului, nu
  eticheta unui detector (sursa „învățat din verdicte”). Câștigă cel mai apropiat exemplu care
  își trece pragul. Pragurile, măsurate pe instalație: fundalurile seamănă între ele la ~0,88,
  găinile fără nume cu cele numite la ~0,87, iar prelata și pământul ating cel mult 0,82 față
  de găini. Exemplele se refac din `feedback.jsonl` la pornirea nvr-ai. În fișa camerei,
  necunoscuții și fundalul sunt ascunși implicit, cu un comutator.
- Butoane ✓ ✕ ∅ și sub notificările de detecție YOLO de pe Telegram (🚨 om / câine / pisică /
  urs), nu doar sub candidații scenei. Verdictul intră în același `feedback.jsonl`, cu
  decupajul din chenarul detecției (sidecarul snapshotului) salvat în `data/scena/<cam>/`:
  ✓ devine exemplu de obiect al camerei, ∅ exemplu de fundal, ✕ deschide vocabularul; toate
  intră în datasetul de fine-tune și în recomandarea de prag de noapte. Mesajele fără snapshot
  (cooldown-ul pozelor) rămân fără butoane.
- Server MCP (`POST /mcp`, cu jetonul API ca `X-Token` sau `Authorization: Bearer`): un
  asistent poate întreba sistemul ce s-a întâmplat (evenimente cu filtru de text, episoade,
  candidați), căuta în poze, cere cadrul unei camere, vedea recomandările și comuta modul.
  Puntea stdio `scripts/openvigil_mcp.py` pentru Claude Desktop / Claude Code; `docs/mcp.md`.
- Model de limbaj configurabil (`llm.py`): Qwen-ul local din nvr-ai sau, opțional, un server
  compatibil OpenAI (Ollama, LM Studio, vLLM) din Setări → Unelte, cu buton de test.
  Rezumatele episoadelor de prezență pot fi scrise de el (o propoziție, numai din fapte;
  șablonul rămâne ca `text_sablon`). Jurnalul se poate filtra după text.
- Vremea (Setări → Unelte → Locație și vreme): localitatea se caută după nume (geocodare
  Open-Meteo, fără cheie), vremea de afară se citește la 5 minute și se combină cu ce
  estimează camerele (ploaie din cadrele sărite ale scenei, vânt din zonele instabile, ceață
  din contrastul fundalului). Fiecare eveniment din jurnal primește câmpul `vreme`; un prag de
  încredere opțional (`vreme_prag`) se aplică detecției pe ploaie, ninsoare, ceață sau
  furtună; pe vreme rea candidații „necunoscut” nu mai pleacă pe Telegram (comutabil).
  Unealta MCP `vreme`; `data/vreme.json` e citit de nvr-ai fără rețea.
- Verdict nou în „Învață scena”: „∅ Nimic nou (doar fundal)”, pe Telegram și în fișă; „copac”
  în vocabularul implicit ca nume neutru.
- Întrebările în limbaj natural („when did the garbage truck come?”) merg și în engleză, cu
  răspunsuri în limba interfeței.
- `RECORDINGS_DIR`, `ARHIVA_DIR`, `BACKUP_DIR`: înregistrările, nivelul de arhivă și copiile
  nocturne pot sta pe alt disc decât restul datelor (bind-uri separate).

### Reparat
- Modelul de limbaj (descrieri de persoane/vehicule, analiza „ceva nou”) pica în imaginea
  Docker cu „Failed to find C compiler”: torch 2.13 trecea unele operații prin nuclee Triton,
  care cer un compilator. `TORCH_DISABLE_NATIVE_JIT=1` în imagine ține torch pe nucleele clasice.
- După descărcarea modelului de limbaj, torch rămânea cu ~8 GB de VRAM rezervați dar
  nefolosiți (fragmentare), iar următoarea încărcare pica cu „VRAM insuficient”.
  `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` în imagine (se pierduse la mutarea de pe
  systemd). Raportul `/capabilities` al nvr-ai arată acum VRAM-ul pe modele și fragmentarea.

- Sidecar-ul cu chenare al pozelor de detecție (`<poză>.json`, folosit de „găsește-l pe toate
  camerele” ca să taie omul exact din chenarul momentului) nu se scria niciodată: lipsea un
  import în `snaps.py`, iar eroarea era doar în jurnal („sidecar nescris: name 'store' is not
  defined”). Test de regresie.

## [1.0.0] — 2026-10-26

Prima versiune publicată.

### Adăugat
- Release-uri cu imagine publicată în GitHub Container Registry (`ghcr.io/sm26449/openvigil:1.0.0`,
  `:latest`; `:edge` din `main`); `install.sh` trage imaginea în loc să o construiască
  (`--build` pentru construcție locală). Versiunea apare în Setări → Stare, în meniul de pe
  telefon, în `/api/me` și `/healthz`.
- Versiune de schemă în bazele SQLite (`app/schema.py`, tabela `schema_versiuni`): migrările
  viitoare rulează o singură dată, în ordine. Secretele din `.env` pot veni din fișiere
  (`NVR_PASS_FILE`, `TELEGRAM_TOKEN_FILE`).
- Interfață multilingvă: română și engleză. Limba urmează browserul, poate fi fixată pe cont
  (Contul meu → Limba) sau din `?lang=`; `NVR_LANG` dă limba serverului (notificări, rezumat).
  Cheia unui text e textul românesc (`t()` în Python și JavaScript, `app/i18n.py`), pagina
  e tradusă pe server; dicționare JSON în `app/static/i18n/`, suprascrieri în `data/i18n/`;
  `scripts/i18n_check.py` verifică acoperirea.
- Mod demo: patru camere sintetice (vendor `demo`, go2rtc `ffmpeg:virtual`), din Setări → Unelte;
  fără acțiuni, fără rețea, înregistrate doar prin go2rtc.
- Site-ul proiectului: [openvigil.dev](https://openvigil.dev), servit de pe mașina noastră publică
  (`deploy/site/`), nu de pe GitHub Pages.
- CI pe GitHub Actions: pytest fără GPU (`requirements-ci.txt`), verificarea modulelor ES,
  shellcheck, validarea fișierelor Compose, căutarea de secrete; build-ul imaginii cu testele
  rulate în container (săptămânal și la schimbări de Dockerfile/lock). Template-uri de issue și PR.
- `docs/operare.md`: jurnale, actualizare, copii, porturi, GPU, dezinstalare; secțiuni de
  depanare în README.
- Repo-ul public OpenVigil, pornit de la zero din instalația de origine: configurare prin
  variabile de mediu (`config.py` + `.env`), o singură imagine Docker pentru web, record, ai
  și go2rtc, `docker-compose.yml` (+ suprapunerea `docker-compose.gpu.yml`), `install.sh`
  cu provizionarea Docker și a nvidia-container-toolkit, descărcarea modelelor la prima
  pornire (`scripts/modele.py`), licență AGPL-3.0 și `NOTICE.md` cu licențele modelelor.

### Schimbat
- Lanțul de custodie memorează căi relative la rădăcina proiectului (rândurile vechi se migrează).
- Imaginea Docker se construiește într-o singură etapă, cu lock-ul instalat `--no-deps`; testele
  sunt în imagine (`docker compose run --rm --no-deps web test`).
- Numele sistemului e o setare (`NVR_NAME`), nu un text fix.
- Criptarea pe disc nu mai e gestionată din aplicație: se face la nivelul gazdei (LUKS /
  fscrypt). Aplicația doar sesizează și așteaptă când directorul de înregistrări nu e scriibil.
- `ai_service` pornit implicit: inferența rulează în containerul `ai`.
