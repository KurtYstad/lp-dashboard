#!/usr/bin/env bash
set -euo pipefail

BACKUP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ARCHIVE_DIR="$BACKUP_DIR/archive"
ENV_FILE="$BACKUP_DIR/.env"
umask 077

if [[ ! -r "$ENV_FILE" ]]; then
    echo "Backup configuration not found or unreadable: $ENV_FILE" >&2
    exit 1
fi

DB_USER=""
DB_PASSWORD=""
DB_HOST="localhost"
DB_PORT="3306"
DB_NAME=""
FTP_HOST=""
FTP_PORT="21"
FTP_USER=""
FTP_PASSWORD=""
FTP_REMOTE_DIR=""
FTP_TLS="0"

while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *=* ]] || continue

    key="${line%%=*}"
    value="${line#*=}"
    key="${key//[[:space:]]/}"
    value="${value#"${value%%[![:space:]]*}"}"
    value="${value%"${value##*[![:space:]]}"}"

    if [[ ${#value} -ge 2 ]]; then
        first_char="${value:0:1}"
        last_char="${value: -1}"
        if [[ "$first_char" == "$last_char" && ( "$first_char" == '"' || "$first_char" == "'" ) ]]; then
            value="${value:1:${#value}-2}"
        fi
    fi

    case "$key" in
        DB_USER) DB_USER="$value" ;;
        DB_PASSWORD) DB_PASSWORD="$value" ;;
        DB_HOST) DB_HOST="$value" ;;
        DB_PORT) DB_PORT="$value" ;;
        DB_NAME) DB_NAME="$value" ;;
        FTP_HOST) FTP_HOST="$value" ;;
        FTP_PORT) FTP_PORT="$value" ;;
        FTP_USER) FTP_USER="$value" ;;
        FTP_PASSWORD) FTP_PASSWORD="$value" ;;
        FTP_REMOTE_DIR) FTP_REMOTE_DIR="$value" ;;
        FTP_TLS) FTP_TLS="$value" ;;
    esac
done < "$ENV_FILE"

required_values=(DB_USER DB_PASSWORD DB_NAME FTP_HOST FTP_USER FTP_PASSWORD)
for name in "${required_values[@]}"; do
    if [[ -z "${!name}" ]]; then
        echo "Missing required setting in $ENV_FILE: $name" >&2
        exit 1
    fi
done

if [[ ! "$DB_NAME" =~ ^[A-Za-z0-9_]+$ ]]; then
    echo "DB_NAME must contain only letters, digits, and underscores." >&2
    exit 1
fi

if [[ ! "$DB_PORT" =~ ^[0-9]+$ ]] || (( DB_PORT < 1 || DB_PORT > 65535 )); then
    echo "DB_PORT must be a valid TCP port." >&2
    exit 1
fi

if [[ ! "$FTP_HOST" =~ ^[A-Za-z0-9.-]+$ ]]; then
    echo "FTP_HOST must be a hostname or IPv4 address." >&2
    exit 1
fi

if [[ ! "$FTP_PORT" =~ ^[0-9]+$ ]] || (( FTP_PORT < 1 || FTP_PORT > 65535 )); then
    echo "FTP_PORT must be a valid TCP port." >&2
    exit 1
fi

if [[ ! "$FTP_REMOTE_DIR" =~ ^[A-Za-z0-9._/-]*$ || "$FTP_REMOTE_DIR" == *"/../"* || "$FTP_REMOTE_DIR" == ../* || "$FTP_REMOTE_DIR" == */.. ]]; then
    echo "FTP_REMOTE_DIR may contain only letters, digits, '.', '_', '/', and '-' (without '..' path segments)." >&2
    exit 1
fi

if [[ "$FTP_TLS" != "0" && "$FTP_TLS" != "1" ]]; then
    echo "FTP_TLS must be 0 (FTP) or 1 (explicit FTPS)." >&2
    exit 1
fi

if ! command -v mariadb-dump >/dev/null 2>&1; then
    echo "mariadb-dump was not found; install the MariaDB client tools." >&2
    exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
    echo "curl was not found; install curl to upload the backup." >&2
    exit 1
fi

mkdir -p -- "$ARCHIVE_DIR"

escape_option_value() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    value="${value//$'\n'/\\n}"
    value="${value//$'\r'/\\r}"
    printf '"%s"' "$value"
}

escape_curl_value() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf '%s' "$value"
}

DB_CONFIG="$(mktemp)"
CURL_CONFIG="$(mktemp)"
TEMP_DUMP=""
cleanup() {
    rm -f -- "$DB_CONFIG" "$CURL_CONFIG"
    if [[ -n "$TEMP_DUMP" ]]; then
        rm -f -- "$TEMP_DUMP"
    fi
}
trap cleanup EXIT

{
    printf '[client]\n'
    printf 'host=%s\n' "$(escape_option_value "$DB_HOST")"
    printf 'port=%s\n' "$DB_PORT"
    printf 'user=%s\n' "$(escape_option_value "$DB_USER")"
    printf 'password=%s\n' "$(escape_option_value "$DB_PASSWORD")"
} > "$DB_CONFIG"

printf 'user = "%s:%s"\n' \
    "$(escape_curl_value "$FTP_USER")" \
    "$(escape_curl_value "$FTP_PASSWORD")" > "$CURL_CONFIG"

timestamp="$(date '+%Y-%m-%d_%H-%M-%S')"
backup_file="$ARCHIVE_DIR/${timestamp}_backup.sql"
TEMP_DUMP="${backup_file}.tmp.$$"

if [[ -e "$backup_file" ]]; then
    echo "A backup with this timestamp already exists: $backup_file" >&2
    exit 1
fi

echo "Creating database backup: $(basename "$backup_file")"
mariadb-dump \
    --defaults-extra-file="$DB_CONFIG" \
    --single-transaction \
    --quick \
    --routines \
    --events \
    --triggers \
    --hex-blob \
    --databases "$DB_NAME" > "$TEMP_DUMP"
mv -- "$TEMP_DUMP" "$backup_file"
TEMP_DUMP=""

remote_dir="${FTP_REMOTE_DIR#/}"
remote_dir="${remote_dir%/}"
if [[ -n "$remote_dir" ]]; then
    remote_url="ftp://${FTP_HOST}:${FTP_PORT}/${remote_dir}/$(basename "$backup_file")"
else
    remote_url="ftp://${FTP_HOST}:${FTP_PORT}/$(basename "$backup_file")"
fi

curl_args=(--config "$CURL_CONFIG" --fail --show-error --silent --ftp-create-dirs --upload-file "$backup_file")
if [[ "$FTP_TLS" == "1" ]]; then
    curl_args+=(--ssl-reqd)
fi

echo "Uploading backup to ${FTP_HOST}:${FTP_PORT}"
curl "${curl_args[@]}" "$remote_url"
echo "Backup created and uploaded successfully."
