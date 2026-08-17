#!/usr/bin/env bash
# Lance l'addon YouTube Music Eclipse en local.
#
# L'addon écoute sur 0.0.0.0:8000 (ou $PORT) pour être accessible depuis
# ton téléphone sur le même réseau WiFi.
#
# Dans Eclipse (téléphone) :
#   Settings -> Connections -> Add Connection -> Addon
#   colle : http://<IP-DU-MAC>:8000/manifest.json
#   (trouve l'IP avec la commande ci-dessous)
set -euo pipefail
cd "$(dirname "$0")"

# IP locale du Mac pour aider à l'ajout dans Eclipse
IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "?")
echo "Adresse de ton Mac (à utiliser dans Eclipse) : http://${IP:-127.0.0.1}:${PORT:-8000}/manifest.json"
echo ""

if [ ! -d .venv ]; then
  echo ">>> Création de l'environnement virtuel..."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

echo ">>> Démarrage de l'addon (Ctrl+C pour arrêter)..."
PORT="${PORT:-8000}" "$(pwd)/.venv/bin/python" app.py
