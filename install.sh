#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
BACKUP_SQL="$PROJECT_DIR/backup.sql"
TMP_ADMIN_SQL="$(mktemp)"
TMP_RESTORE_SQL="$(mktemp)"
TMP_ADMIN_CNF="$(mktemp)"
TMP_PENDING="$(mktemp)"
DB_ADMIN_MODE=""
INSTALL_SYSTEMD="no"
DB_USER_VALUE=""
DB_PASSWORD_VALUE=""
DB_HOST_VALUE="localhost"
DB_PORT_VALUE="3306"
DB_NAME_VALUE="lpdashboard"
ALCHEMY_API_KEY_VALUE=""
HELIUS_API_KEY_VALUE=""
CT_KEY_VALUE=""
CT_SECRET_VALUE=""
TELEGRAM_TOKEN_VALUE=""
TELEGRAM_CHAT_ID_VALUE=""
AERO_STAKER_ADDRESS_VALUE=""

cleanup() {
    rm -f -- "$TMP_ADMIN_SQL" "$TMP_RESTORE_SQL" "$TMP_ADMIN_CNF" "$TMP_PENDING"
}
trap cleanup EXIT

step() {
    printf '\n\033[1;32m==> %s\033[0m\n' "$1"
}

confirm() {
    local answer
    read -r -p "$1 [j/N]: " answer
    [[ "$answer" =~ ^[JjYy]$ ]]
}

prompt_value() {
    local variable_name="$1" label="$2" default_value="$3" answer
    read -r -p "$label [$default_value]: " answer
    printf -v "$variable_name" '%s' "${answer:-$default_value}"
}

prompt_secret() {
    local variable_name="$1" label="$2" answer
    read -r -s -p "$label (leer = überspringen): " answer
    printf '\n' >&2
    printf -v "$variable_name" '%s' "$answer"
}

valid_identifier() {
    [[ "$1" =~ ^[A-Za-z0-9_-]{1,64}$ ]]
}

valid_db_user() {
    [[ "$1" =~ ^[A-Za-z0-9_-]+$ ]]
}

run_db_admin() {
    if [[ "$DB_ADMIN_MODE" == "sudo" ]]; then
        sudo mariadb --protocol=socket "$@"
    else
        mariadb --defaults-extra-file="$TMP_ADMIN_CNF" "$@"
    fi
}

install_os_packages() {
    local include_server="$1"
    local -a packages

    if command -v apt-get >/dev/null 2>&1; then
        packages=(mariadb-client build-essential pkg-config libmariadb-dev python3-venv)
        if [[ "$include_server" == "yes" ]]; then
            packages+=(mariadb-server)
        fi
        sudo apt-get update
        sudo apt-get install -y "${packages[@]}"
    elif command -v dnf >/dev/null 2>&1; then
        packages=(mariadb gcc pkgconf-pkg-config mariadb-connector-c-devel python3-devel)
        if [[ "$include_server" == "yes" ]]; then
            packages+=(mariadb-server)
        fi
        sudo dnf install -y "${packages[@]}"
    elif command -v yum >/dev/null 2>&1; then
        packages=(mariadb gcc pkgconfig mariadb-connector-c-devel python3-devel)
        if [[ "$include_server" == "yes" ]]; then
            packages+=(mariadb-server)
        fi
        sudo yum install -y "${packages[@]}"
    elif command -v zypper >/dev/null 2>&1; then
        packages=(mariadb-client gcc pkg-config libmariadb-devel python3-devel)
        if [[ "$include_server" == "yes" ]]; then
            packages+=(mariadb)
        fi
        sudo zypper --non-interactive install "${packages[@]}"
    else
        echo "Kein unterstützter Paketmanager (apt, dnf, yum oder zypper) gefunden." >&2
        return 1
    fi
}

mariadb_installed() {
    command -v mariadb >/dev/null 2>&1 &&
        systemctl list-unit-files mariadb.service --no-legend 2>/dev/null |
            grep -q '^mariadb.service'
}

start_mariadb() {
    if systemctl list-unit-files mariadb.service --no-legend 2>/dev/null | grep -q '^mariadb.service'; then
        sudo systemctl enable --now mariadb
    else
        echo "MariaDB wurde installiert, aber kein mariadb.service-systemd-Dienst gefunden." >&2
        return 1
    fi
}

configure_db_admin() {
    local admin_user admin_password

    if sudo mariadb --protocol=socket --batch --skip-column-names -e "SELECT 1" >/dev/null 2>&1; then
        DB_ADMIN_MODE="sudo"
        return
    fi

    echo "Der lokale MariaDB-Root-Zugang über sudo konnte nicht verwendet werden."
    prompt_value admin_user "MariaDB-Administrationsbenutzer" "root"
    prompt_secret admin_password "Passwort des MariaDB-Administrationsbenutzers"
    if [[ -z "$admin_password" ]]; then
        echo "Ohne MariaDB-Administrationszugang kann die Datenbank nicht eingerichtet werden." >&2
        return 1
    fi

    ADMIN_USER_VALUE="$admin_user" ADMIN_PASSWORD_VALUE="$admin_password" \
        python3 - >"$TMP_ADMIN_CNF" <<'PY'
import os

def quote_option(value):
    if "\n" in value or "\r" in value:
        raise SystemExit("MariaDB-Anmeldedaten dürfen keine Zeilenumbrüche enthalten.")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

print("[client]")
print("user=" + quote_option(os.environ["ADMIN_USER_VALUE"]))
print("password=" + quote_option(os.environ["ADMIN_PASSWORD_VALUE"]))
print("protocol=socket")
PY
    chmod 600 "$TMP_ADMIN_CNF"
    DB_ADMIN_MODE="cnf"
    run_db_admin --batch --skip-column-names -e "SELECT 1" >/dev/null
}

prepare_restore() {
    DB_NAME_VALUE="$DB_NAME_VALUE" BACKUP_SQL="$BACKUP_SQL" \
        python3 - "$TMP_RESTORE_SQL" <<'PY'
import os
import re
import sys

source = os.environ["BACKUP_SQL"]
database = os.environ["DB_NAME_VALUE"]
destination = sys.argv[1]
required_tables = {
    "aero", "arbitrum", "base", "bsc", "cake", "ethereum", "optimism",
    "orca", "positions", "polygon", "raydium", "robinhood", "total",
}
found_tables = set()
found_use = False

with open(source, encoding="utf-8") as backup, open(destination, "w", encoding="utf-8") as output:
    for line in backup:
        match = re.match(r"\s*CREATE TABLE\s+`([^`]+)`", line, re.IGNORECASE)
        if match:
            found_tables.add(match.group(1))

        if re.match(r"\s*CREATE DATABASE\b", line, re.IGNORECASE):
            continue
        if re.match(r"\s*USE\s+`uniswap`\s*;", line, re.IGNORECASE):
            output.write(f"USE `{database}`;\n")
            found_use = True
            continue
        output.write(line)

missing_tables = sorted(required_tables - found_tables)
if missing_tables:
    raise SystemExit("Im Backup fehlen erwartete Tabellen: " + ", ".join(missing_tables))
if not found_use:
    raise SystemExit("Im Backup wurde kein erwartetes USE `uniswap` gefunden.")
PY
    chmod 600 "$TMP_RESTORE_SQL"
}

write_database_setup_sql() {
    DB_NAME_VALUE="$DB_NAME_VALUE" \
    DB_USER_VALUE="$DB_USER_VALUE" \
    DB_PASSWORD_VALUE="$DB_PASSWORD_VALUE" \
    DB_USER_MODE_VALUE="$DB_USER_MODE" \
        python3 - >"$TMP_ADMIN_SQL" <<'PY'
import os
import re

database = os.environ["DB_NAME_VALUE"]
user = os.environ["DB_USER_VALUE"]
password = os.environ["DB_PASSWORD_VALUE"]
user_mode = os.environ["DB_USER_MODE_VALUE"]

if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", database):
    raise SystemExit("Ungültiger Datenbankname.")
if user and not re.fullmatch(r"[A-Za-z0-9_-]+", user):
    raise SystemExit("Ungültiger Datenbankbenutzername.")

def quote_sql(value):
    return "'" + value.replace("\\", "\\\\").replace("'", "''") + "'"

print(f"CREATE DATABASE IF NOT EXISTS `{database}` CHARACTER SET utf8mb4;")
if user and (user_mode == "existing" or password):
    account = quote_sql(user) + "@'localhost'"
    if user_mode == "new":
        print(f"CREATE USER IF NOT EXISTS {account} IDENTIFIED BY {quote_sql(password)};")
        print(f"ALTER USER {account} IDENTIFIED BY {quote_sql(password)};")
    print(f"GRANT ALL PRIVILEGES ON `{database}`.* TO {account};")
PY
    chmod 600 "$TMP_ADMIN_SQL"
}

configure_files() {
    DB_USER_VALUE="$DB_USER_VALUE" \
    DB_PASSWORD_VALUE="$DB_PASSWORD_VALUE" \
    DB_HOST_VALUE="$DB_HOST_VALUE" \
    DB_PORT_VALUE="$DB_PORT_VALUE" \
    DB_NAME_VALUE="$DB_NAME_VALUE" \
    ALCHEMY_API_KEY_VALUE="$ALCHEMY_API_KEY_VALUE" \
    HELIUS_API_KEY_VALUE="$HELIUS_API_KEY_VALUE" \
    CT_KEY_VALUE="$CT_KEY_VALUE" \
    CT_SECRET_VALUE="$CT_SECRET_VALUE" \
    TELEGRAM_TOKEN_VALUE="$TELEGRAM_TOKEN_VALUE" \
    TELEGRAM_CHAT_ID_VALUE="$TELEGRAM_CHAT_ID_VALUE" \
    AERO_STAKER_ADDRESS_VALUE="$AERO_STAKER_ADDRESS_VALUE" \
    python3 - "$PROJECT_DIR" "$TMP_PENDING" <<'PY'
import os
import sys
from pathlib import Path

project = Path(sys.argv[1])
pending_path = Path(sys.argv[2])

database_keys = {
    "DB_USER": "DB_USER_VALUE",
    "DB_PASSWORD": "DB_PASSWORD_VALUE",
    "DB_HOST": "DB_HOST_VALUE",
    "DB_PORT": "DB_PORT_VALUE",
    "DB_NAME": "DB_NAME_VALUE",
}
env_files = {
    ".env.example": {
        **database_keys,
        "ALCHEMY_API_KEY": "ALCHEMY_API_KEY_VALUE",
        "HELIUS_API_KEY": "HELIUS_API_KEY_VALUE",
        "ct_key": "CT_KEY_VALUE",
        "ct_secret": "CT_SECRET_VALUE",
    },
    "notify/aero/.env.example": {
        **database_keys,
        "ALCHEMY_API_KEY": "ALCHEMY_API_KEY_VALUE",
        "AERO_STAKER_ADDRESS": "AERO_STAKER_ADDRESS_VALUE",
    },
    "notify/orca/.env.example": {
        **database_keys,
        "HELIUS_API_KEY": "HELIUS_API_KEY_VALUE",
    },
    "notify/raydium/.env.example": {
        **database_keys,
        "HELIUS_API_KEY": "HELIUS_API_KEY_VALUE",
    },
    "notify/pancakeswap/.env.example": database_keys,
    "notify/uniswap/arbitrum/.env.example": {
        **database_keys,
        "ALCHEMY_API_KEY": "ALCHEMY_API_KEY_VALUE",
    },
    "notify/uniswap/base/.env.example": {
        **database_keys,
        "ALCHEMY_API_KEY": "ALCHEMY_API_KEY_VALUE",
    },
    "notify/uniswap/ethereum/.env.example": {
        **database_keys,
        "ALCHEMY_API_KEY": "ALCHEMY_API_KEY_VALUE",
    },
    "notify/uniswap/polygon/.env.example": {
        **database_keys,
        "ALCHEMY_API_KEY": "ALCHEMY_API_KEY_VALUE",
    },
    "notify/uniswap/robinhood/.env.example": {
        **database_keys,
        "ALCHEMY_API_KEY": "ALCHEMY_API_KEY_VALUE",
    },
    "notify/uniswap/total/.env.example": {
        **database_keys,
        "ALCHEMY_API_KEY": "ALCHEMY_API_KEY_VALUE",
        "HELIUS_API_KEY": "HELIUS_API_KEY_VALUE",
    },
}
telegram_files = [
    "telegram.conf.example",
    "notify/aero/telegram.conf.example",
    "notify/orca/telegram.conf.example",
    "notify/pancakeswap/telegram.conf.example",
    "notify/raydium/telegram.conf.example",
    "notify/uniswap/arbitrum/telegram.conf.example",
    "notify/uniswap/base/telegram.conf.example",
    "notify/uniswap/ethereum/telegram.conf.example",
    "notify/uniswap/polygon/telegram.conf.example",
    "notify/uniswap/robinhood/telegram.conf.example",
    "notify/uniswap/total/telegram.conf.example",
]
pending = []

def quote_env(value):
    if "\n" in value or "\r" in value:
        raise SystemExit("Credential values dürfen keine Zeilenumbrüche enthalten.")
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"

def write_config(template_name, destination_name, fields, file_format):
    template = project / template_name
    destination = project / destination_name
    optional_fields = {"ct_key", "ct_secret", "AERO_STAKER_ADDRESS"}
    if destination.exists():
        print(f"Vorhandene Datei bleibt unverändert: {destination_name}")
        destination.chmod(0o600)
        values = {}
        for line in destination.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            if separator:
                values[key] = value.strip().strip("'\"")
        for key in fields:
            value = values.get(key, "")
            if not value or "replace_with_" in value:
                suffix = " (optional)" if key in optional_fields else ""
                pending.append(f"{destination_name}: {key}{suffix}")
        return
    if not template.is_file():
        raise SystemExit(f"Beispieldatei fehlt: {template_name}")

    lines = template.read_text(encoding="utf-8").splitlines()
    seen = set()
    output = []
    for line in lines:
        key, separator, _ = line.partition("=")
        if separator and key in fields:
            seen.add(key)
            value = os.environ.get(fields[key], "")
            if value:
                output.append(f"{key}={quote_env(value) if file_format == 'env' else value}")
            else:
                output.append(f"{key}={quote_env('') if file_format == 'env' else ''}")
                suffix = " (optional)" if key in optional_fields else ""
                pending.append(f"{destination_name}: {key}{suffix}")
        else:
            output.append(line)

    missing_keys = set(fields) - seen
    if missing_keys:
        raise SystemExit(
            f"{template_name} enthält erwartete Felder nicht: "
            + ", ".join(sorted(missing_keys))
        )

    template.chmod(0o600)
    template.write_text("\n".join(output) + "\n", encoding="utf-8")
    template.replace(destination)
    destination.chmod(0o600)
    print(f"Konfiguration erstellt: {destination_name}")

for relative_path, fields in env_files.items():
    write_config(relative_path, relative_path.removesuffix(".example"), fields, "env")

for relative_path in telegram_files:
    write_config(
        relative_path,
        relative_path.removesuffix(".example"),
        {"token": "TELEGRAM_TOKEN_VALUE", "chat_id": "TELEGRAM_CHAT_ID_VALUE"},
        "telegram",
    )

pending_path.write_text("\n".join(pending) + ("\n" if pending else ""), encoding="utf-8")
PY
}

echo "LP Dashboard - interaktive Installation"
echo "Dieses Script richtet MariaDB, die Python-Umgebung, Konfigurationen und den Dashboard-Webdienst ein."
echo "Die Monitoring- und Positionsimport-Timer können optional mitinstalliert werden."

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Die Installation benötigt Linux mit systemd." >&2
    exit 1
fi
if (( EUID == 0 )); then
    echo "Bitte als normaler Benutzer starten, nicht mit sudo. Das Script fragt sudo bei Bedarf selbst ab." >&2
    exit 1
fi
if ! command -v sudo >/dev/null 2>&1 || ! command -v systemctl >/dev/null 2>&1; then
    echo "sudo und systemctl werden für die MariaDB- und systemd-Einrichtung benötigt." >&2
    exit 1
fi
if [[ ! -f "$BACKUP_SQL" ]]; then
    echo "backup.sql fehlt im Projekt-Root: $BACKUP_SQL" >&2
    exit 1
fi
if [[ ! -x "$PROJECT_DIR/install-systemd.sh" ]]; then
    echo "install-systemd.sh fehlt oder ist nicht ausführbar." >&2
    exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 ist nicht installiert. Bitte Python 3.10 oder neuer installieren." >&2
    exit 1
fi
if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
    echo "Python 3.10 oder neuer wird benötigt." >&2
    exit 1
fi

step "1/6: Systemdienste auswählen"
echo "Der Dashboard-Webdienst wird unabhängig von dieser Auswahl installiert und gestartet."
if confirm "Sollen zusätzlich die Monitoring- und Positionsimport-Timer über systemd installiert und aktiviert werden?"; then
    INSTALL_SYSTEMD="yes"
fi

step "2/6: MariaDB bereitstellen"
if ! mariadb_installed; then
    if ! confirm "MariaDB wurde nicht gefunden. Soll MariaDB jetzt installiert werden?"; then
        echo "Ohne MariaDB wird die Installation beendet. Für eine vorhandene Remote-Datenbank ist dieses Script nicht vorgesehen." >&2
        exit 1
    fi
fi

sudo -v
if mariadb_installed; then
    INSTALL_DB_SERVER="no"
else
    INSTALL_DB_SERVER="yes"
fi
install_os_packages "$INSTALL_DB_SERVER"
if ! command -v mariadb >/dev/null 2>&1; then
    echo "Der MariaDB-Client wurde nach der Paketinstallation nicht gefunden." >&2
    exit 1
fi
start_mariadb

step "3/6: Datenbank aus backup.sql einrichten"
while true; do
    prompt_value DB_NAME_VALUE "Name der MariaDB-Datenbank" "lpdashboard"
    if valid_identifier "$DB_NAME_VALUE"; then
        break
    fi
    echo "Der Datenbankname darf nur Buchstaben, Ziffern, Unterstriche und Bindestriche enthalten (maximal 64 Zeichen)."
done
prepare_restore

echo "Datenbankbenutzer:"
echo "  1) Neuen Benutzer lpdashboard anlegen (Standard)"
echo "  2) Vorhandenen MariaDB-Benutzer verwenden"
while true; do
    read -r -p "Auswahl [1]: " DB_USER_MODE_INPUT
    DB_USER_MODE_CHOICE="${DB_USER_MODE_INPUT:-1}"
    if [[ "$DB_USER_MODE_CHOICE" == "1" || "$DB_USER_MODE_CHOICE" == "2" ]]; then
        break
    fi
    echo "Bitte 1 oder 2 eingeben."
done
if [[ "$DB_USER_MODE_CHOICE" == "2" ]]; then
    DB_USER_MODE="existing"
    while true; do
        read -r -p "Vorhandener MariaDB-Benutzer (leer = überspringen): " DB_USER_VALUE
        if [[ -z "$DB_USER_VALUE" ]] || valid_db_user "$DB_USER_VALUE"; then
            break
        fi
        echo "Erlaubt sind Buchstaben, Ziffern, Unterstriche und Bindestriche."
    done
else
    DB_USER_MODE="new"
    DB_USER_VALUE="lpdashboard"
fi

if [[ -n "$DB_USER_VALUE" ]]; then
    prompt_secret DB_PASSWORD_VALUE "Passwort für MariaDB-Benutzer $DB_USER_VALUE"
    if [[ "$DB_USER_MODE" == "new" && -n "$DB_PASSWORD_VALUE" ]]; then
        while true; do
            local_password_confirmation=""
            prompt_secret local_password_confirmation "Passwort wiederholen"
            if [[ "$DB_PASSWORD_VALUE" == "$local_password_confirmation" ]]; then
                break
            fi
            echo "Die Passwörter stimmen nicht überein."
            prompt_secret DB_PASSWORD_VALUE "Passwort für MariaDB-Benutzer $DB_USER_VALUE"
            if [[ -z "$DB_PASSWORD_VALUE" ]]; then
                break
            fi
        done
    fi
fi

configure_db_admin
if ! run_db_admin --batch --skip-column-names -e \
    "SELECT COLLATION_NAME FROM INFORMATION_SCHEMA.COLLATIONS WHERE COLLATION_NAME='utf8mb4_uca1400_ai_ci'" |
    grep -q '^utf8mb4_uca1400_ai_ci$'; then
    echo "Die Kollation utf8mb4_uca1400_ai_ci wird nicht unterstützt; verwende utf8mb4_general_ci für den Restore."
    sed -i 's/utf8mb4_uca1400_ai_ci/utf8mb4_general_ci/g' "$TMP_RESTORE_SQL"
fi
DB_EXISTS="$(run_db_admin --batch --skip-column-names -e \
    "SELECT SCHEMA_NAME FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME='${DB_NAME_VALUE}'")"
if [[ -n "$DB_EXISTS" ]]; then
    echo "Die Datenbank '$DB_NAME_VALUE' existiert bereits."
else
    echo "Die Datenbank '$DB_NAME_VALUE' wird neu angelegt."
fi

DB_USER_MODE_VALUE="$DB_USER_MODE" write_database_setup_sql
run_db_admin <"$TMP_ADMIN_SQL"

RESTORE_BACKUP="yes"
if [[ -n "$DB_EXISTS" ]]; then
    if ! confirm "backup.sql enthält nur Tabellenstrukturen und ersetzt gleichnamige Tabellen in '$DB_NAME_VALUE' (vorhandene Tabellendaten gehen verloren). Tabellenstrukturen importieren?"; then
        RESTORE_BACKUP="no"
    fi
else
    echo "Das Backup mit den leeren Tabellenstrukturen wird importiert."
fi

if [[ "$RESTORE_BACKUP" == "yes" ]]; then
    run_db_admin <"$TMP_RESTORE_SQL"
    echo "Datenbank-Backup wurde importiert."
else
    echo "Restore übersprungen; die vorhandene Datenbank wurde nicht aus backup.sql ersetzt."
fi

step "4/6: API- und Telegram-Zugangsdaten abfragen"
echo "Leere Eingaben werden übersprungen und am Ende mit dem jeweiligen Dateipfad aufgelistet."
prompt_secret ALCHEMY_API_KEY_VALUE "Alchemy API Key (EVM-Netzwerke)"
prompt_secret HELIUS_API_KEY_VALUE "Helius API Key (Solana)"
prompt_secret CT_KEY_VALUE "CoinTracking API Key (optional für Portfolio-Gesamtwert)"
prompt_secret CT_SECRET_VALUE "CoinTracking API Secret (optional für Portfolio-Gesamtwert)"
prompt_secret AERO_STAKER_ADDRESS_VALUE "Aerodrome Staker-Adresse (optional für Rewards)"
prompt_secret TELEGRAM_TOKEN_VALUE "Telegram Bot Token (wird für alle Notify-Skripte verwendet)"
prompt_secret TELEGRAM_CHAT_ID_VALUE "Telegram Chat ID (wird für alle Notify-Skripte verwendet)"

if [[ -z "$DB_USER_VALUE" ]]; then
    echo "Kein Anwendungs-DB-Benutzer angegeben; die Datenbank-Zugangsdaten bleiben in den Konfigurationsdateien offen."
fi
if [[ -z "$DB_PASSWORD_VALUE" ]]; then
    echo "Kein Anwendungs-DB-Passwort angegeben; die Datenbank-Zugangsdaten bleiben in den Konfigurationsdateien offen."
fi

step "5/6: Konfigurationen und Python-Umgebung erstellen"
configure_files

if [[ -e "$PROJECT_DIR/.venv" ]]; then
    if [[ ! -x "$PROJECT_DIR/.venv/bin/python" ]]; then
        echo "$PROJECT_DIR/.venv existiert, enthält aber keine verwendbare Python-Umgebung." >&2
        exit 1
    fi
    echo "Vorhandene virtuelle Umgebung wird weiterverwendet."
else
    python3 -m venv "$PROJECT_DIR/.venv"
fi

"$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$PROJECT_DIR/.venv/bin/python" -m pip install \
    "uvicorn[standard]" \
    fastapi \
    python-multipart \
    jinja2 \
    pandas \
    mariadb \
    python-dotenv \
    web3 \
    httpx \
    eth-abi \
    eth-utils \
    "python-telegram-bot<14" \
    telegram-send \
    matplotlib \
    numpy \
    solana \
    solders

step "6/6: Dashboard-Webdienst und gewählte systemd-Dienste installieren"
SYSTEMD_INSTALL_MODE="--dashboard-only"
if [[ "$INSTALL_SYSTEMD" == "yes" ]]; then
    if ! grep -qv ' (optional)$' "$TMP_PENDING"; then
        SYSTEMD_INSTALL_MODE=""
    else
        echo "Mindestens eine erforderliche Konfiguration ist unvollständig. Die Monitoring- und Positionsimport-Timer werden erst nach dem Ergänzen der unten aufgeführten Angaben aktiviert."
        INSTALL_SYSTEMD="deferred"
    fi
fi

if [[ -n "$SYSTEMD_INSTALL_MODE" ]]; then
    sudo env LP_DASHBOARD_USER="$(id -un)" "$PROJECT_DIR/install-systemd.sh" "$SYSTEMD_INSTALL_MODE"
else
    sudo env LP_DASHBOARD_USER="$(id -un)" "$PROJECT_DIR/install-systemd.sh"
fi

if [[ "$INSTALL_SYSTEMD" == "no" ]]; then
    echo "Monitoring- und Positionsimport-Timer wurden auf Wunsch nicht installiert."
fi

echo
if [[ -s "$TMP_PENDING" ]]; then
    echo "Noch zu hinterlegende Angaben:"
    while IFS= read -r item; do
        printf '  - %s\n' "$item"
    done <"$TMP_PENDING"
else
    echo "Alle abgefragten Zugangsdaten wurden in den Konfigurationsdateien hinterlegt."
fi

if [[ "$INSTALL_SYSTEMD" == "deferred" ]]; then
    echo "Nach dem Ergänzen der Konfigurationen kann sudo ./install-systemd.sh die Monitoring- und Positionsimport-Timer installieren."
fi
if [[ "$RESTORE_BACKUP" == "no" ]]; then
    echo "Hinweis: backup.sql wurde nicht importiert; prüfe, ob die vorhandene Datenbank alle benötigten Tabellen enthält."
fi
echo "Installation abgeschlossen."
