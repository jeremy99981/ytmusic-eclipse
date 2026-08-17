#!/usr/bin/env bash
set -e

# 1) start the Proof-of-Origin Token provider (lets the web client pass
#    YouTube's "are you a bot" check from a datacenter IP, no cookies needed)
node /opt/bgutil/server/build/main.js --port 4416 > /tmp/pot.log 2>&1 &

# 2) wait until the POT server is accepting connections
for i in $(seq 1 30); do
  if curl -s -o /dev/null http://127.0.0.1:4416/ping; then
    echo "POT provider ready"; break
  fi
  sleep 0.5
done

# 3) start the addon web server
exec gunicorn app:app \
  --bind 0.0.0.0:${PORT:-10000} \
  --workers 1 --threads 8 --timeout 180
