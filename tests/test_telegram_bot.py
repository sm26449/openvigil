"""Comenzile Telegram (app/telegram_bot.py): /acasa etc. comuta modul, /stare raspunde,
altceva e ignorat."""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import mod  # noqa: E402
import telegram_bot  # noqa: E402


@pytest.fixture(autouse=True)
def _izolare(tmp_path, monkeypatch):
    monkeypatch.setattr(mod, "FILE", str(tmp_path / "mod.json"))
    monkeypatch.setattr(mod, "_anunta", lambda st: None)
    monkeypatch.setattr(telegram_bot, "_stare_text", lambda: "STARE")


def test_comenzi():
    assert telegram_bot.raspuns("/plecat", cine="telegram:Stefan").startswith("🔒 Mod: Plecat")
    assert mod.get()["mod"] == "plecat" and mod.get()["sursa"] == "telegram"
    assert "Deja" in telegram_bot.raspuns("/plecat")
    assert telegram_bot.raspuns("/acasa@nvr_bot extra").startswith("🏠 Mod: Acasa")
    assert telegram_bot.raspuns("/stare").startswith("STARE")
    assert telegram_bot.raspuns("salut") is None
    assert telegram_bot.raspuns("") is None
