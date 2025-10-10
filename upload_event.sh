#!/usr/bin/env bash
set -euo pipefail

LOCAL_PATH="${1:-}"
REMOTE_NAME="${2:-}"

FTP_SCHEME="${FTP_SCHEME:-ftp}"               # ftp | ftps | sftp
FTP_HOST="${FTP_HOST:-}"
FTP_USER="${FTP_USER:-}"
FTP_PASS="${FTP_PASS:-}"
FTP_REMOTE_DIR="${FTP_REMOTE_DIR:-/}"
SLEEP_BETWEEN="${SLEEP_BETWEEN:-2}"

trim_one(){ printf '%s' "$1" | tr -d '\r' | sed 's/^[ \t]\+//; s/[ \t]\+$//'; }
trim_all_lines(){ tr -d '\r' | sed 's/^[ \t]\+//; s/[ \t]\+$//' | tr -d '\n'; }

FTP_SCHEME="$(trim_one "$FTP_SCHEME")"
FTP_HOST="$(trim_one "$FTP_HOST")"
FTP_USER="$(trim_one "$FTP_USER")"
FTP_PASS="$(trim_one "$FTP_PASS")"
FTP_REMOTE_DIR="$(printf '%s' "$FTP_REMOTE_DIR" | trim_all_lines)"

[[ -z "$LOCAL_PATH" || -z "$REMOTE_NAME" ]] && { echo "Uso: upload_event.sh <local_path> <remote_name>"; exit 2; }
[[ ! -f "$LOCAL_PATH" ]] && { echo "ℹ️ No existe $LOCAL_PATH; se omite."; exit 0; }
for v in FTP_HOST FTP_USER FTP_PASS; do [[ -z "${!v}" ]] && { echo "❌ Falta $v"; exit 1; }; done

[[ "$FTP_REMOTE_DIR" != /* ]] && FTP_REMOTE_DIR="/$FTP_REMOTE_DIR"
FTP_REMOTE_DIR="$(printf '%s' "$FTP_REMOTE_DIR" | sed 's://*:/:g; s:/*$::')"

URL_BASE="${FTP_SCHEME}://${FTP_HOST}${FTP_REMOTE_DIR}"
URL_FILE="${URL_BASE}/${REMOTE_NAME}"

CURL_OPTS=( -sS --fail --show-error --retry 3 --retry-delay 2 --user "$FTP_USER:$FTP_PASS" )
case "$FTP_SCHEME" in
  ftp|ftps) CURL_OPTS+=( --ftp-method nocwd --ftp-create-dirs ) ;;
  sftp)     : ;;
  *) echo "❌ Esquema no soportado: $FTP_SCHEME"; exit 1 ;;
esac

echo "⬆️  ${LOCAL_PATH} → ${URL_FILE}"
curl "${CURL_OPTS[@]}" -T "$LOCAL_PATH" "$URL_FILE"
echo "✅ Subido: ${REMOTE_NAME}"
sleep "$SLEEP_BETWEEN"
