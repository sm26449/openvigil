#!/usr/bin/env python3
"""Setari editabile din UI, persistate in data/settings.json (chmod 600 — contine parole).
Fallback la valorile din config.py pentru instalarile existente."""
import os
import threading

import config as C
import store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILE = os.path.join(ROOT, "data", "settings.json")
_lock = threading.Lock()

DEFAULTS = {
    # Telegram
    "telegram_token": getattr(C, "TELEGRAM_TOKEN", ""),
    "telegram_chat": getattr(C, "TELEGRAM_CHAT_ID", ""),
    # adresa publica a interfetei (ex. http://192.168.1.2:8079): cu ea, notificarile
    # primesc o legatura direct in redare la momentul evenimentului (#pb/cam/zi/sec)
    "web_url": "",
    # comenzi prin Telegram (/acasa, /plecat, /noapte, /stare) — pornit explicit, ca sa nu
    # se bata pe getUpdates cu alt proces care ar folosi acelasi bot
    "telegram_comenzi": False,
    # modul trece singur acasa -> noapte la night_start si inapoi la night_end
    "mod_auto_noapte": False,
    # NVR-ul Hikvision e scos din functiune: nu mai e supravegheat, nu mai apare in Stare si
    # nu se mai incearca nimic prin el (istoric vechi, rezerva de inregistrare/export, sync
    # de setari pe canalele lui). Camerele inregistreaza direct aici. Reversibil.
    "nvr_retras": False,
    # Email (SMTP)
    "email_enabled": False,
    "email_smtp": "",
    "email_port": 587,
    "email_user": "",
    "email_pass": "",
    "email_to": "",
    # Ce detectii trimit notificare + cat de des
    "notify_labels": list(getattr(C, "NOTIFY_LABELS", ["dog", "cat", "person", "bear"])),
    "notify_cooldown": int(getattr(C, "NOTIFY_COOLDOWN", 300)),
    # Ce detectii declanseaza sirena automata (pe camerele cu automatizarea pornita)
    "auto_siren_labels": ["dog"],
    # Ce sunet se reda automat: id 1-12 = sunet din firmware, "c1".."c3" = sunet propriu
    "auto_siren_sound": 1,
    "auto_siren_volume": 90,
    "auto_siren_times": 3,
    # Hash-ul parolei de interfata (gol = fara login). Setat din UI, niciodata expus.
    "web_password": "",
    # Re-encodare inregistrari vechi (necesita encoder hardware HEVC/AV1)
    "reencode_enabled": False,
    # Vizualizari salvate: combinatii de filtru + aranjare, reutilizabile cu un click.
    # [{name, group, q, cols, preview, ids?}] — "ids" fixeaza o selectie manuala de camere.
    # Retentie (zile). Segmentele care contin evenimente se pastreaza mai mult decat
    # cele "goale" — o inregistrare cu ceva in ea valoreaza mai mult decat ore de liniste.
    "retention_days": 14,          # inregistrare continua, fara evenimente
    "retention_event_days": 60,    # segmente care contin detectii sau alarme
    "retention_snapshots_days": 90,  # pozele din tabul Alarme (ocupa foarte putin)
    "retention_min_free_gb": 50,   # sub acest prag se sterge, indiferent de varsta
    # nivelul de arhiva (un al doilea disc): segmentele cu evenimente se muta acolo cand
    # expira retentia primara, in loc sa se stearga; gol = oprit
    "arhiva_dir": "",
    "arhiva_zile": 180,
    "arhiva_min_free_gb": 20,
    # copia nocturna criptata (backup_nocturn.py): ora, directorul local, destinatia
    # externa (rsync: user@host:/cale sau un director montat), cate copii pastram local
    "backup_ora": "03:30",
    # rezumatul zilei pe Telegram, o data pe zi, la ora data (digest.start in nvr-web)
    "digest_telegram": False,
    "digest_ora": "21:00",
    "backup_dir": "",              # gol = data/backup
    "backup_remote": "",
    "backup_pastreaza": 14,
    "backup_fete": True,           # biblioteca de fete (persoane + embeddings, fara modele)
    "events_keep_days": 365,       # jurnalul de evenimente (SQLite, ~45MB/luna)
    # Alarma de zgomot (pe camerele cu microfon). Pragurile sunt in dBFS: 0 = maxim,
    # -60 = foarte liniste. Fondul obisnuit al unei curti e in jur de -45 dBFS.
    "webhook_urls": [],          # adresele catre care trimitem evenimente (Node-RED etc.)
    "webhook_secret": "",        # daca e setat, semnam corpul cu HMAC-SHA256
    "webhook_events": ["detectie", "alarma_camera", "zgomot", "camera_offline",
                       "camera_online", "gol_inregistrare", "sirena"],
    "api_token": "",             # jeton pentru comenzile venite din afara
    "incident_gap_sec": 300,     # cat de departe pot fi doua cadre si tot o intamplare
    # Recunoastere persoane (Face ID). Pragul e similaritate cosinus ArcFace:
    # aceeasi persoana da de regula 0.5-0.8, persoane diferite sub 0.3.
    "face_enabled": False,
    "face_threshold": 0.45,
    "face_notify": True,         # notificare Telegram/email cu numele recunoscut
    "face_unknown_save": True,   # pastreaza fetele nerecunoscute pentru etichetare
    "face_antispoof": True,      # respinge pozele/ecranele aratate camerei (MiniFASNet)
    "face_antispoof_conf": 0.7,  # respinge doar peste aceasta incredere a ansamblului
    # Descrierea persoanelor detectate (VLM local): "barbat ~40 ani, geaca neagra..."
    "person_describe": False,
    # Re-ID pe corp (OSNet): fata ancoreaza identitatea, corpul o propaga pe
    # camerele urmarite — valabil O ZI (infatisarea = hainele de azi)
    "reid_enabled": True,
    # Camerele cu urmarire de obiecte (ByteTrack, numarare exacta) — opt-in, e scump
    "track_cams": [],
    # Urmarirea ruleaza pe CPU implicit: fiecare lucrator tine o instanta proprie de
    # model (ByteTrack cu stare) si pe GPU-ul de 16GB nu incap alaturi de detectie +
    # Face ID + LPR + SigLIP + Qwen. CPU-ul (28 nuclee) are loc. Pune True doar daca
    # ai VRAM liber (ex. cand urmarirea o face nvr-ai cu UN model partajat).
    "track_gpu": False,
    # Urmarirea o face serviciul separat nvr-ai (un model partajat), nu lucratorii
    # locali din nvr-web. Cand e True: web-ul opreste lucratorii lui de track si ia
    # starea de la nvr-ai; nvr-ai emite evenimentele "obiect" catre web.
    "ai_service": True,
    # Analitica peste urmarire (nvr-ai): linii de numarare, zone de intrare, viteza.
    # count_lines = {cam: [{name, p1:[x,y], p2:[x,y]}]} normalizate; la traversare ->
    # eveniment "trecere". count_zones = {cam: [{name, poly:[[x,y]...]}]} -> "intrare_zona".
    # speed_cal = {cam: metri_per_pixel} pentru km/h (optional).
    "count_lines": {},
    "count_zones": {},
    "speed_cal": {},
    # Ce escaladeaza la eveniment "intrus" (alarma de perimetru confirmata de AI), per
    # camera: "persoane" (doar oameni — implicit; vehiculele sunt trafic normal),
    # "strict" (orice om/vehicul — pentru perimetre reale, ex. camp/livada), "noapte"
    # (orice, dar doar noaptea), "off" (deloc). {cam_id: mod}.
    "intrus_mode": {},
    # Detectie de obiect nou/disparut pe camere STATICE (nvr-ai): fundal-median lent +
    # persistenta. Camerele alese emit "obiect_nou"/"obiect_disparut" cand ceva e lasat sau
    # luat si ramane asa peste prag. Trebuie sa fie si in track_cams (de acolo iau cadrele).
    "scene_cams": [],
    "scene_persist_min": 30,     # cate minute trebuie sa persiste schimbarea ca s-o raportam
    "scene_roi_top": 0.35,       # fractia de sus a cadrului ignorata (cer/copaci = fals-pozitive)
    # Motion-gating: nu trecem prin YOLO o scena care nu s-a schimbat (mai putine
    # fals-pozitive de zgomot + headroom la scalare). Heartbeat periodic prinde staticul.
    "motion_gate": True,
    # Detectie pe regiuni: pe camerele alese, decupam regiunea de miscare din cadrul HD
    # si o inferam separat — prinde obiectele mici la distanta (strada). Opt-in, cere HD.
    "tile_cams": [],
    # Intervalul de noapte — ridica severitatea evenimentelor (persoana necunoscuta
    # noaptea = critic, ziua = atentie). Trece corect peste miezul noptii.
    "night_start": "22:00",
    "night_end": "06:00",
    # Citire numere de inmatriculare (LPR), pe camerele cu detectia pornita.
    # Pragul e media increderii OCR per caracter (0..1).
    "lpr_enabled": False,
    "lpr_min_conf": 0.85,        # sub prag: ignorat
    "lpr_sure_conf": 0.90,       # peste: o singura citire ajunge; intre praguri: cere doua
                                 # citiri ale aceluiasi numar pe aceeasi camera in 30 s
    "lpr_notify_known": True,    # notificare la masinile cu nume
    "lpr_notify_all": False,     # notificare la ORICE numar citit (galagie la strada)
    "lpr_notify_unknown": False, # notificare la numerele FARA nume (masina straina)
    "lpr_alert_cams": [],        # ...doar pe camerele astea (gol = toate); ex. poarta
    # Cautare semantica in alarme (SigLIP multilingual, index in data/semantic/)
    "semantic_enabled": False,
    # Puntea MQTT (evenimente publicate + actiunea "mqtt" din reguli)
    "mqtt_enabled": False,
    "mqtt_host": "",
    "mqtt_port": 1883,
    "mqtt_user": "",
    "mqtt_pass": "",
    "mqtt_topic": "nvr",
    "noise_alarm_enabled": False,
    "noise_threshold_db": -25,   # peste atat inseamna "tare"
    "noise_sustain_sec": 5,      # cat trebuie sa tina ca sa fie alarma
    "noise_spike_db": 18,        # crestere fata de fondul obisnuit = varf brusc
    # Serverul de timp pentru camere (gol = doar setam ora direct, fara NTP)
    "ntp_server": "",
    "views": [],
    "default_view": "",
}


def _read():
    """Config complet de pe disc (fara lock — apelantul il detine daca e nevoie)."""
    saved = store.load_json(FILE, {}) or {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in saved.items() if k in DEFAULTS})
    return out


def load():
    with _lock:
        return _read()


def get(key):
    return load()[key]


def nvr_retras():
    """NVR-ul Hikvision e scos din functiune (comutatorul din Setari -> Sistem)."""
    return bool(load().get("nvr_retras")) or not getattr(C, "NVR_IP", "")


def update(new):
    # TOT read-modify-write sub acelasi lock: doua POST-uri de setari concurente nu-si mai
    # pierd campuri (inainte, load() era in afara lock-ului). Scriere ATOMICA: un crash la
    # mijloc nu lasa settings.json gol. Doar nvr-web scrie; nvr-ai citeste -> os.replace ii
    # da mereu o versiune intreaga (nu e nevoie de lock cross-proces).
    with _lock:
        cur = _read()
        cur.update({k: v for k, v in new.items() if k in DEFAULTS})
        store.atomic_write_json(FILE, cur, mode=0o600, indent=2)
    return cur
    return cur
