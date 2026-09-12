#!/bin/bash
set -euo pipefail

source /root/.nextcloud_backup_credentials

# Wie export.py: Pfad relativ zum eigenen Skript-Speicherort, unabhaengig
# vom Checkout-Ort auf diesem Server.
SCRIPT_DIR="$(cd "$(dirname "$(realpath "${BASH_SOURCE[0]}")")" && pwd)"
SRC="${SCRIPT_DIR}/output/neue_woerter.csv"

NC_FOLDER="BT_firstsaid_Projekt"

NEXTCLOUD_URL="https://files.btv-opendesk.de/remote.php/dav/files/${NEXTCLOUD_USER}"
DATE=$(date +%F)
DEST="${NEXTCLOUD_URL}/${NC_FOLDER}/neue_woerter-${DATE}.csv"

curl -s -o /dev/null -u "${NEXTCLOUD_USER}:${NEXTCLOUD_PASS}" -X MKCOL "${NEXTCLOUD_URL}/${NC_FOLDER}/" || true
curl -sSf -u "${NEXTCLOUD_USER}:${NEXTCLOUD_PASS}" -T "$SRC" "$DEST"
