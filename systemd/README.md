# LP dashboard systemd services

For a complete interactive installation, including the MariaDB restore,
Python virtual environment, credentials, and timer setup, use
[`install.sh`](../install.sh) from the project root. This document describes
the systemd-specific installer and service behavior. The installer always
enables the dashboard web service. A full installation also enables the
periodic monitoring, positions-import, and database-backup timers.

The dashboard service runs Uvicorn on `127.0.0.1:8000` and restarts it if it
exits. It is reachable locally on the server. It is not exposed directly to
the network. For remote access, an example Apache reverse-proxy configuration
with HTTPS and Basic Authentication is provided in
[`apache-lp-dashboard.conf`](../apache-lp-dashboard.conf); setup instructions
are in the root [README](../README.md#apache-reverse-proxy). The positions
timer runs `positions.py` every five minutes. Completed positions are skipped;
pending or incomplete positions are retried on later runs. Each run checks all
configured source tables.

## Requirements

- The project is deployed on Linux with systemd. The installer uses the
  checkout directory as the project directory. Its absolute path must contain
  only letters, digits, `/`, `.`, `_`, and `-`.
- The service user owns or can read the project and its configuration files.
- The virtual environment at `<project-dir>/.venv` has the application's
  dependencies installed, including the `uvicorn` executable.
- The root and per-script `.env` files contain database credentials and
  required RPC/API configuration. Keep them private; credentials are
  deliberately not copied into the systemd units.
- `backup/.env` contains the database and FTP settings for the daily backup.
  The backup service requires `mariadb-dump` and `curl`; its local SQL files
  are written to `backup/` with private permissions.
- The dashboard's on-demand portfolio summary also needs CoinTracking API
  credentials named `ct_key` and `ct_secret` in `.env`. It reads balances only
  when the summary is opened and caches them for one hour.
- Trust Wallet logos are cached only for coins present in the portfolio, rather
  than copying the full asset repository; Ethereum symbols take priority.
- Each `telegram.conf` uses the `telegram-send` INI format with a `[telegram]`
  section. Keep these files private; secret configuration files are excluded
  from Git.
- The service environment has the `telegram-send` package installed:

  ```sh
  PROJECT_DIR="$(pwd -P)"
  "$PROJECT_DIR/.venv/bin/python" -m pip install telegram-send
  ```

- The importer sends a Telegram message after a new successful import, when an
  existing incomplete row is successfully updated, and whenever an import
  fails. Telegram delivery failures are logged and do not roll back database
  writes.

## Install systemd services and timers

The project-root `install-systemd.sh` renders the unit templates using the
current project path and service user, installs and starts
`lp-dashboard-web.service`, installs the Notify, positions, and database
backup services and timers, and enables the timers. Run it from the deployed checkout:

```sh
sudo ./install-systemd.sh
```

For a full installation, missing per-script `positions` files are created
empty before the timers start. Existing files are left untouched; add position
IDs to these files or manage them through the dashboard.

To install only the dashboard service (without monitoring or positions-import
timers), use:

```sh
sudo ./install-systemd.sh --dashboard-only
```

When invoking it from a root shell without `SUDO_USER`, specify the account
explicitly:

```sh
LP_DASHBOARD_USER=your_linux_user ./install-systemd.sh
```

Replace `your_linux_user` with the account that owns or can read the project.

The installer expects `.venv/bin/python` and `.venv/bin/uvicorn` to exist.
Configure the per-script `.env`, `backup/.env`, `telegram.conf`, and
`positions` files before the timers run. Service output is sent to journald
rather than written to cron-style log files. The positions and database
backup services and timers are installed and enabled along with the Notify
timers when using the full installer.

Optionally check the database/API setup without writing position rows:

```sh
PROJECT_DIR="$(pwd -P)"
"$PROJECT_DIR/.venv/bin/python" "$PROJECT_DIR/positions.py" --dry-run --limit 1
```

Verify Telegram delivery separately:

```sh
PROJECT_DIR="$(pwd -P)"
cd "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" -c 'from positions import _send_telegram_notification; _send_telegram_notification("LP positions Telegram test")'
```

To run one positions import immediately:

```sh
sudo systemctl start lp-dashboard-positions.service
```

After confirming the Notify timers work, remove the corresponding entries
from the user's crontab to avoid duplicate runs.

## Check status and logs

```sh
systemctl status lp-dashboard-web.service
sudo journalctl -u lp-dashboard-web.service --since today
systemctl status lp-dashboard-positions.timer
systemctl list-timers lp-dashboard-positions.timer
sudo journalctl -u lp-dashboard-positions.service --since today
systemctl status lp-dashboard-backup.timer
systemctl list-timers lp-dashboard-backup.timer
sudo journalctl -u lp-dashboard-backup.service --since today
systemctl list-timers 'lp-dashboard-notify-*'
sudo journalctl -u lp-dashboard-notify-uniswap-arbitrum.service --since today
```

The service exits unsuccessfully if any position in a run could not be
imported; those rows remain incomplete and are retried on the next timer run.
The timer continues to trigger future runs.

To disable the periodic checks:

```sh
sudo systemctl disable --now lp-dashboard-positions.timer
```

To disable the daily database backup:

```sh
sudo systemctl disable --now lp-dashboard-backup.timer
```

To stop the dashboard service:

```sh
sudo systemctl disable --now lp-dashboard-web.service
```
