# Starea lucrului la căutarea semantică

Actualizat: 2026-11-14. Acest fișier este un jurnal de predare; starea efectivă a
backendului se citește din aplicație, nu se deduce din documentație.

## Instalație verificată

- Web și AI rulează `2.1.3-local.4`; înregistrarea și baza sunt healthy.
- PostgreSQL este activ pentru căutare; coada este persistentă, iar oglindirea NPZ este oprită.
- Verificarea de activare a comparat exact **829.525 vectori în 92 de zile** și metadatele lor,
  toate identice. După curățarea celor două zile expirate fără surse, indexul activ are 90 de zile.
- Imaginile noi continuă să intre, deci numărul curent poate depăși reperul verificat.
- Ramura de lucru: `fix/cautare-semantica-20261113`. Nu s-a publicat această etapă.
- Spațiu liber pe discul sistem la verificare: aproximativ 13 GB; migrarea, WAL-ul,
  exporturile și restaurarea de test trebuie planificate în acest spațiu.

## Finalizat în etapa instalată

- Filtre calendaristice locale, camere, ore inclusiv peste miezul nopții și prima/ultima apariție.
- Întrebări din bara globală și galerie, cu aceleași reguli de autorizare.
- NPZ atomic pe zile, citire NPY/JSON istorică, deduplicare și loturi de scriere grupate.
- Reconciliere periodică și raportarea acoperirii imaginilor indexate.
- Eticheta declanșatoare nu exclude alte obiecte din snapshot; clasa filtrează decupajele.
- Comparație izolată NumPy/pgvector pe vectorii existenți, fără recalculare GPU.

Comparația anterioară a inclus 303.945 vectori din 30 de zile: 15/15 interogări au avut
rezultate echivalente; timpii mediani observați au fost aproximativ 2,46 s și 1,23 s.
Este o măsurătoare locală, nu o garanție de latență și nici o evaluare a preciziei vizuale.
La inventarul anterior, indexul complet avea aproximativ 838.000 de vectori în 91 de zile.
Acestea sunt repere istorice; importul final trebuie să raporteze numerele actuale.

## Implementat și instalat în etapa PostgreSQL

- `app/semantic_store.py`: vectori pgvector, control explicit de backend, identitate de model,
  coadă persistentă, lease, generații, retry, erori permanente, tombstone și retenție.
- `app/semantic.py`: integrarea workerului, reconciliere, filtrare SQL înainte de top-k,
  oglindire temporară NPZ și blocare coordonată pentru comutare.
- `scripts/semantic_migrate.py`: prepare, import reluabil, verificare integrală,
  activare condiționată de egalitate, export verificat și rollback fără GPU.
- `app/backup_nocturn.py`: vectori prin COPY binar, restaurare într-o singură tranzacție,
  încărcarea părinților înaintea tabelelor dependente și reluarea joburilor restaurate.
- Pornirea workerului după încălzirea modelelor și actualizarea indexului la schimbarea surselor.

## Validare încheiată

- Suita backend: **600 teste trecute**, pe PostgreSQL de test izolat.
- Testele țintite de stocare și backup: **22 trecute**; verificările statice trec.
- În timpul validării s-a descoperit că restaurarea golește separat tabele legate prin FK.
  Corecția golește tabelele împreună și le încarcă în ordinea dependențelor; testele de
  restaurare validă și rollback la arhivă deteriorată trec după corecție.
- Verificarea diferențelor Git nu raportează erori de whitespace.
- Importul inițial a copiat 829.473 vectori în 221,6 secunde, fără recalculare GPU;
  intrările noi explică diferența față de numărul verificat la comutare.
- Prima verificare reală a refuzat comutarea din cauza ordonării locale diferite între
  PostgreSQL și Python. Compararea după cheie a demonstrat egalitatea datelor; exportul
  folosește acum explicit collation `C`, cu test pentru nume de fișiere cu majuscule/minuscule.
  Verificarea integrală repetată a trecut în 81,16 secunde, fără relaxarea criteriilor.
- Reconcilierea lasă zilele expirate în grija retenției în bloc; nu rescrie NPZ pentru
  fiecare poză istorică dispărută. Există test dedicat.
- Verificările HTTP autentificate trec pentru perioade, prima apariție, ore, cameră,
  interval invalid (400) și cont restricționat. Rezultatele vizuale rămân candidați de evaluat.
- Restartul controlat al serviciului AI a trecut. După restart, aceleași verificări HTTP
  trec și `/api/semantic/status` confirmă `postgres`, persistență activă și 90 de zile.
- Exportul de revenire a verificat prin recitire exactă **829.552 vectori și metadatele lor
  în 90 de zile**, în 53,61 secunde, fără GPU și fără schimbarea backendului.
  Directorul temporar a fost eliminat după salvarea raportului; exportul se poate regenera.
- Copia reală criptată a fost creată pe discul local de backup: **3.250.806.604 octeți**,
  aproximativ 3,25 GB. Decriptarea, SHA-256 și structura completă a celor **829.555 rânduri**
  din COPY binar au fost verificate, inclusiv rândurile șterse logic. Operația a durat
  302,62 secunde. Restaurarea tranzacțională este verificată de suita izolată; această
  verificare reală a arhivei nu reprezintă restaurarea tuturor datelor producției într-o altă DB.
- La încheiere, serviciile sunt healthy, PostgreSQL rămâne activ, coada recuperabilă este
  drenată, iar cele 173 de imagini goale rămân erori permanente. Baza temporară de test
  și exportul temporar au fost eliminate; rapoartele și copia criptată au fost păstrate.

## Următorii pași, în ordine

1. Evaluarea vizuală pe exemple etichetate, apoi prioritizarea track-urilor, verificării VLM
   selective și alertelor semantice. Aceste funcții nu sunt declarate implementate.
2. Măsurarea și reducerea consumului RAM al backupului complet: transportul vectorilor
   este binar și folosește fișier temporar, dar arhiva și criptarea folosesc încă memoria.

Exemplele vizuale exportate nu au încă adevăr de referință complet etichetat. Nu raportăm
precizie/recall global din scorurile modelului. La reconcilierea anterioară au rămas 173 de
JPG-uri goale: sunt surse invalide, nu vectori care pot fi recuperați fără imagine.

Procedura și comenzile sunt în [semantic-search.md](semantic-search.md), iar contractul
web/AI în [ai-contract.md](ai-contract.md). Rapoartele cu imagini și date locale rămân în
zona privată de audit, exclusă din Git și din imagine; nu se adaugă în documentația publică.
