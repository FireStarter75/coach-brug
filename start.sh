#!/bin/bash
# Start de coach-brug.
#
# Deze container hangt NIET aan het LAN en niet aan Cloudflare: hij zit op een
# eigen docker-netwerk en is alleen bereikbaar voor het dashboard, met een token.
# Hij krijgt geen mount van de dashboardcode of -data; alleen de gesloten
# Vibe-configuratie (vibe-home/, met session-logging uit).
#
# De Vibe CLI zit in het image zelf (pip install mistral-vibe); hij praat met
# een Mistral API-key uit .env in plaats van met een Claude-abonnement.
set -e
BASE=/mnt/user/appdata/garmin-coach
source "$BASE/.env"

if [ -z "$MISTRAL_API_KEY" ]; then
  echo "FOUT: MISTRAL_API_KEY ontbreekt in $BASE/.env — vul die eerst in." >&2
  exit 1
fi
if [ -z "$COACH_TOKEN" ]; then
  echo "FOUT: COACH_TOKEN ontbreekt in $BASE/.env." >&2
  exit 1
fi

docker network inspect garmin-net >/dev/null 2>&1 || docker network create garmin-net

docker rm -f garmin-coach 2>/dev/null || true

docker run -d --name garmin-coach \
  --restart unless-stopped \
  --network garmin-net \
  -e TZ=Europe/Amsterdam \
  -e HOME=/home/coach \
  -e VIBE_HOME=/home/coach/.vibe \
  -e COACH_TOKEN="$COACH_TOKEN" \
  -e COACH_MODEL=mistral-medium-3.5 \
  -e COACH_TIMEOUT=150 \
  -e COACH_CONCURRENCY=2 \
  -e COACH_MAX_BUDGET_USD=0.50 \
  -e MISTRAL_API_KEY="$MISTRAL_API_KEY" \
  -v "$BASE/vibe-home:/home/coach/.vibe" \
  --memory 2g \
  garmin-coach:latest

sleep 3
docker ps --filter name=garmin-coach --format '{{.Names}}  {{.Status}}'
