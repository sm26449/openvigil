# Migrarea pe PostgreSQL + pgvector (2.0.0)

> Stare: **plan aprobat, în lucru pe ramura `feat/postgres`.** Instalarea live rămâne pe
> `1.1.0` (SQLite), neatinsă, până când 2.0.0 e probat. Țintă de deploy: **EliteDesk**, curat,
> în stiva lui. pv-stack rămâne complet neatins.

## 1. De ce

SQLite ne-a dus bine, dar punctul în care am ajuns (scorul spațial + semantic al falsurilor,
re-ID pe corp, fețe, scenă) cere **vectori de primă clasă** și căutare după similaritate. Azi
vectorii sunt împrăștiați în fișiere separate — fețe în JSON (`faces.py`), re-ID în `.npy`
(`reid.py`), scenă în shard-uri `.npy` pe zile (`semantic.py`) — fiecare cu propriul cod de
încărcare și căutare brute-force. Un singur Postgres + `pgvector` le unifică: *„arată-mi toate
corpurile care seamănă cu ăsta, pe toate camerele, din ultima lună"* devine o interogare, nu cod.

Decizie: **PostgreSQL obligatoriu, fără fallback pe fișiere.** Renunțăm complet la SQLite.
Rațiunea: o singură cale de date, un singur model mental, backup/restore unificat, și spațiu de
creștere (infrastructura poate ține mult mai mult pe viitor). Prețul — un container în plus — e
acceptat conștient.

## 2. Arhitectura stivei

Serviciul `db` intră în **același** `docker-compose.yml` openvigil (al 5-lea, lângă
go2rtc/web/record/ai):

- imagine `pgvector/pgvector:pg16` (Postgres 16 cu extensia `vector` preinstalată);
- ascultă **doar pe `127.0.0.1`** (port din `.env`, implicit 5433 ca să nu lovească un
  Postgres de sistem pe 5432); zero expunere în rețea, zero coliziune cu MySQL-ul pv-stack;
- datadir **sub `DATA_DIR`** (`${DATA_DIR}/pg`) → intră automat sub criptarea fscrypt, ca restul
  stării; vectorii sunt criptați la repaus;
- `healthcheck: pg_isready`; `web`, `ai`, `record` capătă `depends_on: db: condition: service_healthy`;
- parola și conexiunea în `.env` (0600): `PG_HOST`, `PG_PORT`, `PG_DB`, `PG_USER`, `PG_PASS`.

Portabilitate EliteDesk: se mută volumul `${DATA_DIR}/pg` + `.env` și se dă `up -d`. Aceeași stivă.

## 3. Modelul de date

O singură bază (`openvigil`), tabele cu nume vorbitoare. Nu folosim scheme PG multiple la început
(simplitate); le putem introduce mai târziu dacă vrem separare logică.

Tabelele migrate (de la SQLite): `events`/jurnal, `bookmarks`/marcaje, `jobs`, `push`,
`segmente`/lanț de custodie, + `schema_versiuni`. Tabel nou pentru vectori:

```sql
CREATE TABLE vectori (
    id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cam       INTEGER NOT NULL,
    tip       TEXT NOT NULL,          -- 'fp' | 'fata' | 'corp' | 'scena'
    eticheta  TEXT,                   -- person, car, numele persoanei etc.
    regim     TEXT,                   -- 'zi' | 'noapte' (pt. scorer)
    box       JSONB,                  -- [x,y,w,h] normalizat
    verdict   TEXT,                   -- 'ok' | 'gresit' | NULL
    poza      TEXT,                   -- POINTER către fișierul de pe disc, NU imaginea
    ts        DOUBLE PRECISION NOT NULL,
    emb       vector(768) NOT NULL
);
CREATE INDEX vectori_hnsw ON vectori USING hnsw (emb vector_cosine_ops);
CREATE INDEX vectori_lookup ON vectori (cam, tip, eticheta, regim);
```

**În Postgres stau doar vectorii + metadate ușoare** (~3 KB/rând). Pozele/crop-urile rămân
fișiere pe disc; `poza` e doar calea către ele.

## 4. Clasa DB — `app/db.py`

Un singur modul, importat de toate cele 4 servicii. **SQL raw parametrizat, nu ORM** — codul
actual e deja SQL curat; un ORM ar fi o rescriere inutilă și o sursă de bug-uri. Driver: **psycopg 3**
(`psycopg[binary,pool]`) + adaptorul `pgvector`.

API propus (stabil, minim):

```python
import db

with db.conn() as c:              # conexiune din pool, autocommit
    r = c.execute("SELECT ... WHERE cam = %s", (cam,)).fetchall()

with db.tx() as c:                # tranzacție (commit la ieșire, rollback la excepție)
    c.execute("INSERT ...", (...))
    c.execute("UPDATE ...", (...))

db.aplica("jurnal", SCHEMA_SQL, MIGRARI)   # oglindește schema.aplica, dar PG
```

- pool per proces (`psycopg_pool.ConnectionPool`, min 1 / max 4) — cele 4 servicii au fiecare
  pool-ul lor mic;
- `db.aplica(nume, schema, migrari)` păstrează **exact** tiparul vostru din `app/schema.py`
  (versiuni per-modul în `schema_versiuni`, migrări numerotate, fiecare într-o tranzacție);
- înregistrăm tipul `vector` o singură dată, la deschiderea pool-ului.

## 5. Traducerea SQLite → PostgreSQL (checklist)

| SQLite | PostgreSQL |
|---|---|
| `?` (placeholder) | `%s` |
| `INTEGER PRIMARY KEY AUTOINCREMENT` | `BIGINT GENERATED ALWAYS AS IDENTITY` |
| `INSERT OR REPLACE` / `OR IGNORE` | `INSERT ... ON CONFLICT ... DO UPDATE / DO NOTHING` |
| `rowid` | cheie primară explicită |
| `executescript(...)` | instrucțiuni separate (psycopg le acceptă una câte una) |
| `isolation_level=None` (autocommit) | pool `autocommit=True` sau `db.tx()` explicit |
| `json_extract`/json1 | operatori `jsonb` (`->`, `->>`, `@>`) |
| căutarea `?text=` din jurnal (substring) | `ILIKE '%...%'` (ulterior `tsvector` dacă vrem) |

**Tipurile de timp rămân identice la început** (stringuri `strftime` + epoch `float`), ca să NU
mișcăm semantica în timpul migrării. Upgrade la `timestamptz` — abia la final, ca pas separat și
testat.

## 6. Faze (fiecare: verde în CI, cu teste de paritate)

- **Faza 0 — fundația.** Serviciul `db` în compose; dependențe (`psycopg`, `pgvector`); CI capătă
  un serviciu Postgres; `app/db.py` + `db.aplica` PG; test de fum pe conexiune. **Niciun tabel
  mutat încă** — zero schimbare de comportament.
- **Faza 1 — ușoare. ✅ FĂCUTĂ.** `push`, `jobs`, `marcaje` → PG prin `app/db.py` (nu mai împart
  `events.db` cu jurnalul); `scripts/migrare_pg.py` (migrare date one-shot, idempotentă) + teste pe
  Postgres. `jobs` a căpătat coloana `seq` (IDENTITY) în locul lui `rowid` pentru ordine; în `push`
  coloana `user` apare citată `"user"` (cuvânt rezervat în PostgreSQL).
- **Faza 2 — `jurnal`/events. ✅ FĂCUTĂ.** Tabelul `events` pe PG; contractul `query()`/`insert()`
  neschimbat pentru cei ~9 consumatori (`rules`, `analiza`, `ask`, `mcp`, `recomandari`, `episoade`,
  `scena_web`, `routes_cameras`, `server`). Căutarea de text → `ILIKE` (PG `LIKE` e case-sensitive);
  ordinea în aceeași secundă → `seq` (IDENTITY) în locul lui `rowid`; `stats()` folosește
  `pg_total_relation_size`; migrarea `events.jsonl` scoasă (istoricul se mută cu `migrare_pg.py`).
  `conftest.py` sare toată suita dacă PG nu e disponibil (aplicația oricum nu rulează fără bază).
- **Faza 3 — `segmente`/lanț de custodie.** Scris de nvr-record; migrat cu **re-verificarea
  lanțului de hash** după import (dovezi — integritate înainte de orice).
- **Faza 4 — vectorii.** Scorerul FP (vezi §7) + mutarea `faces`/`reid`/`scena` în `pgvector`.
- **Faza 5 — final.** Backup → `pg_dump` (vezi §8); scot `sqlite3` din tot codul; șterg
  manipularea fișierelor `.db`; docs; bump **2.0.0**.

## 7. Scorerul de falsuri (feature-ul care a pornit totul)

Problema: cam 6 dă „person" fals pe o fâșie IR de gard, mereu în aceleași pătrățele (col 24–26,
rândurile 13–16 pe grila 32×24 — 72% din falsuri, cu `ok=0`). Nu se rezolvă prin reantrenare
YOLO (crop prea mic). Se rezolvă ținând minte **unde** și **cum arată** falsul.

Scor cu doi termeni, peste tabela `vectori` (`tip='fp'`):

```
scor_fals(detecție) = w_poz · rata_fals_celulă
                     + w_emb · (sim_la_false − sim_la_reale)

suprimă alarma dacă scor_fals ≥ prag  ȘI  ok_local = 0   (plasa de siguranță)
```

- `rata_fals_celulă` = fals/(fals+ok+k) în pătrățel+etichetă+regim (interogare pe metadate);
- `sim_la_false/reale` = cosinus (`1 - (emb <=> $q)`) între embedding-ul SigLIP al crop-ului și
  cei mai apropiați vecini `verdict='gresit'` vs `='ok'` (interogare `pgvector`, index HNSW);
- **auto-corectare:** fiecare verdict nou (∅/ok) întărește sau ridică mutul; dacă apare un „ok"
  real exact acolo, plasa `ok_local=0` oprește suprimarea — mutul se dizolvă singur;
- aplicat în `detect.py`, lângă filtrul de zonă (linia ~441), ca `filtru="invatat"`, **înainte**
  de alarmă și Telegram; rămâne în jurnal (auditabil, reversibil).

Avantajul față de zonă manuală / `conf_noapte`: e **țintit per celulă + etichetă + regim**, se
construiește singur din verdictele deja date, se auto-corectează, și merge pe **toate** camerele.

## 8. Backup / restore

`backup_nocturn.py` azi copiază `events.db`/`segmente.db` prin API-ul SQLite și sare peste
`semantic/` (se reface). După migrare:

- vectorii **care nu se refac** (verdicte FP, fețe) intră în copie prin **`pg_dump -Fc`** al bazei
  `openvigil`, adăugat în tar-ul Fernet existent;
- embeddings „rebuildable" (scenă, re-ID din arhivă) pot rămâne în afara copiei dacă vrem backup
  mic — se regenerează din sursă;
- restore: `pg_restore` într-o bază goală înainte de pornirea serviciilor.

## 9. Deploy pe EliteDesk

1. se aduce stiva (repo la tag 2.0.0) + `.env` + volumul `${DATA_DIR}/pg`;
2. `docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d`;
3. la migrarea de pe instalarea veche (SQLite → PG) se rulează scripturile de migrare o singură
   dată, cu verificare pe nr. rânduri + hash-uri (lanțul de custodie re-validat);
4. tot aici intră și itemii amânați: data-root pe disc mare, parolă admin rotită, api_token nou,
   cont dbg-mut șters.

## 10. Riscuri și controale (ca să nu naștem bug-uri)

- **Live neatins:** instalarea curentă rămâne pe `1.1.0` (SQLite) până 2.0.0 e probat integral.
- **Tot pe ramură** (`feat/postgres`); main rămâne stabil.
- **CI verde obligatoriu** cu serviciul Postgres nou la fiecare fază.
- **Teste de paritate:** aceeași interogare, vechi (SQLite) vs nou (PG), pe un set semănat.
- **Migrarea datelor** = scripturi one-shot, verificate pe nr. rânduri + sume de control;
  lanțul de custodie re-validat după import.
- **pv-stack complet neatins** — Postgres-ul nostru e intern, pe `127.0.0.1`, port separat.

## 11. Decizii (luate; deschise la obiecții)

- **PostgreSQL obligatoriu**, fără fallback — *luat*.
- **psycopg 3 + SQL raw**, nu ORM — *recomandat*.
- **O singură bază**, fără scheme multiple la început — *recomandat*.
- **Tipurile de timp păstrate** (string + epoch) în migrare; `timestamptz` ulterior — *recomandat*.
- Port implicit **5433** (local), ca să nu lovească un Postgres de sistem — *recomandat*.
