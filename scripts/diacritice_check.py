#!/usr/bin/env python3
"""Textele de interfata in romana fara diacritice („sterge", „Notificari", „dupa").

Cheia de traducere e textul romanesc (vezi docs/i18n.md), deci o cheie fara diacritice
ajunge asa pe ecran. Lista cuprinde doar forme care sunt MEREU gresite fara diacritice
(„zona", „regula", „persoana" sunt si forme articulate corecte, deci nu apar aici).
Iesire 1 daca gaseste ceva; `--lista` arata toate aparitiile."""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRESITE = """si sa in fara dupa inca cand pana daca iesi sterge stergi cauta apasa descarca
masina masini masinii masinile numar numarul miscare miscari miscarea detectie detectii detectia
inregistrare inregistrari inregistrarea notificari notificarile setari setarile salveaza inchide
marti sambata duminica zabovit fa cat cate masurat masuratori masuratorile varful colecteaza ramane
dureaza corecteaza invata invatare porneste opreste functioneaza lipseste gasit gasita incarca incarcare
tarziu astazi maine saptamana saptamani intai poti selecteaza adauga adaugata asteapta incearca
retentie retentia sanatate sanatatea urmatoare urmatorul
cateva niste ti iti vazut sterse sters""".split()
RX = re.compile(r"(?<![\wĀ-ɏ])(" + "|".join(sorted(set(GRESITE), key=len, reverse=True)) + r")(?![\wĀ-ɏ])", re.I)


def chei():
    with open(os.path.join(ROOT, "app", "static", "i18n", "en.json"), encoding="utf-8") as f:
        return list(json.load(f))


def main():
    fara_loc = lambda k: re.sub(r"\{[^}]*\}", " ", k)   # {cand}, {pana} sunt nume de parametri, nu text
    rau = [(k, sorted({m.lower() for m in RX.findall(fara_loc(k))})) for k in chei() if RX.search(fara_loc(k))]
    if "--lista" in sys.argv:
        for k, w in rau:
            print(f"{', '.join(w):24s} | {k}")
    print(f"diacritice: {len(rau)} texte de interfata cu forme fara diacritice")
    return 1 if rau else 0


if __name__ == "__main__":
    sys.exit(main())
