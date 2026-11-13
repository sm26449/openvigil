# Căutare semantică / Semantic search

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
sunt o sursă suplimentară. Grupările sunt per cameră, cu durată maximă de 30 minute, și nu
reprezintă identificarea certă a aceluiași obiect. Potrivirile slabe sunt marcate și pot fi
verificate prin poze și clip. Obiectele urmărite și episoadele din jurnal sunt raportate separat:
sursele se pot suprapune, iar lipsa observațiilor nu dovedește absența unei treceri.

Indexul live rămâne pe disc: NPY/JSON se citesc în continuare; o zi actualizată se scrie ca
NPZ atomic. NPY/JSON vechi rămân pentru revenire, dar nu primesc intrările noi; după revenirea
la o imagine veche trebuie refăcut indexul lipsă. Nu șterge fișierele NPZ la o revenire.
Reconcilierea la cinci minute recuperează snapshot-uri și candidați ai scenei care au jurnal
și fișiere disponibile. Erorile sunt raportate în starea backfill-ului.

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

---

Questions work in both global search and the alarm gallery through `/api/search`.
Examples: “when did the garbage truck pass yesterday between 8 and 10 at Gate?”,
“when did the white car first appear?”, “how many people passed today?”. Camera names
must match a configured, visible camera; ambiguous or conflicting selections are rejected.

Yesterday, the day before yesterday, calendar weeks and months follow the installation's
time zone. ISO dates and daily hour ranges are supported, including overnight ranges.
The upper boundary is exclusive. The effective period is displayed in the response.

Search covers saved, indexed images. Snapshot coverage does not describe the entire video
recording or all scene crops. Groups stay within one camera and at most 30 minutes; they
do not establish object identity. Weak matches are marked, with images and clip access for
review. Tracked objects and detection episodes are reported separately because they can
overlap. No observations is not proof that nothing passed.

The live index stays on disk. Updated days use atomic NPZ files, while legacy NPY/JSON
files remain readable and available for rollback. An old image will not see new NPZ
entries and must backfill missing snapshots. Keep NPZ files when rolling back.

The pgvector comparison script requires an explicitly marked test database, a frozen
copy of the index and a declared model identity. It compares storage and retrieval
equivalence without GPU inference and does not switch the live backend. Visual precision,
track-based sampling, selective VLM verification and saved semantic alerts require
separate evaluation before activation.
