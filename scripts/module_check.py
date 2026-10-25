#!/usr/bin/env python3
"""Verifica modulele ES ale interfetei (app/static/js/*.js + app/static/app.js):
fiecare `import { x } from './f.js'` gaseste `x` printre exporturile lui f, fiecare export
e declarat in fisier, nucleul (00-core) nu importa din ecrane, si `node --check` trece.
    venv/bin/python scripts/module_check.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JS = os.path.join(ROOT, "app", "static", "js")
ENTRY = os.path.join(ROOT, "app", "static", "app.js")
IMPORT = re.compile(r"^import\s+\{([^}]*)\}\s+from\s+'(?:\./(?:js/)?|(?=\.\./))([^']+)';", re.M)
EXPORT = re.compile(r"^export\s+\{([^}]*)\};", re.M)
DECL = re.compile(r"^(?:async\s+function|function|class|const|let)\s+([A-Za-z_$][\w$]*)", re.M)


def analiza():
    """Intoarce lista problemelor (goala = totul in regula)."""
    probleme = []
    mod = {fn: open(os.path.join(JS, fn)).read() for fn in sorted(os.listdir(JS)) if fn.endswith(".js")}
    exp = {}
    for fn, s in mod.items():
        names = set()
        for m in EXPORT.finditer(s):
            names |= {n.strip() for n in m.group(1).split(",") if n.strip()}
        if fn == "state.js":
            names |= set(re.findall(r"^export\s+const\s+([A-Za-z_$][\w$]*)", s, re.M))
        decl = set(DECL.findall(s))
        for m in re.finditer(r"^(?:const|let)\s+([^;\n]*);?$", s, re.M):      # `let a = 1, b = 2;` pe o linie
            decl |= set(re.findall(r"(?:^|,)\s*([A-Za-z_$][\w$]*)\s*(?==|,|$)", m.group(1)))
        for m in re.finditer(r"^import\s+\{([^}]*)\}", s, re.M):
            decl |= {n.strip().split(" as ")[-1] for n in m.group(1).split(",") if n.strip()}
        for n in names:
            if n not in decl and fn != "state.js":
                probleme.append(f"{fn}: exporta {n}, care nu e declarat acolo")
        exp[fn] = names
    surse = dict(mod)
    surse["app.js"] = open(ENTRY).read()
    for fn, s in surse.items():
        for m in IMPORT.finditer(s):
            tinta = m.group(2)
            if tinta.startswith("../"):                       # module din app/static/ (video-rtc, icons)
                if not os.path.exists(os.path.join(JS, tinta)):
                    probleme.append(f"{fn}: importa din {tinta}, care nu exista")
                continue
            if tinta not in exp:
                probleme.append(f"{fn}: importa din {tinta}, care nu exista")
                continue
            for n in (x.strip() for x in m.group(1).split(",") if x.strip()):
                if n not in exp[tinta]:
                    probleme.append(f"{fn}: importa {n} din {tinta}, care nu-l exporta")
            if fn == "00-core.js" and tinta != "state.js":
                probleme.append(f"nucleul importa din {tinta}: ar intra in ciclu si $/html ar fi in TDZ la evaluarea ecranelor")
    return probleme


def node_check():
    """`node --check` pe fiecare modul (copie .mjs, ca node sa-l trateze ca modul)."""
    node = shutil.which("node")
    if not node:
        return []
    out = []
    with tempfile.TemporaryDirectory() as d:
        for fn in [ENTRY] + [os.path.join(JS, f) for f in os.listdir(JS) if f.endswith(".js")]:
            tmp = os.path.join(d, os.path.basename(fn)[:-3] + ".mjs")
            shutil.copy(fn, tmp)
            r = subprocess.run([node, "--check", tmp], capture_output=True, text=True)
            if r.returncode:
                out.append(f"{os.path.basename(fn)}: {r.stderr.strip().splitlines()[-1][:120]}")
    return out


if __name__ == "__main__":
    p = analiza() + node_check()
    for x in p:
        print(" !", x)
    print("ok" if not p else f"{len(p)} probleme")
    sys.exit(1 if p else 0)
