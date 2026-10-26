#!/usr/bin/env python3
"""Copia nocturna CRIPTATA a starii: tot ce nu se poate reface (configuratie, conturi,
reguli, jurnalul de evenimente, marcaje, dosare, biblioteca de fete, harta, amprentele
segmentelor), intr-o arhiva tar.gz criptata (Fernet: AES-128-CBC + HMAC), cu cheia
instalarii din data/backup_key (generata o data, 0600 — de pastrat SEPARAT, altfel
copia e inutila). Inregistrarile, pozele si modelele descarcabile NU intra.

Local: `backup_dir` (implicit data/backup), ultimele `backup_pastreaza` copii. In afara
casei: `backup_remote` = tinta rsync (user@host:/cale, cheia SSH a utilizatorului
serviciului) sau un director montat; copia pleaca imediat dupa ce e scrisa local.
Ruleaza zilnic la `backup_ora` dintr-un fir al nvr-web, sau la cerere (job „backup").
Refacerea: `restaureaza(fisier, cheie, destinatie)` — testata in tests/test_backup_nocturn.py
(creare -> refacere -> comparare), adica „testul de restaurare in CI".
"""
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import threading
import time
import logare
from i18n import t

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
KEY_FILE = os.path.join(DATA, "backup_key")
STARE_FILE = os.path.join(DATA, "backup_stare.json")
PREFIX = "nvr-backup-"
# ce intra: fisiere si directoare RELATIVE la data/ (cele care lipsesc se sar)
FISIERE = ["cameras.json", "settings.json", "detect.json", "users.json", "rules.json",
           "incidents.json", "cases.json", "mod.json", "harta.json", "continuity.json",
           "stationar.json", "rule_last_fire.json", "noise_stats.json", "changes.jsonl",
           "secret_key", "push_vapid.json", "vmd_backup.json", "stream_backup.json"]
DIRECTOARE = ["plates", "harta", "cases"]     # NU semantic/ (indexul SigLIP, ~2 GB, se reface)
FETE = ["faces/persons", "faces/embeddings.json", "faces/log.jsonl", "faces/unknown"]
BAZE = ["events.db", "segmente.db"]          # prin API-ul de backup SQLite (copie consistenta)
_lock = threading.Lock()


def cheie(genereaza=True):
    """Cheia Fernet a instalarii (base64, 44 caractere)."""
    try:
        with open(KEY_FILE) as f:
            k = f.read().strip()
            if k:
                return k
    except FileNotFoundError:
        pass
    if not genereaza:
        return None
    from cryptography.fernet import Fernet
    k = Fernet.generate_key().decode()
    fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(k + "\n")
    return k


def amprenta_cheie(k=None):
    k = k or cheie()
    return hashlib.sha256(k.encode()).hexdigest()[:12]


def backup_dir():
    import settings
    return (settings.get("backup_dir") or "").strip() or os.path.join(DATA, "backup")


def _copie_sqlite(cale, dest):
    import sqlite3
    src = sqlite3.connect(cale, timeout=15)
    dst = sqlite3.connect(dest)
    with dst:
        src.backup(dst)
    src.close()
    dst.close()


def _adauga(tar, cale_abs, nume_arh):
    if os.path.isdir(cale_abs):
        for rad, _, fis in os.walk(cale_abs):
            for f in fis:
                p = os.path.join(rad, f)
                tar.add(p, arcname=os.path.join(nume_arh, os.path.relpath(p, cale_abs)))
    elif os.path.isfile(cale_abs):
        tar.add(cale_abs, arcname=nume_arh)


def creeaza(dest_dir=None, fete=None, data_dir=None, progres=None):
    """Scrie <dest>/nvr-backup-YYYYMMDD-HHMMSS.tar.gz.enc (+ .json manifest). Intoarce manifestul."""
    import settings
    from cryptography.fernet import Fernet
    data_dir = data_dir or DATA
    dest_dir = dest_dir or backup_dir()
    if fete is None:
        fete = bool(settings.get("backup_fete"))
    os.makedirs(dest_dir, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    nume = f"{PREFIX}{stamp}.tar.gz.enc"
    buf = io.BytesIO()
    continut = []
    tmpdb = []
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for rel in FISIERE + DIRECTOARE + (FETE if fete else []):
            p = os.path.join(data_dir, rel)
            if os.path.exists(p):
                _adauga(tar, p, rel)
                continut.append(rel)
        for rel in BAZE:
            p = os.path.join(data_dir, rel)
            if os.path.exists(p):
                tmp = os.path.join(dest_dir, f".{rel}.{stamp}.tmp")
                _copie_sqlite(p, tmp)
                tmpdb.append(tmp)
                tar.add(tmp, arcname=rel)
                continut.append(rel)
        if progres:
            progres(40, t("arhiva construita"))
    for tmpf in tmpdb:
        try:
            os.remove(tmpf)
        except OSError:
            pass
    clar = buf.getvalue()
    sha = hashlib.sha256(clar).hexdigest()
    cript = Fernet(cheie().encode()).encrypt(clar)
    cale = os.path.join(dest_dir, nume)
    tmp = cale + ".part"
    with open(tmp, "wb") as f:
        f.write(cript)
    os.chmod(tmp, 0o600)
    os.replace(tmp, cale)
    manifest = {"fisier": nume, "creat": stamp, "octeti": len(cript), "octeti_clar": len(clar),
                "sha256_clar": sha, "cheie": amprenta_cheie(), "continut": continut,
                "versiune": 1}
    with open(cale[:-len(".tar.gz.enc")] + ".json", "w") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
    if progres:
        progres(70, t("criptată și scrisa local"))
    return manifest


def lista(dest_dir=None):
    """Copiile locale, cele mai noi primele: [{fisier, creat, octeti, manifest}]."""
    dest_dir = dest_dir or backup_dir()
    out = []
    try:
        for fn in os.listdir(dest_dir):
            if fn.startswith(PREFIX) and fn.endswith(".tar.gz.enc"):
                p = os.path.join(dest_dir, fn)
                m = {}
                try:
                    with open(p[:-len(".tar.gz.enc")] + ".json") as f:
                        m = json.load(f)
                except (OSError, ValueError):
                    pass
                out.append({"fisier": fn, "octeti": os.path.getsize(p),
                            "creat": m.get("creat") or fn[len(PREFIX):-len(".tar.gz.enc")],
                            "continut": m.get("continut", []), "cheie": m.get("cheie")})
    except FileNotFoundError:
        pass
    out.sort(key=lambda x: x["fisier"], reverse=True)
    return out


def roteste(dest_dir=None, pastreaza=14):
    """Pastreaza ultimele `pastreaza` copii locale."""
    dest_dir = dest_dir or backup_dir()
    sterse = 0
    for b in lista(dest_dir)[max(1, int(pastreaza)):]:
        for suf in (".tar.gz.enc", ".json"):
            try:
                os.remove(os.path.join(dest_dir, b["fisier"][:-len(".tar.gz.enc")] + suf))
            except OSError:
                pass
        sterse += 1
    return sterse


def trimite(cale, remote, run=subprocess.run):
    """Copia pleaca in afara casei: rsync la user@host:/cale (SSH neinteractiv) sau copiere
    intr-un director montat. Intoarce (ok, mesaj)."""
    remote = (remote or "").strip()
    if not remote:
        return None, t("fara destinatie externa")
    fisiere = [cale, cale[:-len(".tar.gz.enc")] + ".json"]
    if ":" in remote and not os.path.isdir(remote):
        cmd = ["rsync", "-a", "--partial", "--timeout=120",
               "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15", *fisiere, remote.rstrip("/") + "/"]
        try:
            r = run(cmd, capture_output=True, text=True, timeout=1800)
        except Exception as e:
            return False, f"rsync: {str(e)[:120]}"
        return (r.returncode == 0), (t("trimis") if r.returncode == 0 else (r.stderr or t("rsync a esuat"))[-200:])
    try:
        os.makedirs(remote, exist_ok=True)
        for f in fisiere:
            shutil.copy2(f, os.path.join(remote, os.path.basename(f)))
        return True, t("copiat")
    except OSError as e:
        return False, t("copiere: {e}", e=str(e)[:120])


def restaureaza(cale, k, dest_dir, manifest=None):
    """Decripteaza si despacheteaza o copie in dest_dir (un director GOL sau data/ cu
    serviciile oprite). Verifica SHA-256 fata de manifest daca e dat. Intoarce lista."""
    from cryptography.fernet import Fernet, InvalidToken
    with open(cale, "rb") as f:
        cript = f.read()
    try:
        clar = Fernet((k or "").strip().encode()).decrypt(cript)
    except (InvalidToken, ValueError):
        raise ValueError(t("cheie greșită sau fișier corupt"))
    if manifest and manifest.get("sha256_clar") and hashlib.sha256(clar).hexdigest() != manifest["sha256_clar"]:
        raise ValueError(t("continutul nu corespunde manifestului (sha256)"))
    os.makedirs(dest_dir, exist_ok=True)
    out = []
    with tarfile.open(fileobj=io.BytesIO(clar), mode="r:gz") as tar:
        for m in tar.getmembers():
            if m.name.startswith("/") or ".." in m.name.split("/"):
                raise ValueError(t("cale periculoasă în arhiva: {cale}", cale=m.name))
        tar.extractall(dest_dir, filter="data")
        out = [m.name for m in tar.getmembers() if m.isfile()]
    return out


def _stare(d):
    try:
        import store
        store.atomic_write_json(STARE_FILE, d, mode=0o600, indent=2)
    except OSError:
        pass


def stare():
    try:
        with open(STARE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def ruleaza(progres=None, cine="program"):
    """O copie completa: creare, rotatie, trimitere externa, stare + audit. Intoarce manifestul
    (+ `extern`)."""
    import settings
    with _lock:
        t0 = time.time()
        try:
            m = creeaza(progres=progres)
            roteste(pastreaza=int(settings.get("backup_pastreaza") or 14))
            ok_ext, msg_ext = trimite(os.path.join(backup_dir(), m["fisier"]), settings.get("backup_remote"))
            m["extern"] = {"ok": ok_ext, "mesaj": msg_ext}
            m["durata_s"] = round(time.time() - t0, 1)
            m["de"] = cine
            _stare({"ultima": m, "ok": True, "la": time.time()})
            try:
                import audit
                audit.log("copie de siguranță", m["fisier"], {"octeti": m["octeti"], "extern": msg_ext}, who=cine)
            except Exception:
                pass
            if ok_ext is False:
                try:
                    import notify
                    notify.send(t("⚠️ Copia de siguranță s-a scris local, dar NU a plecat în afara: {motiv}", motiv=msg_ext),
                                key="backup-extern")
                except Exception:
                    pass
            if progres:
                progres(100, t("gata"))
            return m
        except Exception as e:
            _stare({"ok": False, "eroare": str(e)[:200], "la": time.time()})
            try:
                import notify
                notify.send(t("⚠️ Copia de siguranță a EȘUAT: {e}", e=str(e)[:120]), key="backup-esuat")
            except Exception:
                pass
            raise


def start():
    """Firul zilnic: la `backup_ora` (HH:MM), o data pe zi."""
    def bucla():
        import settings
        ultima_zi = stare().get("ultima", {}).get("creat", "")[:8]
        # la pornire NU facem o copie „pentru ca a trecut ora": serviciul reporneste des;
        # daca ora de azi a trecut deja, prima copie e maine la ora ei
        if time.strftime("%H:%M") >= (settings.get("backup_ora") or "03:30").strip():
            ultima_zi = time.strftime("%Y%m%d")
        while True:
            try:
                import settings
                ora = (settings.get("backup_ora") or "03:30").strip()
                azi = time.strftime("%Y%m%d")
                if time.strftime("%H:%M") >= ora and ultima_zi != azi:
                    ultima_zi = azi
                    ruleaza()
            except Exception as e:
                logare.info(f"[backup] {str(e)[:80]}")
            time.sleep(60)
    threading.Thread(target=bucla, daemon=True, name="backup-nocturn").start()
