#!/usr/bin/env python3
"""Blueprint: antrenare model (fine-tune local, data/finetune) + Face ID.

Grup extras din server.py (audit #8). Rutele de train copiaza best.pt din data/finetune/runs peste
MODEL_PATH; cele de faces gestioneaza biblioteca de fete. Globale/helper-uri partajate
(_latest_trained, MODEL_PATH, FINETUNE_DIR) stau AICI — doar rutele de aici le folosesc.
Restul rutelor AI (semantic, plates, ask, search, rules, track) sunt in routes_analytics.
"""
import glob
import os
import re
import shutil
import time

from flask import Blueprint, jsonify, request, send_file

import adnotare
import ai_client
import audit
import describe
import detect
import faces
from i18n import t
import record
import settings
from server import ROOT, _visible_ids, current_user, load_ext

bp = Blueprint("ai", __name__)

DATASET_DIR = os.path.join(ROOT, "data", "dataset")        # cadre curate, materia prima
FINETUNE_DIR = os.path.join(ROOT, "data", "finetune")      # dataset etichetat + runs/
MODEL_PATH = os.path.join(ROOT, "data", "yolo11n.pt")


def _runs():
    """Antrenamentele locale: [{nume, data, best}] (data/finetune/runs/<nume>/weights/best.pt),
    cele mai noi primele."""
    out = []
    for best in glob.glob(f"{FINETUNE_DIR}/runs/*/weights/best.pt"):
        out.append({"nume": os.path.basename(os.path.dirname(os.path.dirname(best))),
                    "data": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(best))),
                    "best": best})
    return sorted(out, key=lambda r: r["data"], reverse=True)


def _latest_trained():
    """Cel mai nou best.pt produs de un antrenament local (sau None)."""
    r = _runs()
    return r[0]["best"] if r else None


def _n_finetune():
    """Cate imagini are datasetul etichetat (train + val)."""
    n = 0
    for sub in ("train", "val"):
        d = os.path.join(FINETUNE_DIR, "images", sub)
        n += len(os.listdir(d)) if os.path.isdir(d) else 0
    return n


@bp.route("/api/train/status")
def api_train_status():
    runs = _runs()
    n_collected = len(os.listdir(DATASET_DIR)) if os.path.isdir(DATASET_DIR) else 0
    bak = MODEL_PATH + ".bak"
    return jsonify({
        "collected": n_collected, "finetune": _n_finetune(),
        "harvesting": _harvest["running"], "harvested": _harvest["made"],
        "model_date": time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(MODEL_PATH))),
        "trained": bool(runs),
        "trained_date": runs[0]["data"] if runs else None,
        "runs": [{"nume": r["nume"], "data": r["data"]} for r in runs],
        "backup": os.path.exists(bak),
        "backup_date": (time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(bak)))
                        if os.path.exists(bak) else None),
        "job": ai_client.train_stare() if ai_client.remote() else None,   # None = nvr-ai oprit
    })


@bp.route("/api/train/dataset", methods=["POST"])
def api_train_dataset():
    """Construieste datasetul etichetat (teacher yolo11x + SigLIP pe specii) in nvr-ai.
    Body: {max?} — cel mult atatea cadre (esantion); 0 = toate (poate dura ore)."""
    if not ai_client.remote():
        return jsonify({"error": t("nvr-ai nu rulează: antrenamentul are nevoie de GPU-ul lui")}), 503
    b = request.get_json(silent=True) or {}
    d = ai_client.train_start("dataset", maxim=b.get("max"))
    if d is None:
        return jsonify({"error": t("nvr-ai nu răspunde")}), 503
    if d.get("error"):
        return jsonify(d), 409
    audit.log("dataset de antrenament pornit", detail={"max": b.get("max")})
    return jsonify(d)


@bp.route("/api/train/run", methods=["POST"])
def api_train_run():
    """Antreneaza un model nou (data/finetune/runs/curte-vN) in nvr-ai. Body: {epoci?}."""
    if not ai_client.remote():
        return jsonify({"error": t("nvr-ai nu rulează: antrenamentul are nevoie de GPU-ul lui")}), 503
    if not os.path.isfile(os.path.join(FINETUNE_DIR, "dataset.yaml")):
        return jsonify({"error": t("nu există dataset etichetat: construiește-l întâi")}), 400
    b = request.get_json(silent=True) or {}
    try:
        epoci = max(1, min(300, int(b.get("epoci") or 30)))
    except (TypeError, ValueError):
        return jsonify({"error": t("epoci: număr întreg între 1 și 300")}), 400
    d = ai_client.train_start("antrenare", epoci=epoci)
    if d is None:
        return jsonify({"error": t("nvr-ai nu răspunde")}), 503
    if d.get("error"):
        return jsonify(d), 409
    audit.log("antrenament pornit", d.get("nume") or "", detail={"epoci": epoci})
    return jsonify(d)


@bp.route("/api/train/stop", methods=["POST"])
def api_train_stop():
    d = ai_client.train_stop() if ai_client.remote() else None
    if d is None:
        return jsonify({"error": t("nvr-ai nu răspunde")}), 503
    audit.log("job de antrenament oprit")
    return jsonify(d)


_harvest = {"running": False, "made": 0}


@bp.route("/api/train/harvest", methods=["POST"])
def api_train_harvest():
    """Extrage cadre (1/min) din inregistrarile de pe disc in data/dataset, in fundal."""
    if _harvest["running"]:
        return jsonify({"ok": True, "running": True, "made": _harvest["made"]})

    def work():
        try:
            _harvest["made"] = record.harvest_frames(DATASET_DIR)
        finally:
            _harvest["running"] = False

    _harvest["running"] = True
    _harvest["made"] = 0
    import threading
    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True, "running": True})


@bp.route("/api/train/apply", methods=["POST"])
def _swap_formal(sursa):
    """Hot-swap-ul ca TRANZACTIE (faza 6): pre-flight pe candidat in nvr-ai (chiar
    procesul care-l va rula), swap, reincarcare fortata + confirmare — si ROLLBACK
    automat daca reincarcarea pica. Ori aterizeaza complet si verificat, ori nu
    se schimba nimic. Intoarce (raspuns_json, cod)."""
    if ai_client.remote():
        v = ai_client.model_verifica(sursa)
        if v is None:
            return {"error": t("nvr-ai nu raspunde — nu schimb modelul pe nevazute")}, 503
        if v.get("error"):
            return {"error": t("pre-flight picat: {e}", e=v["error"])}, 422
    shutil.copy2(MODEL_PATH, MODEL_PATH + ".bak")
    shutil.copy2(sursa, MODEL_PATH)
    detect.reload_model()                     # fallback-ul local din web, la zi si el
    if not ai_client.remote():
        return {"ok": True, "nota": t("nvr-ai oprit — activat doar local (fallback)")}, 200
    r = ai_client.model_reincarca()
    if r and r.get("ok"):
        audit.log("model activat", os.path.basename(sursa),
                  {"amprenta": r["amprenta"]})
        return {"ok": True, "amprenta": r["amprenta"], "clase": r["clase"]}, 200
    # reincarcarea a picat DUPA swap -> punem inapoi modelul vechi, verificat la fel
    shutil.copy2(MODEL_PATH + ".bak", MODEL_PATH)
    detect.reload_model()
    ai_client.model_reincarca()
    motiv = (r or {}).get("error") or t("nvr-ai nu a confirmat")
    return {"error": t("activarea a picat ({motiv}) — modelul VECHI a fost repus", motiv=motiv)}, 502


def api_train_apply():
    """Aplica cel mai nou model antrenat local (data/finetune/runs) — tranzactie verificata:
    pre-flight in nvr-ai, swap, confirmare cu amprenta, rollback automat la esec."""
    best = _latest_trained()
    if not best:
        return jsonify({"error": t("nu există încă niciun antrenament în data/finetune/runs")}), 404
    rasp, cod = _swap_formal(best)
    if rasp.get("ok"):
        rasp.update(model=os.path.relpath(best, ROOT), backup="data/yolo11n.pt.bak")
    return jsonify(rasp), cod


@bp.route("/api/train/revert", methods=["POST"])
def api_train_revert():
    """Schimba modelul activ cu backup-ul (apasat din nou = revii la cel nou) —
    aceeasi tranzactie verificata ca activarea."""
    bak = MODEL_PATH + ".bak"
    if not os.path.exists(bak):
        return jsonify({"error": t("nu există backup (nu s-a aplicat încă niciun model)")}), 404
    # _swap_formal face el backup-ul curentului in .bak, deci schimbul devine simetric:
    # sursa = backupul de acum, iar dupa swap .bak = modelul de dinainte de revert
    tmp = MODEL_PATH + ".revert-sursa"
    shutil.copy2(bak, tmp)
    try:
        rasp, cod = _swap_formal(tmp)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return jsonify(rasp), cod


# ---- editorul de adnotare (data/finetune) ----
@bp.route("/api/adnotare")
def api_adnotare_lista():
    """?split=train|val &neverificate=1 &cam=N &offset &limit -> cadrele datasetului etichetat."""
    a = request.args
    try:
        return jsonify(adnotare.lista(split=a.get("split") or None,
                                      doar_neverificate=a.get("neverificate") == "1",
                                      cam=a.get("cam", type=int), offset=a.get("offset", 0, type=int),
                                      limit=max(1, min(500, a.get("limit", 60, type=int)))))
    except OSError as e:
        return jsonify({"error": str(e)[:120]}), 500


@bp.route("/api/adnotare/cadru/<split>/<fn>", methods=["GET", "POST"])
def api_adnotare_cadru(split, fn):
    """GET: cadrul cu chenarele lui. POST {boxes:[{cls,cx,cy,w,h}]}: scrie etichetele si
    marcheaza cadrul verificat de om (reconstruirea datasetului nu-l mai suprascrie)."""
    try:
        if request.method == "GET":
            return jsonify(adnotare.cadru(split, fn))
        b = request.get_json(silent=True) or {}
        rez = adnotare.salveaza(split, fn, b.get("boxes") or [], cine=(current_user() or {}).get("user"))
        audit.log("etichete verificate", fn, detail={"n": rez["n"]})
        return jsonify(rez)
    except FileNotFoundError:
        return jsonify({"error": t("cadrul nu există")}), 404
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


@bp.route("/api/adnotare/nesigure/scaneaza", methods=["POST"])
def api_adnotare_nesigure_scaneaza():
    """Body: {n} -> nvr-ai scaneaza cele mai noi n cadre brute cu modelul activ si pune in coada
    pe cele cu chenare de incredere mijlocie (0,30-0,55)."""
    if not ai_client.remote():
        return jsonify({"error": t("nvr-ai nu rulează: scanarea are nevoie de modelul lui")}), 503
    b = request.get_json(silent=True) or {}
    try:
        n = max(10, min(20000, int(b.get("n") or 500)))
    except (TypeError, ValueError):
        return jsonify({"error": t("n: număr întreg")}), 400
    d = ai_client.nesigure_start(n)
    if d is None:
        return jsonify({"error": t("nvr-ai nu răspunde")}), 503
    audit.log("scanare cadre nesigure", detail={"n": n})
    return jsonify(d)


@bp.route("/api/adnotare/nesigure/stare")
def api_adnotare_nesigure_stare():
    d = ai_client.nesigure_stare() if ai_client.remote() else None
    import nesigure
    return jsonify({"scan": d, "in_coada": len(nesigure.incarca())})


@bp.route("/api/adnotare/cadru/nesigure/<fn>/ignora", methods=["POST"])
def api_adnotare_ignora(fn):
    """Cadrul nu e de interes: iese din coada fara sa intre in dataset."""
    import nesigure
    if not nesigure.scoate(fn):
        return jsonify({"error": t("cadrul nu e în coadă")}), 404
    return jsonify({"ok": True})


@bp.route("/api/adnotare/img/<split>/<fn>")
def api_adnotare_img(split, fn):
    try:
        p = adnotare.img_path(split, fn)
    except ValueError:
        p = None
    if not p:
        return jsonify({"error": t("cadrul nu există")}), 404
    return send_file(p, mimetype="image/jpeg", max_age=3600)


# ---- „gaseste-l pe toate camerele” (arhiva re-ID) ----
@bp.route("/api/reid/cauta")
def api_reid_cauta():
    """?poza=/snapshots/<zi>/<fn> (sau <zi>/<fn>) sau ?crop_zi=&crop_fn=; &zile=. GET, ca sa
    fie permis si operatorului/viewerului (VIEWER_GET); raspunde cu aparitiile corpului pe
    camerele vizibile contului, iar poza de interogare trebuie sa fie de pe o camera vizibila."""
    if not ai_client.remote():
        return jsonify({"error": t("nvr-ai nu rulează: căutarea are nevoie de modelul lui")}), 503
    b = request.args
    poza = (b.get("poza") or "")
    for pref in ("/snapshots/", "/api/flux/thumb/"):
        if poza.startswith(pref):
            poza = poza[len(pref):]
    try:
        zile = max(1, min(90, int(b.get("zile") or 7)))
    except (TypeError, ValueError):
        return jsonify({"error": t("zile: număr întreg")}), 400
    body = {"zile": zile, "limit": 80}
    vis = _visible_ids()
    if b.get("crop_zi"):
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", str(b["crop_zi"])) or not re.match(r"^\d{6}_cam\d+_\d+\.jpg$", str(b.get("crop_fn") or "")):
            return jsonify({"error": t("eșantion invalid")}), 400
        body.update(crop_zi=b["crop_zi"], crop_fn=b.get("crop_fn"))
    else:
        m = re.match(r"^\d{4}-\d{2}-\d{2}/\d{6}_cam(\d+)_[A-Za-z0-9_.-]+\.jpg$", poza)
        if not m:
            return jsonify({"error": t("poza invalidă")}), 400
        if vis is not None and int(m[1]) not in vis:
            return jsonify({"error": t("nu ai acces la această camera")}), 403
        body.update(poza=poza)
    if vis is not None:
        body["vizibile"] = sorted(vis)
    d = ai_client.reid_cauta(**body)
    if d is None:
        return jsonify({"error": t("nvr-ai nu răspunde")}), 503
    if d.get("error"):
        return jsonify(d), 400
    nume = {c["id"]: c.get("name") or f"cam {c['id']}" for c in load_ext()}
    for h in d.get("aparitii", []):
        h["cam_nume"] = nume.get(h["cam"], f"cam {h['cam']}")
        h["crop"] = f"/api/reid/crop/{h['zi']}/{h['fn']}"
    return jsonify(d)


@bp.route("/api/reid/crop/<zi>/<fn>")
def api_reid_crop(zi, fn):
    import reid_arhiva
    p = reid_arhiva.cale_crop(zi, fn)
    if not p:
        return jsonify({"error": t("nu există")}), 404
    vis = _visible_ids()
    if vis is not None:
        m = re.match(r"^\d{6}_cam(\d+)_", fn)
        if not m or int(m[1]) not in vis:
            return jsonify({"error": t("nu ai acces la această camera")}), 403
    return send_file(p, mimetype="image/jpeg", max_age=86400)


# ---- recunoastere persoane (Face ID) ----
@bp.route("/api/faces")
def api_faces():
    """Starea Face ID: setari, persoanele din biblioteca, fetele neetichetate."""
    s = settings.load()
    gr = faces.grupuri()
    return jsonify({
        "enabled": s["face_enabled"], "threshold": s["face_threshold"],
        "notify": s["face_notify"], "unknown_save": s["face_unknown_save"],
        "antispoof": s["face_antispoof"], "antispoof_conf": s["face_antispoof_conf"],
        "describe": s["person_describe"],
        "persons": [{"name": p["name"], "photos": len(p["photos"]),
                     "cover": f"/api/faces/img/persons/{p['name']}/{p['photos'][0]}"
                     if p["photos"] else None}
                    for p in faces.persons()],
        "unknown": [{**u, "url": f"/api/faces/img/{u['rel']}"}
                    for u in faces.unknown_list()],
        # necunoscutii ca persoane: grupurile permanente (regrupate in nvr-ai la fiecare crop)
        "grupuri": [{"id": gid, **{k: v for k, v in g.items() if k != "centroid"},
                     "mostre": [{"rel": r, "url": f"/api/faces/img/{r}"} for r in g.get("mostre", [])]}
                    for gid, g in sorted(gr.get("grupuri", {}).items(),
                                         key=lambda kv: -kv[1].get("n", 0))],
        "perechi": gr.get("perechi", []),
        "grupuri_t": gr.get("t"),
    })


@bp.route("/api/faces/regrupeaza", methods=["POST"])
def api_faces_regrupeaza():
    """Regrupeaza acum fetele necunoscute (altfel se face singur la 2 min dupa un crop nou)."""
    try:
        if ai_client.remote():
            d = ai_client.face_regrupeaza()
            if d is None:
                return jsonify({"error": t("nvr-ai nu răspunde")}), 503
        else:
            d = faces.regrupeaza()
    except Exception as e:
        return jsonify({"error": t("regruparea a eșuat: {e}", e=str(e)[:120])}), 500
    return jsonify({"ok": True, "grupuri": len(d.get("grupuri", {})), "perechi": len(d.get("perechi", []))})


@bp.route("/api/faces/person/<name>/uneste", methods=["POST"])
def api_faces_uneste(name):
    """Body: {in} — persoana <name> intra in persoana `in` (pozele se muta, `name` dispare)."""
    tinta = (request.get_json(silent=True) or {}).get("in") or ""
    try:
        if ai_client.remote():
            d = ai_client.face_uneste(tinta, name)
            if d is None:   # nvr-ai e pornit dar a picat la mijloc: NU repetam local, pe jumatate
                return jsonify({"error": t("nvr-ai nu a răspuns; biblioteca nu a fost atinsă de web")}), 503
        else:
            d = {"ok": True, **faces.uneste(tinta, name)}
        if d.get("error"):
            return jsonify(d), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("persoane unite în face-id", f"{name} -> {tinta}", detail={"poze": d.get("poze_mutate")})
    return jsonify(d)


@bp.route("/api/faces/config", methods=["POST"])
def api_faces_config():
    b = request.get_json(silent=True) or {}
    upd = {}
    if "enabled" in b:
        upd["face_enabled"] = bool(b["enabled"])
    if "threshold" in b:
        upd["face_threshold"] = max(0.25, min(0.8, float(b["threshold"])))
    if "notify" in b:
        upd["face_notify"] = bool(b["notify"])
    if "unknown_save" in b:
        upd["face_unknown_save"] = bool(b["unknown_save"])
    if "antispoof" in b:
        upd["face_antispoof"] = bool(b["antispoof"])
    if "antispoof_conf" in b:
        upd["face_antispoof_conf"] = max(0.5, min(0.95, float(b["antispoof_conf"])))
    if "describe" in b:
        upd["person_describe"] = bool(b["describe"])
    settings.update(upd)
    # preincarcarea: in nvr-ai cand serviciul e viu (modelele stau acolo), altfel local
    if ai_client.remote():
        ai_client.warmup(face=bool(upd.get("face_enabled")),
                         describe=bool(upd.get("person_describe")))
    else:
        if upd.get("person_describe"):
            describe.warmup_async()
        if upd.get("face_enabled"):
            faces.warmup_async()
    audit.log("setări face-id", detail=upd)
    return jsonify({"ok": True})


@bp.route("/api/faces/enroll", methods=["POST"])
def api_faces_enroll():
    """Body: {name, rel} (un crop din unknown/ sau o poza existenta din biblioteca)
    sau {name, data} (imagine base64, pentru upload). Crop-ul din unknown dispare
    dupa inrolare — etichetarea E inrolarea."""
    b = request.get_json(silent=True) or {}
    name = b.get("name") or ""
    try:
        if b.get("rel"):
            p = faces.safe_path(b["rel"])
            if not p or not os.path.isfile(p):
                return jsonify({"error": t("imaginea nu există")}), 404
            with open(p, "rb") as f:
                data = f.read()
        elif b.get("data"):
            import base64
            data = base64.b64decode(b["data"].split(",")[-1], validate=False)
        else:
            return jsonify({"error": t("trimite rel sau data")}), 400
        # inrolarea cere modelul de embeddings -> nvr-ai cand e viu, altfel local
        rez = ai_client.face_enroll(name, data) if ai_client.remote() else None
        if rez is None:
            rez = faces.enroll(name, data)
        elif rez.get("error"):
            return jsonify(rez), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if b.get("rel", "").startswith("unknown/"):
        faces.delete_unknown(b["rel"])
    audit.log("persoana inrolata", rez["name"])
    return jsonify({"ok": True, **rez})


@bp.route("/api/faces/person/<name>", methods=["DELETE"])
def api_faces_person_del(name):
    """?photo=<fn> sterge doar o poza; fara parametru sterge persoana intreaga."""
    try:
        # stergerea unei poze reconstruieste embedding-ul persoanei (model) -> nvr-ai
        d = ai_client.face_delete(name, request.args.get("photo")) \
            if ai_client.remote() else None
        if d is None:
            faces.delete_person(name, request.args.get("photo"))
        elif d.get("error"):
            return jsonify(d), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("persoana/poza stearsa din face-id", name)
    return jsonify({"ok": True})


@bp.route("/api/faces/person/<name>/rename", methods=["POST"])
def api_faces_person_rename(name):
    """Body: {nou} — redenumeste persoana (director + embeddings). Fara model,
    dar biblioteca are un singur scriitor (nvr-ai) cand serviciul e viu."""
    nou = (request.get_json(silent=True) or {}).get("nou") or ""
    try:
        d = ai_client.face_rename(name, nou) if ai_client.remote() else None
        if d is None:
            d = {"nume": faces.rename_person(name, nou)}
        elif d.get("error"):
            return jsonify(d), 400
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    audit.log("persoana redenumita în face-id", f"{name} -> {d['nume']}")
    return jsonify({"ok": True, "nume": d["nume"]})


@bp.route("/api/faces/auto-residents", methods=["POST"])
def api_faces_auto_residents():
    """Grupeaza fetele necunoscute capturate si inroleaza automat rezidentii frecventi
    (familie/personal) ca 'Rezident N' — ca sa nu mai fie tratati ca intrusi, fara
    inrolare manuala. Utilizatorul ii poate redenumi/sterge dupa."""
    try:
        d = ai_client.face_auto_enroll() if ai_client.remote() else None
        rez = d.get("rezidenti", []) if d is not None else faces.auto_enroll_residents()
    except Exception as e:
        return jsonify({"error": t("eșuat: {e}", e=str(e)[:120])}), 500
    audit.log("auto-inrolare rezidenți", detail={"nr": len(rez)})
    return jsonify({"ok": True, "rezidenti": rez})


@bp.route("/api/faces/groups")
def api_faces_groups():
    """Fetele necunoscute grupate pe persoana (fara inrolare): [{id, n, zile, interval,
    poze, mostre (cu url), sugestie {nume, sim}|null}]. Calcul in nvr-ai (cache pe crop)."""
    try:
        d = ai_client.face_groups() if ai_client.remote() else None
        grupuri = d.get("grupuri", []) if d is not None else faces.unknown_groups()
    except Exception as e:
        return jsonify({"error": t("gruparea a eșuat: {e}", e=str(e)[:120])}), 500
    for g in grupuri:
        g["mostre"] = [{"rel": r, "url": f"/api/faces/img/{r}"} for r in g.get("mostre", [])]
    return jsonify({"grupuri": grupuri,
                    "persoane": [p["name"] for p in faces.persons()]})


@bp.route("/api/faces/groups/assign", methods=["POST"])
def api_faces_groups_assign():
    """Body: {poze: [rel...], actiune: "persoana"|"ignora", nume}. 'persoana' inroleaza
    pozele (cel mult MAX_INROLATE, distribuite in timp) la numele dat — existent sau nou —
    si scoate TOATE pozele grupului din necunoscute; 'ignora' doar le sterge."""
    MAX_INROLATE = 8
    b = request.get_json(silent=True) or {}
    poze = [r for r in (b.get("poze") or []) if isinstance(r, str) and r.startswith("unknown/")]
    actiune = b.get("actiune") or ""
    nume = (b.get("nume") or "").strip()
    if not poze:
        return jsonify({"error": t("niciun crop în grup")}), 400
    if actiune not in ("persoana", "ignora"):
        return jsonify({"error": t("acțiune necunoscută")}), 400
    inrolate = 0
    if actiune == "persoana":
        if not nume:
            return jsonify({"error": t("scrie numele persoanei")}), 400
        pas = max(1, len(poze) // MAX_INROLATE)
        for rel in poze[::pas][:MAX_INROLATE]:
            p = faces.safe_path(rel)
            if not p or not os.path.isfile(p):
                continue
            with open(p, "rb") as f:
                data = f.read()
            try:
                rez = ai_client.face_enroll(nume, data) if ai_client.remote() else None
                if rez is None:
                    rez = faces.enroll(nume, data)
                if rez.get("error"):
                    continue
                nume = rez.get("name") or nume
                inrolate += 1
            except ValueError as e:
                return jsonify({"error": str(e)}), 400
        if not inrolate:
            return jsonify({"error": t("nicio poză din grup nu a putut fi înrolată")}), 400
    for rel in poze:
        faces.delete_unknown(rel)
    audit.log("grup de fețe " + ("înrolat" if actiune == "persoana" else "ignorat"),
              nume, detail={"poze": len(poze), "inrolate": inrolate})
    return jsonify({"ok": True, "nume": nume, "inrolate": inrolate, "sterse": len(poze)})


@bp.route("/api/faces/unknown/clear", methods=["POST"])
def api_faces_unknown_clear():
    """Body: {rel} sterge un crop; {} le sterge pe toate."""
    faces.delete_unknown((request.get_json(silent=True) or {}).get("rel"))
    return jsonify({"ok": True})


@bp.route("/api/faces/img/<path:rel>")
def api_faces_img(rel):
    """Pozele bibliotecii — sub /api/, deci vizibile doar administratorului."""
    p = faces.safe_path(rel)
    if not p or not os.path.isfile(p):
        return jsonify({"error": t("nu există")}), 404
    return send_file(p, mimetype="image/jpeg", max_age=60)
