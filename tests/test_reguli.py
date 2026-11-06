"""Logica pură a regulilor de analiză (app/reguli.py): scorul geometric de cădere din
keypoints COCO-17, mașina de stări dwell și cooldown-ul. Fără model — totul e pur."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import reguli as R  # noqa: E402


def _kps(puncte, conf=0.9):
    """17 keypoints (x, y, conf); `puncte` = {index: (x, y)}, restul marcate nesigure."""
    out = [(0.0, 0.0, 0.0)] * 17
    for i, (x, y) in puncte.items():
        out[i] = (float(x), float(y), conf)
    return out


# ---- scor_cadere ----
def test_scor_in_picioare_mic():
    # umeri sus, șolduri sub ei (trunchi vertical), cap deasupra umerilor; bbox înalt
    kps = _kps({R.UMAR_S: (100, 100), R.UMAR_D: (140, 100),
                R.SOLD_S: (105, 200), R.SOLD_D: (135, 200),
                R.NAS: (120, 70)})
    assert R.scor_cadere(kps, (90, 50, 150, 230)) < 0.3


def test_scor_culcat_mare():
    # umeri și șolduri la aceeași înălțime, depărtați pe orizontală (trunchi orizontal);
    # capul la nivelul șoldurilor; bbox lat
    kps = _kps({R.UMAR_S: (100, 150), R.UMAR_D: (100, 160),
                R.SOLD_S: (260, 150), R.SOLD_D: (260, 160),
                R.NAS: (70, 155)})
    assert R.scor_cadere(kps, (60, 120, 300, 185)) > 0.75


def test_scor_doar_bbox_cand_lipsesc_keypoints():
    # fără keypoints valide → doar semnalul din raportul bbox
    kps = _kps({}, conf=0.0)
    lat = R.scor_cadere(kps, (0, 0, 200, 80))      # w/h = 2.5 -> saturat
    inalt = R.scor_cadere(kps, (0, 0, 80, 200))    # w/h = 0.4 -> 0
    assert lat > 0.9 and inalt == 0.0


def test_conf_min_ignora_keypoints_nesigure():
    # aceleași puncte „culcat", dar sub pragul de încredere → ignorate, rămâne doar bbox
    kps = _kps({R.UMAR_S: (100, 150), R.SOLD_S: (260, 150)}, conf=0.3)
    s = R.scor_cadere(kps, (0, 0, 100, 200), conf_min=0.5)   # bbox înalt -> ~0
    assert s == 0.0


# ---- Cadere (dwell) ----
def test_dwell_declanseaza_o_data_dupa_prag():
    c = R.Cadere(dwell_s=6.0, scor_prag=0.6)
    assert c.update(5, "t1", 0.9, now=0.0) is False     # abia a căzut jos
    assert c.update(5, "t1", 0.9, now=3.0) is False     # încă în dwell
    assert c.update(5, "t1", 0.9, now=6.0) is True      # dwell împlinit -> candidat
    assert c.update(5, "t1", 0.9, now=9.0) is False     # nu reemite în același episod


def test_dwell_se_anuleaza_la_ridicare():
    c = R.Cadere(dwell_s=6.0, scor_prag=0.6)
    c.update(5, "t1", 0.9, now=0.0)
    assert c.update(5, "t1", 0.2, now=3.0) is False     # s-a ridicat -> reset
    assert c.update(5, "t1", 0.9, now=4.0) is False     # repornește cronometrul
    assert c.update(5, "t1", 0.9, now=9.9) is False     # 5.9 s < dwell
    assert c.update(5, "t1", 0.9, now=10.0) is True     # 6 s de la repornire


def test_dwell_track_uri_independente():
    c = R.Cadere(dwell_s=6.0, scor_prag=0.6)
    c.update(5, "a", 0.9, now=0.0)
    c.update(5, "b", 0.9, now=2.0)
    assert c.update(5, "a", 0.9, now=6.0) is True
    assert c.update(5, "b", 0.9, now=6.0) is False      # b are propriul cronometru
    assert c.update(5, "b", 0.9, now=8.0) is True


def test_expire_uita_track_vechi():
    c = R.Cadere(dwell_s=6.0, scor_prag=0.6)
    c.update(5, "t1", 0.9, now=0.0)
    c.expire(now=100.0, max_age=30.0)
    # după expirare, update repornește episodul (t0 = acum), nu continuă vechiul
    assert c.update(5, "t1", 0.9, now=100.0) is False
    assert c.update(5, "t1", 0.9, now=106.0) is True


# ---- Cooldown ----
def test_cooldown():
    cd = R.Cooldown(120)
    assert cd.gata(5, now=0.0) is True
    assert cd.gata(5, now=60.0) is False
    assert cd.gata(6, now=60.0) is True        # altă cheie, independent
    assert cd.gata(5, now=120.0) is True       # au trecut 120 s
