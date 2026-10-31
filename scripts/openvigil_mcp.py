#!/usr/bin/env python3
"""Punte stdio -> HTTP pentru serverul MCP al OpenVigil, pentru clientii care pornesc
serverele MCP ca procese locale (Claude Desktop, Claude Code, Cursor...).

Citeste mesaje JSON-RPC, unul pe linie, de la stdin, le trimite la `POST <url>/mcp` cu
jetonul API si scrie raspunsul pe stdout, tot unul pe linie. Fara dependente (urllib).

  OPENVIGIL_URL=https://nvr.exemplu.ro OPENVIGIL_TOKEN=... python3 scripts/openvigil_mcp.py

Configurare in Claude Desktop (claude_desktop_config.json):
  {"mcpServers": {"openvigil": {"command": "python3", "args": ["/calea/openvigil_mcp.py"],
                                "env": {"OPENVIGIL_URL": "https://nvr.exemplu.ro",
                                        "OPENVIGIL_TOKEN": "jetonul din Setari -> Automatizari"}}}}
"""
import json
import os
import sys
import urllib.error
import urllib.request


def main():
    url = os.environ.get("OPENVIGIL_URL", "http://127.0.0.1:8079").rstrip("/") + "/mcp"
    token = os.environ.get("OPENVIGIL_TOKEN", "")
    if not token:
        sys.stderr.write("OPENVIGIL_TOKEN lipseste (Setari -> Automatizari -> Jeton pentru comenzi)\n")
        sys.exit(2)
    for linie in sys.stdin:
        linie = linie.strip()
        if not linie:
            continue
        try:
            mesaj = json.loads(linie)
        except ValueError:
            continue
        cerere = urllib.request.Request(
            url, data=json.dumps(mesaj).encode(), method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "X-Token": token})
        try:
            with urllib.request.urlopen(cerere, timeout=120) as r:
                corp = r.read()
        except urllib.error.HTTPError as e:
            corp = e.read()
            if not corp:
                corp = json.dumps({"jsonrpc": "2.0", "id": mesaj.get("id"),
                                   "error": {"code": -32000, "message": f"HTTP {e.code}"}}).encode()
        except Exception as e:
            corp = json.dumps({"jsonrpc": "2.0", "id": mesaj.get("id"),
                               "error": {"code": -32000, "message": str(e)[:120]}}).encode()
        if corp and corp.strip():
            sys.stdout.write(corp.decode().strip() + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
