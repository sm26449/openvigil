#!/usr/bin/env python3
"""Blueprint: analitica retroactiva — cautare pe regiune, harta de caldura, time-lapse.

Toate sunt GET cu `channel` (guard-ul din server aplica restrictia pe camere a contului).
Time-lapse-ul e lung (minute): 202 + job, ca exportul.
"""
from flask import Blueprint, jsonify, request

import analiza
import jobs
import masca
from server import HW, _cam_entry, _run_ffmpeg, current_user

bp = Blueprint("analiza", __name__)
DAY = r"^\d{4}-\d{2}-\d{2}$"


def _zi(s):
    """'YYYY-MM-DD' valid calendaristic, altfel None (2026-13-99 trece de regex, nu si de aici)."""
    import re
    import time
    s = (s or "").strip()
    if not re.match(DAY, s):
        return None
    try:
        time.strptime(s, "%Y-%m-%d")
    except ValueError:
        return None
    return s


@bp.route("/api/regiune")
def api_regiune():
    """?channel=&day=&x=&y=&w=&h= (fractii) -> evenimentele zilei cu chenarul in regiune."""
    try:
        cam = int(request.args["channel"])
        rect = [float(request.args.get(k, "0")) for k in ("x", "y", "w", "h")]
        day = _zi(request.args.get("day"))
        if not day:
            raise ValueError("zi invalida")
        ev = analiza.regiune(cam, day, rect)
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e) or "parametri invalizi"}), 400
    return jsonify({"evenimente": ev, "zi": day, "camera": cam})


@bp.route("/api/heatmap")
def api_heatmap():
    """?channel=&day=&zile=1|7|30 -> grila de intensitate a traseelor de oameni."""
    try:
        cam = int(request.args["channel"])
        zile = int(request.args.get("zile") or 1)
        day = _zi(request.args.get("day"))
        if request.args.get("day") and not day:
            raise ValueError
    except (KeyError, ValueError):
        return jsonify({"error": "parametri invalizi"}), 400
    return jsonify(analiza.heatmap(cam, day, zile))


@bp.route("/api/timelapse")
def api_timelapse():
    """?channel=&day=&pas=60 -> 202 + job care face MP4-ul (un cadru la `pas` secunde)."""
    try:
        cam = int(request.args["channel"])
        day = _zi(request.args.get("day"))
        pas = max(10, min(600, int(request.args.get("pas") or 60)))
        if not day:
            raise ValueError
    except (KeyError, ValueError):
        return jsonify({"error": "parametri invalizi"}), 400
    if not _cam_entry(cam):
        return jsonify({"error": "camera nu există"}), 404
    who = (current_user() or {}).get("user")
    return jsonify(jobs.submit("timelapse", {"channel": cam, "day": day, "pas": pas},
                               who=who, cam=cam)), 202


def _job_timelapse(job):
    cam = _cam_entry(int(job.parametri["channel"]))
    try:
        return analiza.job_timelapse(job, _run_ffmpeg, HW, masca.vf_cam(cam))
    except RuntimeError as e:
        raise jobs.JobError(str(e))


jobs.register("timelapse", _job_timelapse)
