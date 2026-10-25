#!/bin/sh
# Punctul de intrare al imaginii OpenVigil: acelasi container, patru roluri.
#   openvigil web      interfata + API (nvr-web)
#   openvigil record   inregistrarea continua (nvr-record)
#   openvigil ai       detectie, fete, re-ID, descrieri (nvr-ai)
#   openvigil go2rtc   motorul de streaming (config generat din camerele din data/)
#   openvigil test     testele pytest
#   openvigil <cmd>    orice alta comanda (ex. bash)
set -e
cd /opt/openvigil
mkdir -p data

case "${1:-web}" in
  web)    exec venv/bin/python app/server_main.py ;;
  record) exec venv/bin/python app/record_main.py ;;
  ai)
    venv/bin/python scripts/modele.py
    exec venv/bin/python app/ai_main.py ;;
  go2rtc)
    venv/bin/python gen_go2rtc.py
    exec /usr/local/bin/go2rtc -config data/go2rtc.yaml ;;
  test)
    shift
    exec venv/bin/python -m pytest -q tests "$@" ;;
  *) exec "$@" ;;
esac
