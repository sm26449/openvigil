# Căutare semantică / Semantic search

Starea etapei locale, validările și lucrul rămas sunt în [semantic-status.md](semantic-status.md).

Întrebările funcționează din bara globală și din galeria de alarme, prin `/api/search`.
Exemple: „când a trecut mașina de gunoi ieri între 8 și 10 la poartă?”,
„când a apărut prima dată mașina albă?”, „câți oameni au trecut azi?”.
Numele camerei trebuie să corespundă numelui configurat, iar camera trebuie să fie vizibilă
contului. Un nume ambiguu sau diferit de filtrul selectat produce un mesaj explicit.

„Ieri”, „alaltăieri”, săptămânile și lunile calendaristice folosesc fusul orar al instalației.
Se poate specifica o zi ISO (`2026-11-12`) și intervalul zilnic `între 8 și 10` sau
`între 22 și 2`. Sfârșitul este exclusiv. Fără perioadă explicită se folosește filtrul de
zile din interfață; răspunsul afișează limitele efective. Formele de bază funcționează și în engleză.

Rezultatul este o observație în imaginile salvate și indexate. Nu este o analiză exhaustivă
a înregistrării video. Acoperirea afișată numără snapshot-urile de detecție; decupajele scenei
sunt o sursă suplimentară. Eticheta unui snapshot nu exclude alte obiecte din imagine:
un camion cu un operator poate avea eticheta „person”. Filtrarea pe clasă se aplică
decupajelor scenei. Grupările sunt per cameră, cu durată maximă de 30 minute, și nu
reprezintă identificarea certă a aceluiași obiect. Potrivirile slabe sunt marcate și pot fi
verificate prin poze și clip. Obiectele urmărite și episoadele din jurnal sunt raportate separat:
sursele se pot suprapune, iar lipsa observațiilor nu dovedește absența unei treceri.

Backendul implicit rămâne pe disc: NPY/JSON se citesc în continuare; o zi actualizată se scrie ca
NPZ atomic. NPY/JSON vechi rămân pentru revenire, dar nu primesc intrările noi; după revenirea
la o imagine veche trebuie refăcut indexul lipsă. Nu șterge fișierele NPZ la o revenire.
Reconcilierea la cinci minute recuperează snapshot-uri și candidați ai scenei care au jurnal
și fișiere disponibile. Erorile sunt raportate în starea backfill-ului.

## Stocare PostgreSQL și indexare persistentă

Implementarea permite migrarea în PostgreSQL cu pgvector. Instalarea codului nu schimbă
automat backendul: `semantic_persistent` este implicit `false`, iar `semantic_control.backend`
este inițial `files`. Starea efectivă se verifică prin `semantic_migrate.py status` și
`/semantic/status`; existența tabelelor sau a acestui document nu dovedește activarea.

Cu persistența activată, `semantic_vectors` păstrează vectorii normalizați de 768 dimensiuni,
metadatele, camera, timpul, sursa și identitatea modelului. Cheia este
`(model, source, cam, day, fn)`. Imaginile și clipurile rămân pe disc.
Colecția istorică folosește identitatea declarată
`legacy/google/siglip-base-patch16-256-multilingual`; revizia istorică nu este cunoscută.
Revizia observată la încărcarea modelului este verificată pentru inferențele ulterioare.
Schimbarea modelului cere o strategie explicită de reindexare.

`semantic_tasks` înlocuiește coada exclusiv în memorie. Web poate înregistra lucrul în DB,
iar serviciul AI revendică loturi de 16, cu lease de 300 secunde, reluare după expirare
și verificarea generației înainte de confirmare. Un worker vechi nu poate confirma un job
înlocuit. Amprenta fișierului este `mtime_ns:size`, nu un hash al conținutului.
Erorile tranzitorii au retry cu întârziere, până la cinci încercări; imaginile invalide
primesc eroare permanentă. Un fișier schimbat poate fi reprogramat. Cererea de backfill
reia erorile recuperabile; cele permanente nu se repetă automat pentru același fișier.

Ștergerea folosește tombstone pentru a împiedica reînvierea unei intrări printr-un import
vechi. Feedbackul și redenumirile invalidează joburile anterioare. Reconcilierea periodică
recuperează sursele omise și elimină referințele la surse dispărute. Retenția curăță și
vectorii, joburile și checkpointurile zilelor expirate.

Căutarea PostgreSQL folosește scor exact de produs scalar pentru vectori normalizați.
Filtrele de cameră, perioadă, ore și clase eligibile se aplică înainte de limită.
Nu există încă un index ANN/HNSW activ; o optimizare aproximativă cere măsurarea pierderii
de rezultate. Alegerea PostgreSQL rezolvă persistența și administrarea; precizia vizuală
depinde în continuare de model și de imaginile salvate.

### Migrare și verificare

Comenzile se execută din rădăcina aplicației, cu Python și configurarea serviciului AI.
Salvează rapoartele într-un director privat, persistent, din afara repository-ului public.
Nu folosi baza de producție pentru pytest.

```bash
venv/bin/python scripts/semantic_migrate.py status
venv/bin/python scripts/semantic_migrate.py prepare
venv/bin/python scripts/semantic_migrate.py import --output /cale/privata/import.json
venv/bin/python scripts/semantic_migrate.py verify --output /cale/privata/verify.json
venv/bin/python scripts/semantic_migrate.py activate --output /cale/privata/activate.json
venv/bin/python scripts/semantic_migrate.py status
```

`prepare` activează coada persistentă, păstrând căutarea din fișiere. În această etapă,
scrierile noi sunt păstrate și în cache-ul NPZ. `import` reutilizează vectorii existenți,
fără inferență GPU, și salvează checkpointuri pe zile; poate fi reluat dacă backendul
este încă `files`. Sursele lipsă, goale și feedbackul neutru sunt excluse.

`verify` compară integral vectorii și metadatele eligibile, inclusiv amprentele surselor.
`activate` repetă verificarea sub blocare exclusivă a mutațiilor indexului și comută numai
dacă toate zilele coincid. Capturile pot continua să intre în coadă. După activare,
PostgreSQL este sursa principală și oglindirea NPZ se oprește; vechiul cache devine istoric.
O verificare între acel cache și baza activă nu mai este o verificare a stării curente.

### Export, revenire și backup

```bash
venv/bin/python scripts/semantic_migrate.py export --target /cale/privata/export-npz
venv/bin/python scripts/semantic_migrate.py rollback --output /cale/privata/rollback.json
```

`export` scrie NPZ și citește fiecare zi înapoi pentru comparație exactă, fără schimbarea
backendului și fără GPU. `rollback` face același export în directorul activ al indexului,
golește zilele fără corespondent în DB, apoi comută la `files` cu oglindire. Coada rămâne
persistentă. Revenirea backendului nu înseamnă instalarea unei imagini vechi: aceasta poate
să nu înțeleagă NPZ sau joburile persistente. Planifică separat revenirea codului.

Backupul aplicației include vectorii prin COPY binar (`pg/semantic_vectors.bin` și lista
coloanelor), iar celelalte tabele prin CSV, în același snapshot PostgreSQL. Restaurarea
validează antetele și coloanele, apoi golește și încarcă tabelele într-o singură tranzacție,
în ordinea relațiilor dintre ele. Un eșec anulează modificările DB. Joburile restaurate
în starea `running` redevin `pending`, cu generație nouă. Fișierele JPG și configurarea
trebuie recuperate împreună cu baza conform procedurii generale de backup.
Transportul COPY binar folosește un fișier temporar și nu dezvoltă vectorii în CSV.
Construirea și criptarea arhivei complete păstrează însă mecanismul existent în memorie;
consumul RAM al unui backup mare și spațiul temporar trebuie măsurate pe instalație.
Criptarea arhivei în flux rămâne o optimizare separată.

### Verificări și funcții încă separate

`tests/test_semantic_store.py` verifică lease-uri expirate, generații, retry, fișiere
invalide, feedback, filtre înainte de top-k, import reluabil, refuzul activării incomplete,
export/revenire fără GPU și restaurarea binară atomică. Aceste teste folosesc baza izolată.
Activarea reală cere și raportul importului integral, starea serviciilor și verificări HTTP.

Evaluarea vizuală etichetată, eșantionarea pe track-uri, verificarea VLM selectivă și
alertele semantice salvate sunt etape distincte. Migrarea stocării nu le implementează.

`scripts/semantic_compare.py` compară NumPy și pgvector pe un director de shard-uri copiate,
numai într-o bază de test marcată `OPENVIGIL_TEST_DB=1`. Cere `--source`, `--model-id` și
`--output`. Pentru indexuri istorice fără revizie de model, folosește o identitate declarată
cu prefix `legacy/`; forma vectorului singură nu dovedește proveniența. Prototipul nu este
backendul activ. Compară stocarea, scorurile și filtrele, nu precizia recunoașterii vizuale.

Pentru evaluarea vizuală, `tests/fixtures/semantic-evaluation.json` conține 12 întrebări
de pornire. `scripts/semantic_evaluate.py --manifest ... --output ...` exportă candidații
folosind modelul din serviciul AI local. Etichetează identificatorii din export în
`positive_assets` și `negative_assets`, apoi setează `reviewed=true`. Fără aceste etichete,
raportul păstrează precizia necunoscută; scorurile modelului nu sunt folosite ca adevăr.
`known_positive_recall_at_k` acoperă doar exemplele pozitive declarate, nu întregul corpus.

Galeria privată de evaluare poate păstra separat Corect / Greșit / Neclar și observațiile
pentru fiecare rezultat. Marcajele se salvează în browser; exportul JSON este copia de păstrat
și poate fi importat înapoi în același lot. Scorurile pot fi ascunse până la verdict.
Cazurile neclare nu devin rezultate negative și nu sunt eliminate din numitor pentru a
afișa o precizie artificială: precizia strictă a lotului rămâne necunoscută până la
verificarea tuturor rezultatelor. Etichetele automate de test nu sunt etichete umane.
Rezultatele top-k pot măsura calitatea selecției; pentru treceri omise sunt necesare
intervale verificate independent, iar pentru reglaje se păstrează un lot separat de validare.

---

Questions work in both global search and the alarm gallery through `/api/search`.
Examples: “when did the garbage truck pass yesterday between 8 and 10 at Gate?”,
“when did the white car first appear?”, “how many people passed today?”. Camera names
must match a configured, visible camera; ambiguous or conflicting selections are rejected.

Yesterday, the day before yesterday, calendar weeks and months follow the installation's
time zone. ISO dates and daily hour ranges are supported, including overnight ranges.
The upper boundary is exclusive. The effective period is displayed in the response.

Whole-frame labels do not exclude other objects in the image; class filtering applies to
object crops. Search covers saved, indexed images. Snapshot coverage does not describe the entire video
recording or all scene crops. Groups stay within one camera and at most 30 minutes; they
do not establish object identity. Weak matches are marked, with images and clip access for
review. Tracked objects and detection episodes are reported separately because they can
overlap. No observations is not proof that nothing passed.

The default index stays on disk. Updated days use atomic NPZ files, while legacy NPY/JSON
files remain readable and available for rollback. An old image will not see new NPZ
entries and must backfill missing snapshots. Keep NPZ files when rolling back.

PostgreSQL storage and durable indexing are opt-in. The migration command above enables
durable jobs first, imports existing vectors without GPU inference, verifies every vector
and metadata record, then switches storage only after a successful locked verification.
Read `status` to determine the active backend. PostgreSQL search currently uses exact
similarity, with filters before top-k. Images remain on disk. Export and rollback rebuild
and verify NPZ files without GPU; application backups include binary vector data and
restore database tables atomically. Historical model revision remains unknown.

The pgvector comparison script requires an explicitly marked test database, a frozen
copy of the index and a declared model identity. It compares storage and retrieval
equivalence without GPU inference and does not switch the live backend. Visual precision,
track-based sampling, selective VLM verification and saved semantic alerts require
separate evaluation before activation.
