# Învățarea căutării din interfață

Administratorul poate deschide **Învață căutarea** atât din rezultatele căutării globale,
cât și din galeria de alarme. Descrierea este precompletată; pentru o întrebare se folosește
conceptul căutat. Funcția necesită backendul semantic PostgreSQL activ.

## Fluxul de lucru

1. Alege descrierea, camera și selecția. Lista descrierilor sugerează conceptele deja marcate.
2. Verifică imaginea; apăsarea ei deschide o vedere mărită.
3. Alege **Corect**, **Greșit** sau **Neclar**. Verdictul se salvează imediat pe server.
4. Opțional, scrie o observație și apasă **Salvează observația**. Alegerea unui verdict
   salvează și observația curentă.
5. Revino în **Exemple salvate** pentru modificare sau **Elimină exemplul** pentru anulare.

Selecțiile au scopuri diferite:

- **Candidați de verificat**: până la 100 de rezultate ale modelului, în perioada și camera
  selectate, înaintea corecțiilor umane. Pot apărea și exemple deja respinse, pentru reevaluare.
- **Exemple salvate**: exemplele acestui concept, inclusiv cele mai vechi decât perioada
  candidaților; filtrul de cameră se aplică în continuare. Imaginea copiată rămâne disponibilă.
- **Din arhivă, după zi**: imagini indexate în ordine cronologică, fără selecție după scorul
  conceptului. Navigarea este în pagini de 100. Acest mod poate găsi exemple omise din top-k;
  nu reprezintă analiza întregii înregistrări sau a imaginilor care nu au fost indexate.

Schimbarea descrierii cere reîncărcarea selecției înaintea altui verdict. Erorile de salvare
sunt afișate, iar un verdict eșuat nu apare ca salvat.

## Ce învață aplicația

Memoria este pentru **concept + imagine + versiunea imaginii + model**:

- **Corect** recuperează exemplul cunoscut la căutarea conceptului chiar dacă scorul modelului
  este sub prag. În căutarea de imagini, exemplele confirmate apar primele. În răspunsurile
  despre treceri, ele trec de prag și primesc marcajul de confirmare.
- **Greșit** exclude exemplul pentru acel concept înaintea limitării rezultatelor.
- **Neclar** păstrează observația pentru revizuire, fără să promoveze sau să excludă exemplul.
- **Elimină exemplul** șterge memoria pentru acel concept și imagine.

Perioada, camera și drepturile de acces se aplică și exemplelor confirmate. O imagine
ștearsă din index nu este reintrodusă în rezultatele curente. Dacă pixelii au fost înlocuiți,
verdictul vechii versiuni nu se aplică noii intrări. Camera și timpul rămân cele ale capturii;
un exemplu confirmat nu devine o trecere nouă.

Majusculele, diacriticele, punctuația și spațiile se normalizează în cheia conceptului.
Sinonimele sau traducerile diferite nu sunt unite automat. Un camion poate fi corect pentru
„camion” și greșit pentru „mașină de gunoi”. Eticheta detectorului nu este schimbată de verdict.

Aceasta este memorie explicită pentru exemple cunoscute. Nu reantrenează ponderile SigLIP
și nu declară automat corecte alte imagini asemănătoare. Recunoașterea unor exemple noi,
verificarea VLM selectivă și antrenarea unui model necesită validare separată. Numărătoarea
bazată pe clase și jurnal nu este transformată prin aceste marcaje în numărătoare vizuală completă.

## Persistență și operare

`app/semantic_feedback.py` administrează schema `semantic_feedback`, creată prin mecanismul
obișnuit de migrări. Fiecare exemplu păstrează conceptul, verdictul, observația, autorul,
metadatele, amprenta și vectorul normalizat de 768 de dimensiuni. Imaginile marcate se copiază
atomic în `data/semantic-feedback/`, cu identificatori hash. Ele rămân după retenția sursei,
pentru consultare și dezvoltarea ulterioară a unui dataset.

Eliminarea ultimei referințe la o imagine o scoate și din directorul exemplelor. Aceeași
imagine folosită de două concepte se păstrează până la eliminarea ambelor exemple.
Modificarea unui exemplu păstrat rămâne posibilă și după expirarea vectorului din index.
Backupul aplicației include tabelul PostgreSQL și directorul imaginilor păstrate.
Imaginile sunt capturate sub aceeași blocare scurtă care fixează snapshotul relațional.
Copierea îndelungată a celorlalte tabele și vectorilor continuă după eliberarea blocării,
astfel încât editările din UI să poată continua fără a produce referințe fără imagine în backup.

Rutele `/api/semantic/review`, `/api/semantic/review/feedback` și imaginile păstrate din
`/api/semantic/review/image/<id>` sunt pentru administrator. POST/DELETE folosesc CSRF.
Observațiile și autorul nu se publică prin răspunsurile normale de căutare pentru vieweri.

Corecțiile sunt citite din PostgreSQL la cerere, fără repornirea AI și fără reindexare GPU.
Selecția candidaților folosește modelul deja încărcat în serviciul AI; arhiva și exemplele
salvate pot fi consultate fără inferență nouă.

## Validare

`tests/test_semantic_feedback.py` verifică recuperarea sub prag, filtrarea negativelor înainte
de top-k, izolarea pe concept/cameră/perioadă, retragerea verdictelor, versiunile imaginilor,
păstrarea datelor după retenție, confidențialitatea observațiilor și backup/restaurare.
Un test concurent șterge un exemplu după fixarea snapshotului și verifică atât continuarea
editărilor, cât și păstrarea imaginii și a verdictului corespunzător în copia de siguranță.
`tests/ui-semantic-learning.js` verifică modulele reale în browser, inclusiv erori de salvare,
redeschidere, observații, anulare, navigarea arhivei, schimbarea descrierii, XSS, telefon și roluri.

Starea instalării și rezultatele măsurate sunt în [semantic-status.md](semantic-status.md).
