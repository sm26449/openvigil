"""Modulele ES ale interfetei: importurile se potrivesc cu exporturile, nucleul nu depinde
de ecrane, sintaxa trece (scripts/module_check.py)."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import module_check  # noqa: E402


def test_importuri_si_exporturi():
    assert module_check.analiza() == []


def test_sintaxa_modulelor():
    assert module_check.node_check() == []
