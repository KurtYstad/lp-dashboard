#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
SYSTEMD_SOURCE_DIR="$PROJECT_DIR/systemd"
UNIT_DIR="/etc/systemd/system"
SERVICE_USER="${LP_DASHBOARD_USER:-${SUDO_USER:-}}"
INSTALL_MODE="all"

if (( $# > 1 )); then
    echo "Usage: $0 [--dashboard-only]" >&2
    exit 2
fi

case "${1:-}" in
    "")
        ;;
    --dashboard-only)
        INSTALL_MODE="dashboard-only"
        ;;
    *)
        echo "Usage: $0 [--dashboard-only]" >&2
        exit 2
        ;;
esac

if [[ "$(uname -s)" != "Linux" ]]; then
    echo "This installer requires Linux with systemd." >&2
    exit 1
fi

if (( EUID != 0 )); then
    echo "Run this installer with sudo." >&2
    exit 1
fi

if [[ -z "$SERVICE_USER" ]]; then
    echo "Set LP_DASHBOARD_USER or run via sudo from the service user's account." >&2
    exit 1
fi

if [[ ! "$SERVICE_USER" =~ ^[a-zA-Z_][a-zA-Z0-9_-]*$ ]] || ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "Invalid or unknown service user: $SERVICE_USER" >&2
    exit 1
fi

if [[ ! "$PROJECT_DIR" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    echo "Project path must be absolute and contain only letters, digits, /, ., _ or -: $PROJECT_DIR" >&2
    exit 1
fi

if [[ ! -x "$PROJECT_DIR/.venv/bin/python" || ! -x "$PROJECT_DIR/.venv/bin/uvicorn" ]]; then
    echo "Python environment or Uvicorn not found under $PROJECT_DIR/.venv/bin." >&2
    exit 1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl was not found." >&2
    exit 1
fi

NOTIFY_UNITS=(
    lp-dashboard-notify-uniswap-arbitrum
    lp-dashboard-notify-uniswap-ethereum
    lp-dashboard-notify-uniswap-polygon
    lp-dashboard-notify-uniswap-base
    lp-dashboard-notify-orca
    lp-dashboard-notify-raydium
    lp-dashboard-notify-pancakeswap
    lp-dashboard-notify-uniswap-robinhood
    lp-dashboard-notify-aero
    lp-dashboard-notify-uniswap-total-hourly
    lp-dashboard-notify-uniswap-total-daily
    lp-dashboard-notify-uniswap-total-monthly
)

NOTIFY_SCRIPTS=(
    notify/uniswap/arbitrum/uniswap.py
    notify/uniswap/ethereum/uniswap.py
    notify/uniswap/polygon/uniswap.py
    notify/uniswap/base/uniswap.py
    notify/orca/orca.py
    notify/raydium/raydium.py
    notify/pancakeswap/cake.py
    notify/uniswap/robinhood/uniswap.py
    notify/aero/aero.py
    notify/uniswap/total/total.py
    notify/uniswap/total/daily.py
    notify/uniswap/total/monthly.py
)

if [[ ! -f "$SYSTEMD_SOURCE_DIR/lp-dashboard-web.service" ]]; then
    echo "Unit file not found: $SYSTEMD_SOURCE_DIR/lp-dashboard-web.service" >&2
    exit 1
fi

if [[ "$INSTALL_MODE" == "all" ]]; then
    for script in "${NOTIFY_SCRIPTS[@]}"; do
        if [[ ! -f "$PROJECT_DIR/$script" ]]; then
            echo "Notify script not found: $PROJECT_DIR/$script" >&2
            exit 1
        fi
    done

    for unit in "${NOTIFY_UNITS[@]}"; do
        for extension in service timer; do
            if [[ ! -f "$SYSTEMD_SOURCE_DIR/$unit.$extension" ]]; then
                echo "Unit file not found: $SYSTEMD_SOURCE_DIR/$unit.$extension" >&2
                exit 1
            fi
        done
    done

    for unit in lp-dashboard-positions.service lp-dashboard-positions.timer; do
        if [[ ! -f "$SYSTEMD_SOURCE_DIR/$unit" ]]; then
            echo "Unit file not found: $SYSTEMD_SOURCE_DIR/$unit" >&2
            exit 1
        fi
    done
fi

TEMP_UNIT=""
cleanup_temp_unit() {
    if [[ -n "$TEMP_UNIT" && -f "$TEMP_UNIT" ]]; then
        rm -- "$TEMP_UNIT"
    fi
}
trap cleanup_temp_unit EXIT

install_unit() {
    local unit="$1"
    local source="$SYSTEMD_SOURCE_DIR/$unit"
    local destination="$UNIT_DIR/$unit"

    if [[ "$unit" == *.service ]]; then
        TEMP_UNIT="$(mktemp "$UNIT_DIR/.lp-dashboard-unit.XXXXXX")"
        sed \
            -e "s|@PROJECT_DIR@|$PROJECT_DIR|g" \
            -e "s|@SERVICE_USER@|$SERVICE_USER|g" \
            "$source" > "$TEMP_UNIT"
        install -o root -g root -m 0644 "$TEMP_UNIT" "$destination"
        rm -- "$TEMP_UNIT"
        TEMP_UNIT=""
    else
        install -o root -g root -m 0644 "$source" "$destination"
    fi
}

install_unit lp-dashboard-web.service
systemctl daemon-reload

systemctl enable --now lp-dashboard-web.service

if [[ "$INSTALL_MODE" == "dashboard-only" ]]; then
    echo "Installed and enabled the LP Dashboard web service for $SERVICE_USER."
    exit 0
fi

for unit in "${NOTIFY_UNITS[@]}"; do
    install_unit "$unit.service"
    install_unit "$unit.timer"
done

install_unit lp-dashboard-positions.service
install_unit lp-dashboard-positions.timer

systemctl daemon-reload
for unit in "${NOTIFY_UNITS[@]}"; do
    systemctl enable --now "$unit.timer"
done

systemctl enable --now lp-dashboard-positions.timer

echo "Installed and enabled ${#NOTIFY_UNITS[@]} notify timers for $SERVICE_USER."
