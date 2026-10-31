"""Serverul MCP (app/mcp_server.py): protocolul JSON-RPC (initialize, ping, tools/list,
tools/call, notificari, erori) cu uneltele inlocuite; modelul de limbaj (app/llm.py) cu
spatele extern compatibil OpenAI; prompt-ul descrierii de episod; puntea stdio."""
import json
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))
import episoade  # noqa: E402
import llm  # noqa: E402
import mcp_server as M  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.fixture
def unelte(monkeypatch):
    monkeypatch.setitem(M.UNELTE, "camere", (lambda a: [{"id": 1, "nume": "Poarta"}], "d", {"type": "object", "properties": {}}))
    monkeypatch.setitem(M.UNELTE, "poza", (lambda a: {"_imagine": "AAAA", "mime": "image/jpeg"}, "d", {"type": "object", "properties": {}}))
    monkeypatch.setitem(M.UNELTE, "mod", (lambda a: (_ for _ in ()).throw(ValueError("mod necunoscut")), "d", {"type": "object", "properties": {}}))


def test_initialize_ping_lista():
    r = M.trateaza({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}}})
    assert r["result"]["protocolVersion"] == "2025-03-26" and r["result"]["serverInfo"]["name"] == "openvigil"
    assert M.trateaza({"jsonrpc": "2.0", "id": 2, "method": "ping"})["result"] == {}
    assert M.trateaza({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    lst = M.trateaza({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})["result"]["tools"]
    nume = {x["name"] for x in lst}
    assert {"camere", "stare", "evenimente", "episoade", "cauta", "intreaba", "poza", "recomandari", "mod", "scena"} <= nume
    assert all("inputSchema" in x and "description" in x for x in lst)
    assert M.trateaza({"jsonrpc": "2.0", "id": 4, "method": "nu/exista"})["error"]["code"] == -32601
    assert M.trateaza({"id": 5, "method": "ping"})["error"]["code"] == -32600          # fara jsonrpc


def test_tools_call(unelte):
    r = M.trateaza({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "camere", "arguments": {}}})
    assert json.loads(r["result"]["content"][0]["text"])[0]["nume"] == "Poarta"
    assert r["result"]["structuredContent"] == {"rezultat": [{"id": 1, "nume": "Poarta"}]}
    r = M.trateaza({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "poza", "arguments": {"camera": 1}}})
    assert r["result"]["content"][0] == {"type": "image", "data": "AAAA", "mimeType": "image/jpeg"}
    r = M.trateaza({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "mod", "arguments": {"mod": "x"}}})
    assert r["result"]["isError"] and "mod necunoscut" in r["result"]["content"][0]["text"]
    r = M.trateaza({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "nu_exista"}})
    assert r["result"]["isError"]


def test_corp_lista_si_notificari(unelte):
    r, cod = M.trateaza_corp([{"jsonrpc": "2.0", "method": "notifications/initialized"},
                              {"jsonrpc": "2.0", "id": 9, "method": "ping"}])
    assert cod == 200 and r == [{"jsonrpc": "2.0", "id": 9, "result": {}}]
    assert M.trateaza_corp({"jsonrpc": "2.0", "method": "notifications/cancelled"}) == (None, 202)


def test_llm_extern(monkeypatch):
    cfg = {"llm_url": "http://ollama:11434/v1/", "llm_model": "qwen2.5:7b", "llm_key": "k"}
    monkeypatch.setattr(llm.settings, "get", lambda k: cfg.get(k))
    apel = {}

    class R:
        ok, status_code, text = True, 200, ""

        def json(self):
            return {"choices": [{"message": {"content": "  Salut,\n  lume. "}}]}

    import requests
    monkeypatch.setattr(requests, "post", lambda url, **k: apel.update(url=url, **k) or R())
    assert llm.completeaza("x", max_tokens=9) == "Salut, lume."
    assert apel["url"] == "http://ollama:11434/v1/chat/completions"
    assert apel["headers"]["Authorization"] == "Bearer k" and apel["json"]["model"] == "qwen2.5:7b"
    assert apel["json"]["max_tokens"] == 9
    cfg["llm_model"] = ""
    with pytest.raises(RuntimeError):
        llm.completeaza("x")


def test_llm_local_prin_nvr_ai(monkeypatch):
    monkeypatch.setattr(llm.settings, "get", lambda k: "")
    import ai_client
    monkeypatch.setattr(ai_client, "text", lambda p, m, timeout=60: {"ok": True, "text": "da"})
    assert llm.completeaza("x") == "da"
    monkeypatch.setattr(ai_client, "text", lambda p, m, timeout=60: {"error": "VRAM"})
    with pytest.raises(RuntimeError, match="VRAM"):
        llm.completeaza("x")


def test_prompt_descriere_episod():
    import i18n
    i18n.incarca()
    date = {"inceput": "16:47:40", "durata_sec": 190, "persoane_max": 1, "aparitii": 1,
            "nume": ["Stefan"], "zone": ["panouri"], "directii": ["spre casă"], "comportament": "a zăbovit",
            "text": "Stefan · 3 min · zone: panouri"}
    p = episoade.prompt_descriere("Panouri", date, "ro", ["găină"])
    assert "română" in p and "recunoscuti: Stefan" in p and "vazute si: găină" in p and "90" in p
    pe = episoade.prompt_descriere("Panouri", date, "en", ["găină"])
    assert "English" in pe and "recognised: Stefan" in pe and "also seen: chicken" in pe
    assert episoade.scurteaza("scurt") == "scurt"
    lung = episoade.scurteaza("cuvant " * 30)
    assert len(lung) <= 118 and lung.endswith("…") and " cuvant…" in lung


def test_puntea_stdio(tmp_path):
    """Puntea trimite fiecare linie la /mcp cu jetonul si scrie raspunsul pe o linie."""
    import http.server
    import threading
    vazute = []

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            corp = json.loads(self.rfile.read(n))
            vazute.append((self.path, self.headers.get("X-Token"), corp))
            out = json.dumps({"jsonrpc": "2.0", "id": corp.get("id"), "result": {"ok": True}}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        p = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "openvigil_mcp.py")],
                           input='{"jsonrpc":"2.0","id":7,"method":"ping"}\n\n', capture_output=True, text=True,
                           env={**os.environ, "OPENVIGIL_URL": f"http://127.0.0.1:{srv.server_port}", "OPENVIGIL_TOKEN": "tok"},
                           timeout=30)
    finally:
        srv.shutdown()
    assert p.returncode == 0 and json.loads(p.stdout.strip())["result"] == {"ok": True}
    assert vazute == [("/mcp", "tok", {"jsonrpc": "2.0", "id": 7, "method": "ping"})]
