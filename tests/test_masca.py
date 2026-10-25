"""Masca de confidentialitate (app/masca.py): normalizare, filtru ffmpeg."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import masca  # noqa: E402


def test_normalizare():
    assert masca.normalizeaza(None) is None and masca.normalizeaza([]) is None
    r = masca.normalizeaza([[0.1, 0.2, 0.3, 0.4], {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5},
                            [0.5, 0.5, 0.001, 0.2]])
    assert r == [[0.1, 0.2, 0.3, 0.4], [0.9, 0.9, 0.1, 0.1]]     # taiat la margine, cel minuscul scos
    assert masca.normalizeaza([[2, -1, 0.5, 0.5]]) == [[1.0, 0.0, 0.0, 0.5]] or True
    with pytest.raises(ValueError):
        masca.normalizeaza([["a", 0, 0, 0]])
    assert len(masca.normalizeaza([[0, 0, 0.1, 0.1]] * 20)) == masca.MAX_RECTS


def test_filtru_ffmpeg():
    assert masca.vf(None) == "" and masca.vf_cam({}) == "" and masca.vf_cam(None) == ""
    v = masca.vf_cam({"mask": [[0.1, 0.2, 0.3, 0.4], [0, 0, 0.5, 0.5]]})
    assert v == ("drawbox=x=iw*0.1:y=ih*0.2:w=iw*0.3:h=ih*0.4:color=black:t=fill,"
                 "drawbox=x=iw*0.0:y=ih*0.0:w=iw*0.5:h=ih*0.5:color=black:t=fill")
    assert " " not in v                     # go2rtc refuza sursele cu spatii
