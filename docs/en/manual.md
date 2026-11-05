# OpenVigil interface — one system for all your cameras

> **Historic document.** Written along the way, while the system was being built for the
> original installation (18 Hikvision cameras, a Hikvision NVR, systemd services, fscrypt
> encryption on the host). The public version runs in Docker: see [README](../../README.md) and
> [docs/operare.md](../operare.md). The "Services (systemd)" and "encryption" sections and the
> references to the Hikvision NVR, to Traefik or to host paths describe old stages and do not
> apply to the Docker installation; they are kept as a log of decisions.

**The server is the main recorder**: all 18 cameras are recorded locally, and both live view
and recording are pulled **directly from the cameras**, through go2rtc (`camN` = substream,
`camN_main` = raw mainstream; the camera passwords live only in data/go2rtc.yaml, 0600, never
on the command line of any ffmpeg). If go2rtc does not respond, nvr-record falls back to the
camera directly. **The Hikvision NVR is decommissioned** (Settings -> System -> "NVR retired",
since 2026-10-25): it is no longer monitored and nothing tries to contact it any more; the
switch is reversible, in which case playback falls back to it for the days before the migration
(2026-08-03). Measured write rate: ~303 GB/day for all 18 (mainstream copied,
no transcoding) -> retention set on two levels in Settings (currently 7 days
normal / 7 days with events) with an automatic disk-space guard.

## Access
- Web interface: **http://<server-ip>:8079** (LAN) or **https://nvr.example.com** through
  the Traefik of the photovoltaic stack (Let's Encrypt certificate, name resolved to the
  private IP — nothing exposed publicly). See "Access from outside the home" below.
- **Live** tab = grid of the 18 cameras (H.264 substream, lightweight)
- **Recordings** tab = pick a camera + date, press *Search*, see the timeline with the
  recorded segments (motion) and click anywhere to play from that moment.
- **➕ Add camera** button = Hikvision cameras accessed **directly by IP** (not through the NVR).
  You fill in IP/port/user/password; the backend tests the connection, reads the model and
  whether it has storage (playback). Standalone cameras appear in the grid with a badge (IP) + a delete button.
  Persisted in `data/cameras.json` (chmod 600, contains passwords), ids from 101 upwards.
  Live = H.264 substream 102 (copied); HD = mainstream transcoded to H.264, as with the NVR.
  Playback only if they have their own SD/HDD (those without storage show "no recordings").

## Standalone camera actions (microphone, speaker, siren, strobe)
When a camera is added (and once for the existing ones), the backend detects through ISAPI what
the camera can do (`features` in `data/cameras.json`) and prepares it automatically: it turns on
audio on the streams (AAC where possible) and the strobe linkage on IO-1. Buttons on the tile
(standalone cameras) and in the HD bar:
- **🔈 listen** to the camera microphone (unmute; AAC comes through MSE, G.711 through WebRTC)
- **🎤 talk** (hold) — goes through the go2rtc `isapi://` backchannel into the camera speaker
- **📢 siren** — plays the alarm sound once (in HD you pick the sound + the volume; note:
  the settings also stay in effect for an alarm triggered by events)
- **💡 strobe** on/off — the firmware has no test endpoint; we use the standard trick:
  whiteLight linkage on IO-1 + toggling the (unused) alarm input NO<->NC through ISAPI.
  Safety: the backend switches it off by itself after 60 s.
- **⚡ relay** (HD only) — the camera's alarm output
- **red badge on the tile** for active events (motion, intrusion, line crossing, etc.) —
  one thread per camera keeps `alertStream` open and the frontend polls `/api/events`.

API: `POST /api/cam/<id>/siren|strobe|output|volume`, `GET /api/events`.
The NVR cameras (1..16) do not have these actions (the NVR does not expose them directly).

## Animal/person detection (YOLO)
**🐾** button on any tile (and in the HD bar): turns detection on/off for that camera.
One worker per camera grabs a frame from go2rtc (`/api/frame.jpeg`, 640px substream) every ~1.5 s and
runs the model from the active slot `data/yolo11n.pt` — **yolo11m** (COCO) since 2026-08-15,
chosen after a measurement on 240 of our own frames (it eliminated the "person" phantom on the
gate post that yolo11n saw 3 nights in a row, plus the nonexistent bears; 19 ms/frame on the GPU,
revert in one click from Settings → Tools). Detections appear
as a green badge on the tile: person, dog, cat, bird, horse, sheep, cow, bear, plus
vehicles (car, motorcycle, bus, truck) for LPR.
**Hens and pigeons both show up as "bird"** — the standard model does not tell species apart;
that would take a fine-tuned model (a possible future step).
State persisted in `data/detect.json`; model in `data/yolo11n.pt`;
API: `POST /api/cam/<id>/detect {on, auto_siren}`, live labels in `GET /api/events`.
Beyond the COCO classes, a camera can learn on its own what is "new" in its frame — see
"Learn the scene" below.

### Automatic siren on dog (🚨)
**🚨** button on cameras with a siren: when it is on **and** 🐾 detection is active, a detected
dog triggers the siren (3 repeats, 60 s cooldown). The labels that trigger it:
`AUTO_SIREN_LABELS` in `app/detect.py` (by default only `dog`).

## The ⚙ Settings tab (everything from the UI, no terminal)
- **Telegram + Email (SMTP) notifications**: you fill in the details, "Send test" button on
  each channel. Persisted in `data/settings.json` (chmod 600); `config.py` stays only a
  fallback. Message with a photo for the chosen detections + text when a standalone camera goes down/comes back.
- **Which detections notify** (default dog/cat/person/bear — NOT bird, it would spam on hens) and
  **which detections trigger the automatic siren** (default dog) — checkboxes, no restart.
- **Model / training**: see how many clean frames have been collected and how many are in the
  labelled dataset; "📥 Apply the latest trained model" — finds the newest
  `best.pt` in `data/finetune/runs`, backs up to `data/yolo11n.pt.bak`, replaces it and does a **hot reload**
  (detection uses the new model from the next frame, no restart). The
  "↩ Go back to the previous model" button (shown only if a backup exists) **swaps** the active
  model with the backup — press it again and you are back on the new one; nothing is lost.

## Re-cataloguing detections (Alarms tab)
In the lightbox, under the photo: the "Correct" bar — click the correct label (renames the
snapshot, updates the gallery and the pills) or "🗑 False / delete". Also useful for
triaging photos before training.

## Local recording (standalone cameras, no SD)
`app/record.py`: one ffmpeg per standalone camera — mainstream **copied** (no
transcoding, AAC audio) into 5-minute fragmented MP4 segments, in
`data/recordings/cam<id>/YYYY-MM-DD_HH-MM-SS.mp4` (local time). Retention:
on two levels, configured from Settings (`retention_days` / `retention_event_days`,
with a per-camera override) + a space guard (`retention_min_free_gb`, deletes the oldest).
Their playback shows up normally in the Recordings tab ("continuous" segments on the timeline);
playback does a concat with `inpoint` through the go2rtc template `vodcat` + transcoding when needed.

## MP4 export
In the Recordings tab: **Export** interval (from / to, max 30 min) + **⬇ MP4** button.
The export (like the evidence sheet, clips, boxes, the case-file ZIP and adding a
camera) runs in the **job queue** (`app/jobs.py`, 3 workers, the `jobs` table in
PostgreSQL): the route answers 202 + job, the interface follows `/api/jobs/<id>` in the tray
at the bottom right (progress from ffmpeg, cancel) and downloads the file from
`/api/jobs/<id>/fisier` (kept for one hour). Web threads no longer sit blocked on ffmpeg.
Clicking the timeline pre-fills the interval (+2 min). Local = instant cut (`-c copy`);
NVR = real-time RTSP pull (takes about as long as the clip; HEVC, hvc1 tag). `GET /api/export`.

## Events on the timeline
The dots on the playback bar = the detections (green) and alarms (red) of the searched day
(from the snapshots); click a dot = play from that moment.

**Bookmarks:** the *Bookmark* button (in Recordings, during playback, or in the HD
overlay) puts a golden flag with your label at the played moment; bookmarks stay on the
timeline (both bars), have their own list on the right (click = play, *case file* = a ±15 s
piece of evidence into a case file, ✕ = delete) and live in PostgreSQL (the `marcaje` table,
`app/marcaje.py`, API `/api/marcaje`). The operator only places them on their own cameras.

### The "Alarms" tab (snapshots + clips)
On every YOLO detection (with the boxes drawn) and every camera alarm (motion,
intrusion, etc.) a photo is saved in `data/snapshots/YYYY-MM-DD/` (60 s cooldown per
camera+type, automatic deletion after `retention_snapshots_days`, 90 days by default).
The **Alarms** tab of the UI shows them by day,
with a camera filter and **pill filters by type** (with a counter); click a photo = built-in viewer
(lightbox) with left/right navigation (arrows/swipe/Escape). The **▶ Clip** button in the viewer
plays 20 s of video around the event (`/api/clip`, -5s..+15s): instant from the local
recordings (H.264 copied / HEVC transcoded), slower for the NVR cameras (RTSP pull + transcoding).
Clips are cached in `data/tmp/`. Times are Romanian time
(`Environment=TZ=Europe/Bucharest` in nvr-web.service).

## Identification that is useful in practice: LPR with an alert for unfamiliar cars, faces grouped by person
(2026-10-23 evening; started from the finding that Face ID had 29 recognitions in two months,
LPR 39 readings and zero plates with names — the pieces existed but were not producing.)
- **LPR, confirmed readings**: `lpr_min_conf` is the lower threshold (now 0.80), `lpr_sure_conf`
  the upper one (0.90). Above the upper one, a reading is accepted; between the thresholds, the plate is accepted
  only if it was read twice on the same camera within 30 s (`plates.confirmata`). That way the
  lowered threshold catches more real plates without stray OCR from a single pass.
- **Alert for unknown cars** (`lpr_notify_unknown`, only on `lpr_alert_cams`; empty =
  all): any plate WITHOUT a name -> Telegram "🚗⚠️ unknown car, plate X", with a photo.
  Configured on the gate cameras (13, 14), where it is also read. The family's cars are
  enrolled from the AI -> LPR tab ("Add" or "give it a name" on a reading) and then stay quiet.
- **Statistics** on the same screen: readings today / 7 days / without a name (`plates.stats`), so you can
  see whether the threshold and the cameras produce anything.
- **Faces grouped by person** (`faces.unknown_groups`, "✨ Group by person" button):
  the captured unknown faces are grouped by similarity (same threshold as recognition),
  each group shows a few samples spread over time, how many photos / on how many days, and — if
  it resembles someone in the library — the suggestion with a percentage. You confirm with one click: "it's X",
  an existing person from the list, a new name, or "ignore". At most 8 photos are enrolled
  (spread over time), and the whole group leaves the unknowns. Nothing enrols itself:
  automatic enrolment ("Auto-detect residents") doubled photos and mixed up people in
  September, which is why its button disappeared (the API `/api/faces/auto-residents` remains).
  The embeddings of the crops are cached in nvr-ai (by mtime), so grouping can be requested at any time.
- **Unknowns as people** (stage 2, 2026-10-24): grouping no longer has to be requested, it happens
  by itself in nvr-ai two minutes after any new face (`faces.programeaza_regrupare`) and
  is written to `data/faces/grupuri.json` with **stable ids** across regroupings
  (`potriveste_ids`: a new group takes the id of the old group with the nearest centroid,
  cosine >= 0.60). In the AI -> People tab they appear as "Unknown N": photos, how many times and on how
  many days, last appearance (day, time, camera), the suggestion from the library; you name it, merge it
  with an existing person or ignore it. On the cards of known people, "looks like the same as X — merge" appears
  when two people in the library resemble each other above 0.50
  (`perechi_persoane`; `POST /api/faces/person/<b>/uneste {in: a}` moves the photos and rebuilds
  the embeddings). The "Alert on unknown face when I'm away" button creates the rule
  (trigger `persoana_necunoscuta`, away + night modes, notification, 2-minute cooldown).
- **One search** (`app/static/js/48-cauta.js`, the field in the header or the `/` key): the text
  goes in parallel to semantic photo search (`/api/search`, with a period of 7-90 days), to the
  LPR history (`/api/plates?q=`), to the person's history (`/api/faces/history?name=`) and to the
  event log (the latest 400, filtered by words, ignoring diacritics). Results arrive
  in a single grid, newest first, with chips showing the count per source (All /
  Photos / Plates / People / Log); clicking a result opens the event card.
- **Camera health in the list** (Cameras tab): under each camera's status, a small row
  from `/api/continuity?days=1`: "recording, last segment 12 s ago", restarts, "2 gaps today
  (17 min)" (yellow). A single call for the whole list, once the rows exist.
- **"Smart" rules** (stage 3): the rules engine has new triggers — `trecere`
  (counting line crossed; value: `poarta`, `poarta:intra`, `iese`, optional
  `@person` for the label), `intrare_zona` (the zone name, optional `@label`), `intrus`,
  `obiect_nou`, `obiect_disparut`, `parcare_inceput` (label or empty). Lines and zones are
  drawn as before (Live → the camera's lines/zones button; nvr-ai tracks them with
  direction), and each line/zone in the editor has "⚙ rule", which opens AI → Rules
  pre-filled (`regulaDin` in 70-ai.js, also used by the event card); the form
  suggests the drawn lines and zones, with direction. This is the equivalent of the "Smart Event"
  (directional tripwire, intrusion) of Hikvision/Dahua, but tied to the house modes.
- **"Find them on all cameras"** (our AcuPick; `app/reid_arhiva.py`): every
  body sample that re-ID computes anyway (every ~3 s, on tracked people from the
  tracked cameras) is kept on disk, by day: an OSNet embedding (512 x float16) + a
  128 px crop, in `data/reid/<day>.jsonl` and `data/reid/<day>/`. In the card of an event with a photo,
  "🔎 Find them on all cameras" crops the largest person in the photo (or the given box), embeds
  it in nvr-ai (`POST /reid/cauta`) and returns the appearances of the last 1 / 7 / 30
  days across all cameras visible to the account, with the similarity (threshold 0.50; on the same camera,
  within a minute, only the best one); clicking an appearance opens the card/playback, "search by
  this one" continues the chain from that appearance. Limit of the method: same clothes — the next day, with
  a different jacket, they are no longer found. Archive retention = that of the photos (daily cleanup from
  `snaps.prune`). Only the cameras in `track_cams` feed the archive.
- **Camera tampering** (covered lens `shelteralarm`, `tamperdetection`, changed scene,
  defocus): the events already come from the cameras (ISAPI); they now have the category
  "Tampering" in the feed (its own chip), severity "warning" by day (an incident to triage) and "info"
  at night — cam3 and cam4 give ~30 "lens covered" per night from IR/insects
  (2026-10-24), so night triage would have been noise; `/api/sabotaj` counts the tamper
  alarms per camera today, and the camera list shows them (with the note "probably IR/insects" above 10);
  in rules, `alarma_camera` also suggests `shelteralarm` / `tamperdetection` (with cooldown).
  To tune on noisy cameras: the "video tampering" sensitivity in the camera's own interface.
- **Cyclists without a new model**: for every tracked person (`obiect`), nvr-ai computes the
  speed in body-heights per second (`ai_analytics.inaltimi_pe_s`: px/s / box height;
  the person is their own ruler, no calibration in metres). Above 2.2 body-heights/s for at least one
  second (walking ~0.8, running ~1.8, bicycle 2.4-4) the event gets `biciclist`
  (log: "🚲 cyclist (3.1 body-heights/s)"), and rules on `obiect` accept the value
  `biciclist`. Only on tracked cameras (`track_cams`).
- **Code review of the batch** (2026-10-25, 21 findings, all applied): the gaps
  in the camera list read the wrong key (`goluri_azi`); the "newest first" listings in
  Annotation and in the uncertain queue sorted by camera id, not by date; the nvr-ai client
  now also returns 4xx answers with their reason (before it said "nvr-ai does not respond" for
  "a job is already running" or "no person in the photo"); merging people no longer falls back to a second local
  run when nvr-ai died midway; the training job no longer stays "active"
  if the process fails to start and a stop no longer shows up as a failure; the uncertain queue has a lock between
  processes (nvr-ai writes, nvr-web pops); scheduling the regrouping no longer waits for the regrouping;
  rules on `obiect_nou`/`obiect_disparut` no longer have a value (the events have no label);
  viewers and operators can use the feed, search, the log, LPR readings, the person's
  history and "find them on all cameras" (now GET), all filtered to their cameras;
  "Rule from this" waits for the form to load; rebuilding the dataset replaces the
  splits (no train/val leakage) and always keeps the verified frames in the sample;
  the re-ID archive adds the sample to the cache instead of discarding it; the feed keeps the day's listing
  cached on the directory's mtime.
- **Daily summary on Telegram** (2026-10-25): Settings -> Notifications -> "Daily summary":
  on/off and the time (default 21:00; `digest_telegram`, `digest_ora`); a thread in nvr-web
  (`digest.start`, as with the nightly backup: at startup it does not send "because the time has passed")
  sends once a day, on the configured channels, the text from `digest.text`: events and
  incidents, activity interval and peak hour, the most active cameras, who was
  seen (Face ID), unknown cars, recording gaps and cameras that are not writing, reported
  tampering, the headlines of the day (the criticals). Each source is independent: an error in one
  does not cut the message. "Send the summary now" sends it on the spot; `GET /api/digest/text?day=` shows
  it without sending it.
- **Notification channels, in plain view** (Settings -> Status, the "Notifications" row; `/api/system`
  -> `notificari`): Telegram / email / push with the number of devices; red when there is NO
  channel at all (then all alerts stay only in the log — that is how it was until 2026-10-25,
  with nothing signalling it). When the daily summary is off, on Mondays at `digest_ora` a
  "the channel works" test goes out, so the path is checked weekly.
- **Public evidence verification** (`/verifica`, no login; `app/verificare.py`): you upload an
  exported MP4 (up to 2 GB; the fingerprint is computed on the stream, the file is not kept) or
  paste a SHA-256; the answer says whether it is an export produced by this NVR (when, from which
  camera, what interval, who requested it) or a raw segment from the chain of custody, and that it has not
  been modified since. The exports registry is `data/exporturi.jsonl` (written on every
  export, since 2026-10-25); segments are looked up by hash in the chain of custody table
  `segmente` in PostgreSQL (`lant.cauta_hash`).
  Every public verification goes into the audit (only the first 16 characters of the fingerprint).

## The event feed next to Live (stage 1 of the interface analysis, 2026-10-24)
To the right of the Live grid (the "Feed" button in the bar; on by default on wide screens, off
on phones; the choice stays in the browser) sit the latest frames saved today — detections, faces, plates,
alarms — with a thumbnail, what/where/when, and filter chips (People, Vehicles, Animals, Faces,
Plates, Alarms). Click a frame = playback of that moment (`#pb/cam/day/sec`). It
refreshes every 10 s only while the grid is visible and only incrementally (`/api/flux?dupa=`);
new frames pulse for a moment. Thumbnails come from `/api/flux/thumb/<day>/<fn>` (200 px, in-memory
cache, same access rules as `/snapshots`); 40 full-size photos would have been ~4 MB.
Logic in `app/static/js/15-flux.js`, the category in `routes_analytics._flux_categorie`.

**The event card** (`app/static/js/45-fisa.js`, native dialog `#fisa`): a single place for
any event, opened from the feed (click a frame), from the log and from incidents (the
"card" button) — photo, what / where / when / who, the severity, and the actions: "Play the moment"
(`#pb/cam/day/sec`), Clip, Export 30 s (watermarked MP4), To case file, Confirmed / False (when the
event is an incident), "Rule from this" (opens AI -> Rules pre-filled with the type,
the value and the camera). Any element with `data-fisa='{json}'` opens it (delegation on the document).

**Settings that are easier to navigate** (same day): a search field in the Settings menu (shows, across
all sections, only the cards whose title or text contains the words, ignoring diacritics)
and an "advanced" tick box (off by default, kept in the browser) that hides the rarely touched cards:
MQTT, camera time, grid fluidity, automations and the token, region detection,
quick links (`data-avansat` on the card). The "Status" button in the header is a shortcut to
Settings -> Status. The AI tab: each section has its question under the name ("who is coming in?", "which
vehicles come?") and a status sentence built from the data (people, recognitions in 30 days, LPR
readings, active rules, labelled frames), so you can see at a glance what each one produces.

## Person recognition (Face ID)
When YOLO detection confirms a person, `app/faces.py` asks the camera for a high-resolution
frame (ISAPI picture — on the 640px substream faces are unusable) and looks for faces
with InsightFace buffalo_l (SCRFD + ArcFace, local ONNX, on the GPU). Cosine similarity
against the library `data/faces/persons/<name>/`: above the threshold (default 0.45) = recognised
person — snapshot with the name as the label (shows up as a pill in Alarms and a live badge),
notification with the name, webhook `persoana`. Below the threshold = the crop is collected in Settings →
Detection ("Unrecognised faces"), where labelling it IS enrolment; a person becomes
reliable from 3-5 photos taken from different angles. Everything is managed from the card
"Person recognition": start, threshold, notifications, people, photo upload.
**Anti-spoofing** (on by default): every face goes through the MiniFASNet
V1SE+V2 ensemble (uniface, ONNX on the GPU) before matching — a photo or a screen held up to
the camera is rejected (it does not match, it does not enrol) and emits the event
`fata_falsa`, which can trigger rules (the attempt itself is an alarm signal).
Rejection requires BOTH models to say "fake" with confidence above the threshold
(`face_antispoof_conf`, default 0.7) — a real face seen badly does not lose
recognition. Enrolment from uploaded photos does not go through the check (it is legitimate
by its nature).
`data/faces/` (biometrics) is not committed to git; the model (~280 MB) downloads itself on
first use.

## Licence plates (LPR)
Same pattern as Face ID, for vehicles (car/motorcycle/bus/truck — these are
confirmed from a single frame, so that moving cars do not slip through): when a
vehicle appears, the HD frame goes through `app/plates.py` (fast-alpr: YOLOv9 plate detector +
CCT OCR, local ONNX). Readings above the confidence threshold (default 0.85) go into the
log in Settings → Detection, with the plate crop; the same plate is not logged
twice within 120 s. Plates can be given names ("Ion's van") — known cars
notify with their name, and every reading goes out on the `numar` webhook (Node-RED can
open the gate). `data/plates/` is not committed to git.

## Semantic search in alarms
Every photo in Alarms gets a "fingerprint" of meaning (SigLIP base multilingual,
`app/semantic.py`, local, on the GPU) in a per-day index (`data/semantic/<day>.npy` +
JSON meta, deleted automatically together with the retention photos). The search field in the
Alarms tab finds photos by free description **in Romanian** — "white car", "person with
backpack", "dog near the gate" — ordered by match; click = the same viewer,
with clip and "see in context". New photos are indexed as they appear (asynchronous
queue, detection does not wait); the history is indexed in the background on activation or with
the button in Settings → Detection. It also works for viewers (results are filtered to
their cameras). Since 2026-10-27 the "Learn the scene" crops also go into the index (marked 🧩),
with the SigLIP vector already computed at classification — see the "Learn the scene" section.

## The AI tab: people, vehicles, rules, events
A dedicated tab with four sections (sidebar, like Settings):
- **People** — the Face ID library, labelling of unknown faces, and a per-person
  "history": all of their recognised appearances (`data/faces/log.jsonl`) — the answer to
  "when did X last come by?".
- **Vehicles** — LPR: known vehicles and the readings log, plus **exact counting**
  (ByteTrack on the chosen cameras: every object gets a tracked ID at ~4 frames/s
  from the substream; when it leaves the frame it becomes the `obiect` event with the label, duration
  and direction of travel — "how many cars went by" is counted piece by piece).
- **Rules** — the rules engine (`app/rules.py`), on the model familiar from NVRs
  (Detection → Where → Schedule → Output): trigger (specific/unknown person, known/unknown/specific
  plate, detection, camera alarm by subtype, noise, camera
  down) + where (zones = the camera groups, and/or individual cameras, combined with
  OR) + schedule (time interval that can cross midnight, days of the week,
  cooldown) + output (siren, strobe, TTS announcement, relay, notification, `regula` webhook,
  MQTT message on any topic). Actions run in a separate thread and are logged
  with the result of each; loops are impossible (the `regula` event does not
  trigger rules). "Test" button = runs the actions now.
- **Events** — the permanent log (the `events` table in PostgreSQL, retention
  by days from Settings → Retention, one year by default): ALL
  events go through `hooks.emit()` and end up here, with filters by type/camera/day,
  CSV download and a "clip" link to the recording of the moment. The default view
  is by **episodes with duration**: events of the same kind, on the same camera, less than
  5 minutes apart (`incident_gap_sec`) become one row with start/end/duration/how
  many times, and the "MP4" button downloads exactly that interval from the recording
  (`/api/journal?grup=1`, export -5s/+10s around the episode). The "report" button
  issues the **evidence sheet** (`/api/export/report`): camera/interval/moment metadata +
  the **SHA-256** of the MP4 file — what you hand to the police along with the video so its
  integrity can be proven (check: `sha256sum`).

## MQTT bridge
`app/mqtt.py` (Settings → System → MQTT bridge; default broker 192.168.1.2): all
events are published as JSON on `nvr/evenimente/<tip>` and `nvr/camera/<id>/<tip>`,
and `nvr/stare` holds online/offline (retained, with Last Will — the broker announces by itself
that the server has gone down). The "MQTT" action of a rule publishes any message on any topic
— e.g. "unknown person, zone Yard, 23–06 → topic `lumini/curte`, message `ON`" turns on
the light in Home Assistant/Zigbee2MQTT/Tasmota. The connection reconnects by itself; a downed
broker stops nothing (events stay in the local log).

## Dataset and training (YOLO fine-tuning, local)
Besides the frames below, the **verdicts from "Learn the scene"** (and those under the YOLO detections
on Telegram) feed the same dataset — confirmed crops as positive examples, "just
rain / shadow / leaves" as backgrounds, new vocabulary names that can become new classes; see the
section "Learn the scene".

On every detection the **clean** frame (no boxes) is also saved in `data/dataset/`
(max 1/min per camera, never deleted automatically) — the raw material for a model on our own scenes. The flow is
local, with no external tools:
1. `python scripts/finetune_dataset.py` : the yolo11x teacher labels the frames
   automatically, SigLIP splits the birds into species (hen / pigeon / bird), a
   stratified 85/15 split per camera into `data/finetune/{images,labels}/{train,val}` + `dataset.yaml`;
   frames without detections keep empty labels (the background that teaches the model not to see phantoms).
2. Training with ultralytics (in the venv, on the RTX 2000 Ada), result in
   `data/finetune/runs/<name>/weights/best.pt` — the active model `curte-v1` (11 classes) was
   made this way on 2026-08-15: yolo11m, 30 epochs, 7,179 images.
3. Settings -> Tools -> Model: "Apply the latest trained model" (verified transaction in nvr-ai,
   backup, one-click revert).

Steps 1-2 are started from the same card (2026-10-24): "Build the labelled dataset" (with a
cap on frames, random sample with a fixed seed; 0 = all, takes hours at 100,000 frames) and
"Train a new model" (N epochs; the name `curte-v<N+1>` is assigned automatically). They run as
subprocesses in nvr-ai (`app/antrenare.py`: one at a time, progress read from stdout, log, stop
with `/api/train/stop`), because they need the GPU; while they run, detection is slower — start them
at night. `scripts/antreneaza.py` is the recipe of curte-v1 (yolo11m from `data/yolo11m_stock.pt`,
imgsz 640, batch 10, patience 8) and prints one line per epoch.

**Annotation** (AI tab -> Annotation, 2026-10-24, batch 1): the dataset labels frame by
frame — the list (train/val, only unverified, camera filter), the frame with the teacher's
boxes coloured by class, the legend, arrow-key navigation, "Confirm" (Enter) writes the
labels and marks the frame in `data/finetune/verificate.json`; rebuilding the dataset
keeps the labels of the frames verified by a human (`finetune_dataset.py`). Routes:
`/api/adnotare`, `/api/adnotare/cadru/<split>/<fn>` (GET/POST), `/api/adnotare/img/...`;
logic in `app/adnotare.py`. Batch 2 (same day): editing directly on the canvas — dragging on empty space
= new box with the current class, click = select, dragging from inside = move, from a corner =
resize, Delete = remove, 1-9/0 = class (of the selection or the current one; also from the
legend), Ctrl+Z = undo (50 steps), Esc = deselect, Enter = save; with unsaved changes,
switching to another frame asks for confirmation. Batch 3: the **queue of uncertain frames** —
"Find uncertain frames" makes the ACTIVE model (in nvr-ai) go through the newest N raw frames
from `data/dataset` that are not yet in the dataset; those with boxes of confidence
0.30-0.55 go into `data/finetune/nesigure.json`, pre-labelled (boxes with a percentage,
those under the threshold with a dashed line), the most uncertain first. The source "queue: uncertain frames"
in the editor: you correct/confirm -> the frame goes into `images/train` as human-verified and leaves the
queue; "Ignore" removes it without it going in. That way you correct the 50 frames that matter, not 7,000.
(`app/nesigure.py`; routes `/api/adnotare/nesigure/scaneaza|stare`, `.../cadru/nesigure/<fn>/ignora`.)

## Learn the scene: change detection, open vocabulary, verdicts (2026-10-26 … 11-01)
Besides YOLO (which knows the classes it was trained on), each camera can learn what
"normal" means in its frame and flag only **what is new**. It is started from the camera card,
is opt-in and works per camera (`app/scena.py` in nvr-ai, setting `scena`).
- **The learned background** — the camera keeps a reference of the quiet frame; when something changes
  in a region, the crop of that region (from the **HD** stream, not the substream) becomes a
  **candidate**. The candidate is classified on the crop with an **open vocabulary** editable from the
  interface (SigLIP, similarity to human-written names) plus the active YOLO. Whatever is outside the camera's
  detection zone (if one is drawn) does not become a candidate and does not use up classifications.
- **The camera card** shows the candidates with **✓ / ✕ / ? / 🔍** buttons and, by default, hides
  "unknown" and the background (a toggle shows them). The verdicts are collected in
  `data/scena/<cam>/feedback.jsonl`. New events: `scena_candidat`, `scena_analiza`.
- **On Telegram** candidates can go out as a photo with buttons (✓ correct, ✕ what it actually is,
  🔍 reanalyse). "Unknown" goes out only by day and in good weather (at night it is infrared grain,
  in rain it is droplets); at night only the labels given by a detector with
  confidence of at least 0.5 go out. Every decision to send or skip is in the system log.
  The night threshold is a setting (`scena_telegram_noapte`, Settings → Location and weather) and can be
  per camera, from the recommendations (`scena_noapte`).
- **YOLO-World on the crop** (`yolov8m-worldv2`, downloaded on first start into `data/modele/`):
  the whole vocabulary also goes through an open-vocabulary detector, which sees the object in the
  crop, not the background, and wins when the trained YOLO is not sure. Romanian names get
  the English form for CLIP from the interface dictionary, from "name = english name" written in the
  vocabulary, or through a remembered translation; the setting `scena_lume` turns it off.
- **Deep analysis** (the 🔍 button, on demand): YOLO at 640, the whole vocabulary and the language model
  (Qwen2.5-VL) run on the frame and answer in the same chat. The language model's label is believed
  **only** if YOLO-World, SigLIP or the trained YOLO confirms it; otherwise it stays
  displayed as unconfirmed and the rest decides. The text shows from the vocabulary only the names that come out
  ahead, not three names regardless.
- **Examples learned from verdicts**, per camera: any verdict with a name turns the candidate's crop
  into an example for the camera (the SigLIP vector, `data/scena/<cam>/fundal_invatat.*`).
  Background names ("nothing", "leaves", "shadow", "rain", "tree") match above 0.85 and
  beat any label — the candidate does not go out on Telegram and does not enter search. Object names
  (✓ on the proposed label or ✕ with the true name) match above 0.82 and replace only
  "unknown" and the vocabulary's minor scores, not a detector's label (source "learned
  from verdicts"). The closest example that passes its threshold wins. The examples are rebuilt from
  `feedback.jsonl` when nvr-ai starts.
- **Verdicts also under the YOLO detections** (🚨 person / dog / cat / bear on Telegram), not just under
  the scene's candidates. ✓ becomes an object example for the camera, ∅ a background example, ✕ opens
  the vocabulary; the crop comes from the detection's box (the snapshot's sidecar), everything goes into the
  same `feedback.jsonl` and into the fine-tune dataset. Messages without a snapshot (the photo
  cooldown) stay without buttons.
- **Verdicts go into training** (Settings → Tools → Model): confirmed crops become
  positive examples, with the object's box set by YOLO-World; "it's just rain / shadow / leaves"
  become backgrounds without objects; new vocabulary names with enough examples become new classes
  (`data/finetune/clase.json` keeps the ids stable). Training runs on yolo11m or yolo11l
  and is evaluated automatically per class, on the fixed validation set (`scripts/evalueaza.py`,
  `runs/<name>/evaluare.json`); the results appear in the training card.
- **The scene in bad weather**: asks for three times more frames before classification and halves the
  classification budget.
- **Scene crops go into semantic search** (see the section above), with the SigLIP
  vector already computed, marked 🧩; rain, shadows and leaves do not go in. They have the photos' retention
  (`retention_snapshots_days`), except those with a verdict, which stay for training.

## Per-camera recommendations (2026-11-01)
In the camera card, the system infers from what it has seen (log, candidates, verdicts, the
instability map) a few **one-click applicable recommendations**, each audited when applied:
HD region detection when objects are small, a detection zone below the unstable band (trees,
sky), a higher confidence threshold at night when you have rejected "person" several times (new setting, per
camera: `conf_noapte`), turning on tracking or the scene, new names in the vocabulary. The
**"hot zone"** recommendation notices when the scene's candidates crowd into a small patch of the frame
(a bush in the wind, a branch in infrared, grain on the lawn) and proposes a detection zone
that leaves it out.

## Weather on events (2026-10-27)
Settings → Tools → **Location and weather**: the town is looked up by name (Open-Meteo geocoding,
no key), the outdoor weather is read every 5 minutes and combined with what the cameras estimate
(rain from the scene's skipped frames, wind from the unstable zones, fog from the background
contrast). Every event in the log gets the `vreme` field; an optional confidence threshold
(`vreme_prag`) applies to detection in rain, snow, fog or storm; in bad weather
the scene's "unknown" candidates no longer go out on Telegram (switchable). `data/vreme.json` is
read by nvr-ai without a network; there is also an MCP tool `vreme`.

## Assistant: MCP server and configurable language model (2026-10-31)
- **MCP server** (`POST /mcp`, with the API token as `X-Token` or `Authorization: Bearer`): an
  assistant (Claude Desktop / Claude Code, any MCP client) can ask the system what
  happened — events with a text filter, episodes, the scene's candidates — can search the photos,
  request a camera's frame, see the recommendations and switch the house mode. The stdio bridge is
  `scripts/openvigil_mcp.py`; details in `docs/mcp.md`.
- **Configurable language model** (`app/llm.py`): the local Qwen in nvr-ai or, optionally, an
  OpenAI-compatible server (Ollama, LM Studio, vLLM), chosen in Settings → Tools, with a test
  button. The summaries of presence episodes can be written by it (one sentence, from facts only;
  the template stays as `text_sablon`). Natural-language questions also work in English,
  with the answer in the interface language. The log can be filtered by text, ignoring diacritics
  (`/api/journal?text=&zile=`), and a human's verdict on a candidate also reaches the search
  index (the new label, or the row drops out if it was only background).

## Recordings, archive and copies on separate disks (2026-10-26)
`RECORDINGS_DIR`, `ARHIVA_DIR` and `BACKUP_DIR` (separate binds in Docker): the recordings,
the archive tier and the nightly copies can each sit on a different disk from the rest of `data/`.
The remaining data (configuration, databases, faces, index) stays where `data/` is.

## Architecture
```
Browser  ──:8079── backend Flask (cautare ISAPI + inregistrare streamuri)
         ──:8076── releu autentificat (app/stream_proxy.py, in nvr-web; verifica sesiunea)
                        └── 127.0.0.1:1984 go2rtc (streaming MSE/WebRTC; transcodare
                                HEVC->H.264 cu ffmpeg) ── RTSP/ISAPI ──> camere / NVR
```
- The recordings on the NVR are **HEVC/H.265 6MP** -> transcoded to **H.264 720p** for the browser.
- Live uses the **H.264** substream directly (copied, no transcoding).
- go2rtc is the very streaming engine of **Frigate** — if you ever want AI detection,
  the central piece is already here (zero work thrown away).

## Services (systemd, start at boot)
```
systemctl status nvr-go2rtc      # motor streaming  (127.0.0.1:1984, 127.0.0.1:8554, :8555)
systemctl status nvr-web         # interfata web    (:8079) + releul spre go2rtc (:8076)
systemctl status nvr-record      # inregistrare continua camere standalone (separat,
                                 #   ca restarturile interfetei sa nu taie inregistrarea)
journalctl -u nvr-web -f         # log live backend
```
All of them have `ExecStopPost=scripts/notify-fail.sh` -> a Telegram alert on any failure
(crash, OOM, signal; not on a manual stop/restart), at most one per 10 min per service.
The units in `deploy/systemd/` are copies of the ones in `/etc/systemd/system/`.
The watchdog in nvr-web also reports: standalone camera down/back, NVR down/back,
disk almost full. The project is in **git** (without passwords: config.py, cameras.json,
settings.json, go2rtc.yaml are in .gitignore; see config.example.py).

## Navigation (live <-> recordings transition)
**In the HD overlay** (click any camera): under the player is the **time-shift** bar —
`⏪ 30s / 5min / 30min` jump instantly into the same camera's recording, `🔴 Live` brings you
back, and the **day's mini-timeline** (green segments + event dots + red "now"
marker) lets you click anywhere = play from that moment. `↗ Open in Recordings`
takes the camera to the big tab.
**In the Recordings tab** (stage 2 of the audit, 2026-10-23): the timeline loads by itself when
the camera or the day changes; **zoom** 24h / 6h / 1h / 15 min (buttons, `+`/`−`, wheel on the
bar; Shift+wheel scrolls; the window follows the cursor); **pause** (Space/K), **frame by
frame** (`,` `.`, step of 1/25 s in the buffer), instant jumps in the MSE buffer (J/L, arrows,
Shift+arrows ±1 min) with no new stream, speeds **½x / 1x / 2x / 4x / 8x** (`[` `]`; 4x and 8x
only with a GPU, 8x measured 0.995x realtime on HEVC 2688x1520), **Link** copies the address of the moment
(`#pb/<cam>/<day>/<sec>`), dragging on the bar directly offers "⬇ MP4" and "🔖 bookmark" for the interval.
**A single "Events" screen** (instead of Incidents + Alarms + AI → Events): sub-panels
*Open* (incident triage, with a badge on the tab), *Log* (the permanent history, episodes,
CSV, clip/MP4/report) and *Snapshots* (snapshots with filters, semantic search and questions);
addresses `#ev/inc`, `#ev/jurnal`, `#ev/poze` (`#inc` and `#al` remain as aliases). Settings →
Log stays separate: it is the history of configuration changes, not events.
The page address always says where you are (`#live`, `#ev/...`, `#pb/...`, `#hd/<cam>`): a link
received on Telegram opens the moment directly (with `web_url` from Settings → Notifications,
notifications get the link), and Back on a phone closes the HD overlay.
`⏮ Event` / `Event ⏭` jump from event to event,
`⏪10s/1min` and `10s/1min⏩` make fine jumps, the cursor **advances during playback**
(the current position is shown), and the **side event list** (with thumbnails) is
highlighted in sync with playback; click a row = jump there.
**From the Alarms tab**: the `↗ See in context` button in the photo viewer opens playback
at the exact second of the event.
**📸 Screenshot**: button in the HD bar and in playback — saves the current frame as a JPG
(the stream's native resolution, regardless of digital zoom).
**🔎 Digital zoom**: mouse wheel (or pinch on a phone) on any player — HD or
playback; drag with the mouse/finger to pan, double-click or Escape = reset.
Max 6×, the image cannot be dragged out of the frame. Distinct from **🔍+/−** (OPTICAL
zoom, only on the 6 cameras with a motorised lens — that one changes what the
camera records, digital zoom is display only).

## The 📷 Cameras tab (administration)
The single camera registry (`data/cameras.json`) — **the NVR is no longer needed** for
operation, it remains only as the historical archive (the `nvr_channel` field). For each camera:
- **rename**, direct link to its interface, model and NVR channel (if it was imported);
- **automatically detected hardware capabilities** (microphone, speaker, siren, strobe,
  optical zoom, relay) — the buttons appear in the UI only where the hardware exists;
- **recording mode**: continuous / on motion / off. "On motion" records
  continuously and then deletes the segments without events (±30 s) — that way the
  start of an event is never lost, unlike starting/stopping ffmpeg;
- **quality/space**: codec (H.264/H.265), bitrate and fps, with a GB/day estimate. Written
  directly to the camera (ISAPI) — we record exactly what it sends, with no re-encoding;
- **in-camera detections** (firmware): motion, line crossing, intrusion, zone enter/exit,
  scene change — individually switchable.

**Network scanner**: the "🔍 Scan the network" button — you give a range (e.g. `192.168.1.0/24`)
and credentials, and the system finds the cameras (ports 80/8000/554 + ISAPI identification:
model, serial, firmware), marks the ones already added and imports the ticked ones in bulk,
with a full capability probe.

## Space (Settings -> 💾)
Summary of used/free space + the **re-encode old recordings** option (AV1/HEVC,
only with a hardware encoder; the RTX 2000 Ada has av1_nvenc, so the option is available,
with decoding also on the card). The biggest gain, however, comes from the Cameras tab: H.265 codec,
reasonable bitrate/fps and the "on motion" mode on quiet cameras.

## Access from outside the home (HTTPS, push)
HTTPS is not in nvr-web (waitress does not do TLS): it is terminated by the **Traefik** of
`docker-setup` (pv-stack), which already has Let's Encrypt certificates through Cloudflare DNS-01.
Name: `nvr.example.com`, an A record to the LAN IP (like `mbus`), no Cloudflare proxy. A
dynamic file `nvr.yml` in `/docker-storage/pv-stack/traefik/config/dynamic/` with two
routes on `Host(nvr.example.com)`: `/api/ws` and `/api/frame.jpeg` -> `http://172.20.0.1:8076`
(the authenticated relay; over HTTPS the browser requests the streams on the same origin, `GO2RTC =
location.origin` in app.js), the rest -> `http://172.20.0.1:8079`; `certResolver: cloudflare`.
In nvr-web: the `X-Forwarded-*` headers are trusted ONLY from localhost and the Docker networks
(`TRUSTED_PROXIES` in config.py if the proxy sits elsewhere); on HTTPS requests
HSTS and a `Secure` cookie are sent; CSP allows `wss://<host>`. The relay closes the connection
after each plain HTTP request (`Connection: close`), so the proxy does not reuse a
verified connection for another client. Internet access proper remains a network
decision (VPN/WireGuard on the router or a Cloudflare Tunnel) — the application is ready.

**Push notifications** (Web Push, `app/push.py`, `/api/push/*`): each user
subscribes their phone from "My account" (requires HTTPS or localhost and notification
permission; on iPhone, a PWA installed on the home screen). Level: critical only / warning+critical.
Any event logged with severity >= warning (`rules._journal`) reaches the subscribers
at the right level and with access to the camera (the account's camera restriction), with the link
`/#pb/<cam>/<day>/<sec>` straight into playback (click on the notification = jump to the moment in the open
window). The VAPID keys generate themselves (`data/push_vapid.json`, 0600); subscriptions
live in PostgreSQL (the `push_abonamente` table); an endpoint that answers 404/410 is
deleted by itself. Settings -> Notifications shows (to the admin) all subscribed devices.
Telegram remains the fallback channel, it does not depend on any phone.

## Operations: home/away/night mode, map, critical pop-up, I/O panel
- **System mode** (`app/mod.py`, `data/mod.json`): the 🏠 Home / 🔒 Away /
  🌙 Night switch in the header (admin and operator; the viewer only sees it). Rules can be valid
  only in certain modes (step 3, "Only in mode"), the mode change is a trigger
  (`mod`, value = the new mode) and an action ("switch the system to mode…"). It is switched from:
  the header, Telegram (`/acasa`, `/plecat`, `/noapte`, `/stare` — enabled from Settings →
  Notifications, "Accept commands", only from the configured chat), MQTT (`nvr/mod/set`, and
  the state is published retained on `nvr/mod`), the token API (`GET/POST /api/v1/mod`) or
  a rule. "Night automatically" (Settings) switches home → night at `night_start` and back at
  `night_end`; "away" is never touched automatically. Every change goes into the log (event
  `mod`) and into the audit, with who and from where.
- **Map** ("Map" tab, `app/harta.py`, `data/harta.json` + `data/harta/<plan>.png`):
  the property plan (sketch, satellite screenshot; PNG/JPEG/WebP ≤ 8 MB) with the cameras
  placed on it and their view cones. Markers show the state (green = recording, grey =
  not writing, blue = detection, pulsing red = alarm; a critical incident makes the marker
  pulse), click opens the HD view. Admin → "Edit": new plan, upload the image,
  click an unplaced camera to put it in the middle, drag it into place, the direction and aperture of
  the cone from the side panel. Multiple plans (yard, ground floor, upper floor) — the selector appears
  when there are at least two. A restricted account sees only its own cameras on the map.
- **Critical pop-up** (Live bar): on a new critical incident, the camera's tile enlarges for
  8 seconds and is brought into view (like "layout switch on event").
- **I/O panel** (the "I/O" button in the Live bar, admin and operator): siren, strobe,
  relay and voice announcement for all cameras that have them, in a single panel, with the
  announcement text written once at the top.

## Retroactive analytics: region, heat map, time-lapse, mask
All from the Recordings tab (`app/analiza.py`, `app/routes_analiza.py`), on the chosen camera and day:
- **Search by region**: you drag a rectangle on the frame and see all the day's detections whose
  boxes touched it (list on the right, yellow dots on the timeline, click = play). The normalised box
  (`chenar` = [x, y, w, h]) is attached when emitting `detectie` events
  (substream and HD region), `intrus` and `obiect` (the track's last box) — so the
  search covers events after 2026-10-23, not the old history.
- **Heat map**: people's paths (`traseu` from episodes) accumulated on a 64x36 grid
  over the frame, for the chosen day, 7 or 30 days.
- **Time-lapse**: the whole day in a minute (one frame per minute, 720p, downloaded MP4), as a
  job in the queue (a whole day takes a few minutes: ~0.5 s per frame, 4 in parallel).
  Frames are extracted by seeking in each segment, in parallel — NOT by
  reading the whole day (tens of GB of HEVC, even with `-skip_frame nokey` ffmpeg still
  reads them; the first version ran for 10+ minutes).
- **Privacy mask** (HD → the padlock button, admin; `app/masca.py`, `mask` in
  cameras.json): blacked-out rectangles on live, HD, playback (at any speed), clips,
  exports, the evidence sheet and the time-lapse. The recording on disk stays whole. In go2rtc
  the mask goes through per-camera templates (`m<id>`, `m<id>s2`...) generated by
  gen_go2rtc.py, because go2rtc refuses sources with spaces; the substream of a masked camera
  is transcoded on NVENC (no longer copied), and a masked camera plays at native
  resolution (the 720/1080 step does not apply to it). Detection photos on HD stay unmasked
  (internal).

## Data that survives: archive, encrypted nightly copy, chain of custody
- **Archive tier** (Settings → Recording, `arhiva_dir`; `record.py`): when the usual
  retention (`retention_days`) expires, the segments WITH events are MOVED to the second
  disk instead of being deleted (copy + SHA-256 check against the fingerprint taken at
  close + deletion from the primary); they live there for `arhiva_zile` (default 180), with the space guard
  `arhiva_min_free_gb`. The main disk's space guard also moves the segments with
  events to the archive instead of deleting them. `record._files()` sees both tiers, so
  playback, export, coverage and the time-lapse do not know the difference. Off when the directory
  is empty. Note: the 7.3 TB disk at /srv/nfs/disc2_8tb is NTFS mounted read-only and holds
  other data — to be used, it must be remounted rw (or another disk), a decision to make
  separately; Settings shows whether the directory exists and is writable.
- **Encrypted nightly copy** (Settings → Tools; `app/backup_nocturn.py`): daily at
  `backup_ora`, an encrypted tar.gz (Fernet, key from `data/backup_key`, 0600) with everything that
  cannot be rebuilt: the configuration, accounts, rules, incidents, case files, the mode, the map,
  the event log and the segment fingerprints (one consistent PostgreSQL snapshot), the face
  library (people + embeddings, without the downloadable models), the audit, the session key
  and the push keys. NOT included: the recordings, photos, the dataset, the semantic index (rebuildable).
  ~35 MB, ~6 s. Local in `backup_dir` (default `data/backup`, the last `backup_pastreaza`),
  then off-site through `backup_remote` (rsync to `user@host:/path` with openvigil's SSH key,
  or a mounted directory); if sending fails, Telegram announces it. "Make a copy
  now" runs as a job. The **key** is shown in Settings → Tools and must be written down elsewhere —
  without it the copy is useless. Restore: `python scripts/restaureaza-backup.py
  <file.tar.gz.enc> <destination> [--cheie ...]` (with the services stopped, destination `data/`).
  The restore test runs in pytest (`tests/test_backup_nocturn.py`: create → restore
  → compare, wrong key refused).
- **Chain of custody** (`app/lant.py`, the `segmente` table in PostgreSQL): nvr-record computes SHA-256
  for each segment one minute after it closes (newest first, 30 per minute,
  only the last 6 hours as backlog) and keeps the fingerprint with the time it was computed. The evidence
  sheet (`/api/export/report`) lists the clip's source segments with the hash from
  close and whether they are untouched NOW; `/api/segmente?channel&day` gives the list,
  `/api/segmente/verifica?channel&nume` recomputes. Moving to the archive is verified by
  the same fingerprint.

## Robustness and operations (the post-audit batch)
- **Spool for AI events**: when nvr-web is down (restart after settings, deploy),
  nvr-ai puts the events in `data/ai_spool.jsonl` and delivers them in order on return
  (with `moment_spool` in the data). No detection is lost in the restart minute any more.
- **No local models in web when nvr-ai is configured**: with `ai_service` on, nvr-web
  NO LONGER loads YOLO/SigLIP/VLM/Face ID/LPR locally if nvr-ai does not respond (it would have filled
  the VRAM and brought both down); the routes answer with an error until nvr-ai returns.
- **Atomic writes everywhere** (`store.atomic_write_json/text`): detect.json, embeddings,
  plates/known.json, go2rtc.yaml, restore from backup, log truncation. **Lock** on any
  read-modify-write of the camera registry (`EXT_LOCK`). **Monotonic camera IDs**
  (`data/camera_ids.json`): a new camera does not inherit the id (and thus the history) of a deleted one.
- **The recorder restarts** when the camera's connection changes (IP, port, user, password,
  vendor), not only when the camera list changes.
- **Camera scoping in a single place** (`app/scoping.py`): the id in the path
  (`/api/cam/<id>`, `/api/thumb/<id>`, `/api/cameras/<id>`), `channel`/`cam`/`camera` in
  the query or the JSON body; a test walks all the routes with a camera in the path.
- **API token only in the header** (`X-Token`); `?token=` is no longer accepted (it ended up in logs).
  A failed login no longer holds the thread for a second (the limit of 8/15 min per IP remains).
- **`/healthz`** (no authentication): 200 when go2rtc answers, the log can be read
  and recording is writing; otherwise 503 with details. **systemd watchdog**: nvr-web and nvr-record
  are `Type=notify` with `WatchdogSec` (180 s / 120 s) and send READY/WATCHDOG through
  `app/sdnotify.py` (no libraries) — a live but stuck process is restarted, not left for hours.

## What remained open after the audit: logs, messages, accessibility, encryption
- **Logs with levels** (`app/logare.py`): nvr-web, nvr-ai and nvr-record no longer write with
  `print`, but through `logging` (logger "nvr", on stderr, picked up by journald). The message
  text is unchanged (the `[record] ...` label stays), so `journalctl -u nvr-web`
  reads the same; in addition each message has a level. `Environment=NVR_LOG_LEVEL=WARNING`
  in the systemd unit (`systemctl edit nvr-web`) shows only problems; `DEBUG` shows everything.
- **Server messages have diacritics**: the JSON errors (`error`, `mesaj`, `hint`, `motiv`),
  job texts, notifications and audit entries are in correct Romanian, like the
  interface. JSON keys and "code" values (states, types) stay ASCII.
- **Accessibility**: all fields and lists in the interface have `aria-label`; the time
  bar in Recordings and the one in HD are `role="slider"` with `aria-valuenow`/`aria-valuetext`
  (current time) and can be focused from the keyboard; the dots on the bar (events/detections/
  rules) have sufficient contrast on the dark background.
- **Encryption at rest** (classic installation only; in the Docker variant encryption is done at the host level,
  with LUKS or fscrypt on the `data/` directory, and the Settings card and the scripts below do not exist)
  **: fscrypt on all of `data/`, with a master key and automatic start**
  (2026-10-23, with the intermediate step "password after reboot" replaced the same evening, after
  the decision: after a power outage everything must come back by itself). Not in the application: ffmpeg
  needs readable MP4s, and the manufacturers (Hikvision, Dahua, UniFi) either do not encrypt at all
  or keep the key on the device. With fscrypt (ext4 `encrypt`, policy v2, AES-256-XTS) everything in
  `data/` — recordings, photos, `cameras.json` with the camera passwords, `users.json`,
  the PostgreSQL datadir (`pg/`), faces, plates, `go2rtc.yaml` (moved here from `go2rtc/`) — is encrypted
  transparently; processes see everything in clear as long as the key is in the kernel.
  - **The master key** `/etc/nvr/master.key` (root, 0600, 32 random bytes) is the
    `nvr-auto` protector; `nvr-unlock.service` (oneshot, before nvr-go2rtc/web/record/ai, which
    have a drop-in `After=/Wants=`) unlocks `data/` at boot. **Whoever takes the disk together with the machine
    reads the data** (the key is on it): the protection is against copies/clones of the partition and for
    files read without root, the same compromise as the manufacturers. A copy of the key on a
    USB stick kept elsewhere saves you if the system disk dies.
  - **The fallback password** (a password protector, it does not sit on the machine): unlocks manually when the
    key is missing (`sudo fscrypt unlock data`), or from the interface's yellow banner. It is
    set/replaced from **Settings -> System -> Disk encryption**, with the admin account's password
    (re-authentication); the card also shows the status: encrypted, unlocked, key, unit.
  - **The assistant** `sudo scripts/criptare.sh` (interactive: explains each step, asks,
    does it) enables encryption on a new installation: fscrypt, the ext4 feature, the key,
    the protector, moving `data/`, the units. The interface runs the same script through sudo
    (`/usr/local/sbin/nvr-criptare`, a root copy, the only command in
    `/etc/sudoers.d/nvr-criptare`) for `stare`, `parola`, `deblocheaza`. Activation is NOT
    done from the interface: it moves the very directory the server runs from.
  - **If automatic unlocking fails** (missing key, broken unit): nvr-record does not crash
    (`record.blocat()` = file creation gives ENOKEY; it announces on Telegram, READY + watchdog,
    starts by itself after unlocking), `/healthz` gives 503 with `blocat`, and nvr-web starts only
    after unlocking (it needs `users.json`): you unlock over SSH with the fallback password.
  - **Migration**: `scripts/fscrypt_migrare.sh` (the recordings, camera by camera, with
    recording running) and `scripts/fscrypt_date.sh` (the rest of `data/`, 4-5 minutes without
    services; the recordings move by rename, same policy). The hashes in the chain of
    custody remain valid.
- **Logout is safe** (`SESSION_REFRESH_EACH_REQUEST=False`): the session cookie is
  sent only when the session changes. Before, Flask re-sent it on every response, and a delayed
  response to a request started before `/logout` reinstated the old session (found
  by the 2FA test after the unlock banner added a request at page load).
  The 30-day expiry runs from login, not sliding.

## Interface: per-tab polling, grid paused under HD, Esc, PTZ keys, phone
- Alarm/detection states (`/api/events`) are requested only when visible (Live, Map, HD) —
  not from Settings or Events. Under the HD overlay, the grid streams stop
  (`gridPause`) and restart on close; HD keeps its own substream underneath.
- **Esc** closes HD and any visible overlay (mask, region, heat map, zone,
  lines, add camera, case files, summary, clip, photo) from a single place.
- **PTZ from the keyboard** in HD (admin/operator): arrows rotate, `+`/`-` (or PageUp/
  PageDown) zoom, stopping on key release with the 4 s safety stop —
  only on cameras that actually rotate / have a motorised lens.
- The toast has `role=status`/`aria-live`; critical incidents and errors are `role=alert`.
- On touch screens (`pointer: coarse`) the navigation buttons, the Live bar, and the playback
  and camera controls are at least 44 px.
- **Diacritics** in all visible text (page, messages, login pages); the code keys
  sent to the server stay ASCII.
- **The interface is in real ES modules**, by screen, in `app/static/js/`: `00-core`
  (html``, toast, session, jobs, label tables), `05-navigare`, `10-live`, `20-hd`,
  `25-harta`, `30-redare`, `40-evenimente`, `50-camere`, `60-setari`, `70-ai` and `state.js`.
  `app/static/app.js` is only the entry point: it imports the modules in order and starts the application
  (`loadCameras().then(routeHash)`). Rules: the core imports from no screen (it
  is evaluated first, so `$`, `html`, `ME` exist when the screens run their load-time code);
  screens may import each other (live bindings: an exported `let` is seen
  with its current value); variables WRITTEN from several modules live in `state.js` as
  `S.<name>` (`S.cameras`, `S.currentView`, `S.pbExtra`, `S.pendingProba`, `S.popIgnora`),
  because ES does not let a module assign an import. `scripts/module_check.py` (and
  `tests/test_module_check.py`) checks imports/exports, the core rule and syntax.
  When you add a function used from another screen: you put it in `export { ... }` at the end of the
  module and import it explicitly.
- Not done, with reason: encrypting the recordings on disk (it would break ffmpeg's concat/seek
  and direct playback; the backup is encrypted, and the disk is inside the house). (Solved
  later through fscrypt.)

## Multi-vendor: hik / onvif drivers
Each camera has a `vendor` in the registry (`hik` by default, `onvif` for the rest), and everything that
depends on the manufacturer goes through `app/drivers.py`: the RTSP URLs (recording, grid, HD,
the health check), the high-resolution photo (Face ID, intruder confirmation),
identification and ping (watchdog), capabilities, PTZ + presets, the relay, time
and events. `hik` delegates to `camctl.py` (ISAPI, unchanged); `onvif` uses
`app/onvif.py`, a minimal SOAP client (WS-Security UsernameToken, then HTTP Digest) with no new
libraries: GetDeviceInformation, GetServices/GetCapabilities, GetProfiles +
GetStreamUri (the largest profile = main, the next = sub), GetSnapshotUri, PTZ
ContinuousMove/Stop, presets, relays (DeviceIO), time. The URLs found at add time
are kept in `onvif: {main, sub, poza, port}` on the camera.
What does NOT exist on ONVIF (and the interface does not show the buttons): siren, strobe, speaker
(ISAPI two-way audio), setting the stream quality, time synchronisation, the rotation test,
its own events (alertStream) — AI detection on the stream covers that anyway. Adding: the button
"Add camera" → "Other manufacturer (ONVIF)" + the HTTP port; the scanner identifies ONVIF with the
same credentials and imports them with `vendor: onvif`. With the privacy mask and the
rest of the playback features, an ONVIF camera behaves identically (the stream is still RTSP).
Tested against a fake SOAP server (`tests/test_onvif.py`); on a real camera, the first thing to
check is that GetStreamUri returns an RTSP reachable from the server.

## Login, detection zones, PWA, statistics
- **Mandatory login, with accounts and roles** (`data/users.json`, chmod 600, hashed
  passwords): on first start /setup creates the administrator account. The **viewer**
  role sees only Live/Recordings/Alarms for the chosen cameras or groups
  (Settings -> System -> Users); the server refuses any POST/DELETE from it (403), not
  just hides the buttons. The **operator** role (the guard) sees the same AND can act
  live on their cameras — siren, talk, strobe, relay, PTZ, positions (no
  deletion) — but no configuration: the `OPERATOR_ACTIONS` allowlist in server.py. Protections: CSRF on all changes (X-CSRF header),
  HttpOnly+SameSite cookie, rate limiting on login (8 failures/15 min per IP),
  logins recorded in the audit.
  XSS: everything that goes into innerHTML passes through `html\`\`` (app.js), which automatically escapes any
  interpolated value; the server sends a Content-Security-Policy (scripts only ours,
  streams only through the :8076 relay), X-Frame-Options, nosniff. The /api/v1 API stays on the X-Token token.
  go2rtc listens only on localhost: streams reach the browser through the :8076 relay in
  nvr-web, which requires the same session (restricted viewers see only their cameras).
- **My account** (click the user name, top; any role): two-step authentication,
  push notifications on the current device, sign out the other devices.
  - **2FA (TOTP)**: QR scanned in Google Authenticator/Aegis/1Password, confirmed with a code;
    8 single-use recovery codes (shown only once). At login, after the
    password, /login/2fa asks for the code (5 minutes, same rate limit). Codes already used
    are refused (anti-replay). The admin can remove 2FA from any account (Settings ->
    Users -> "remove 2FA"). Implemented in `app/totp.py`, no dependencies.
  - **Revocable sessions**: the cookie carries the account's version (`sess_ver` in users.json);
    changing the password, "sign out all devices" or "sign out" from the accounts
    list increments it and ALL old sessions die. The session lasts 30 days from the
    last use. On HTTPS the cookie gets `Secure` (per request, LAN over HTTP keeps
    working).
  - **Push**: see "Push notifications" below.
- **The change log** (Settings -> Log) records the user (not the IP; the IP appears
  on hover) and, as a safety net, any successful POST/PUT/PATCH/DELETE that the route did not
  audit explicitly ("POST request /api/..." with the body, no secrets). 20,000 entries.
- **Detection zones**: the ▣ button (in the camera's HD bar) opens the editor — you draw the
  polygon on a live frame; only detections with their centre in the zone count.
- **PWA**: on a phone "Add to Home Screen" -> standalone app with an icon.
- **Statistics**: a mini-histogram by hour in the Alarms tab (respects the active filters).
- **⛏ Extract frames from recordings** (Settings): 1 frame/min from all local
  recordings -> data/dataset, for training.
NVR config (IP/user/password, web port, playback height): `config.py` (chmod 600).
After you change `config.py`: `systemctl restart nvr-go2rtc nvr-web`.

## GPU (transcoding acceleration)
**Active**: the whole video chain runs on the GPU (RTX 2000 Ada: NVDEC decode +
h264_nvenc encode) — see `journalctl -u nvr-web | grep hwaccel` ("DA"). Without the card,
the system falls back automatically to the CPU (libx264, accelerated playback limited to 2x).
