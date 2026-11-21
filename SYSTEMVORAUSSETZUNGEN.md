# Systemvoraussetzungen und benötigte Zugänge

Diese Anleitung beschreibt die Voraussetzungen für das LP Dashboard, den
Positionsimport und die Notify-Skripte. Der Root-Installer `install.sh` richtet
die Python-Umgebung, MariaDB, Konfigurationsdateien und den Dashboard-Webdienst
ein. Monitoring- und Positionsimport-Timer können zusätzlich aktiviert werden.

## Systemvoraussetzungen

- **Linux mit systemd.** Der Installer unterstützt Paketmanager auf Basis von
  `apt`, `dnf`, `yum` oder `zypper`. Er ist für eine lokale MariaDB-Instanz
  ausgelegt und konfiguriert keine Remote-Datenbank.
- **Ein normaler Benutzeraccount mit `sudo`-Rechten.** `./install.sh` darf
  nicht als `root` gestartet werden. Das Script verwendet `sudo` für
  Systempakete, MariaDB und systemd.
- **Python 3.10 oder neuer.** Das Script erstellt `.venv` und installiert die
  benötigten Python-Pakete selbst.
- **MariaDB 10.10 oder neuer.** Das Backup benötigt die Kollation
  `utf8mb4_uca1400_ai_ci`. Der Installer kann MariaDB installieren und starten.
- **Internetverbindung vom Server.** Benötigt für System- und Python-Pakete,
  Blockchain-RPC-Aufrufe, Kursdaten und Telegram-Nachrichten.
- **Freier Speicherplatz** für das Projekt, die virtuelle Umgebung und den
  Datenbankimport. Ein fester Mindestwert ist nicht vorgegeben.

Der Installer importiert die leeren Tabellenstrukturen aus `backup.sql` in die
ausgewählte Datenbank; der Dump enthält keine historischen Datensätze. Wenn
die Datenbank bereits existiert, fragt das Script vor dem Restore, da
gleichnamige Tabellen ersetzt werden und deren vorhandene Daten verloren
gehen.

Das Script installiert unter anderem `uvicorn[standard]`, `fastapi`,
`python-multipart`, `jinja2`, `pandas`, `mariadb`, `python-dotenv`, `web3`,
`httpx`, `eth-abi`, `eth-utils`, `python-telegram-bot<14`, `telegram-send`,
`matplotlib`, `numpy`, `solana` und `solders`. Systemseitige Build-Werkzeuge und
MariaDB-Connector-Bibliotheken werden bei unterstützten Paketmanagern ebenfalls
installiert.

## Datenbankzugang

Für die Einrichtung benötigt der Installer administrative Rechte in MariaDB.
Er versucht zuerst den lokalen MariaDB-Administrationszugang über `sudo` und
den Unix-Socket zu verwenden. Falls das nicht möglich ist, fragt er einen
MariaDB-Administrationsbenutzer und dessen Passwort ab. Dieses Admin-Passwort
wird nicht in den Anwendungsdateien gespeichert.

Für den laufenden Betrieb braucht die Anwendung einen MariaDB-Benutzer mit
Zugriff auf die Dashboard-Datenbank:

- Datenbankname: frei wählbar, Standard `lpdashboard`
- Datenbankbenutzer: Standard `lpdashboard` (neu anlegen) oder ein vorhandener
  Benutzer
- Passwort für diesen Anwendungsbenutzer
- Host und Port: Standard `localhost:3306`

Bei einem vorhandenen Benutzer muss das eingegebene Passwort zu dessen
MariaDB-Konto passen. Der Installer erteilt dem Benutzer Rechte auf die
ausgewählte Datenbank. Die Verbindungswerte werden in die `.env`-Dateien des
Dashboards und der Notify-Skripte geschrieben.

## RPC- und API-Zugänge

Die Zugangsdaten werden in diesem Projekt für lesende API-/RPC-Aufrufe
verwendet. Für das Dashboard und die Monitoring-Skripte werden keine
Wallet-Private-Keys benötigt.

| Zugang | Verwendung | Konfigurationsname |
| --- | --- | --- |
| **Alchemy API Key** ([Alchemy](https://www.alchemy.com/)) | EVM-RPC, unter anderem für Ethereum, Arbitrum, Polygon, Base, Optimism, Robinhood Chain und BNB Chain. Wird von Dashboard-Funktionen und den entsprechenden Notify- beziehungsweise Gesamtberichten genutzt. | `ALCHEMY_API_KEY` |
| **Helius API Key** ([Helius](https://www.helius.dev/)) | Solana-Mainnet-RPC, insbesondere für Orca, Raydium, Positionsdetails und Gesamtberichte. | `HELIUS_API_KEY` |
| **CoinTracking API Key und Secret** | Liest CoinTracking-Bestände für den optionalen Portfolio-Gesamtwert. | `ct_key`, `ct_secret` |
| **Aerodrome Staker-Adresse** | Öffentliche Adresse, deren beanspruchbare Aerodrome-Rewards abgefragt werden. Sie ist keine geheime Zugangsinformation. | `AERO_STAKER_ADDRESS` |

Für die konfigurierten EVM-Netzwerke wird derselbe Alchemy-Key verwendet. Der
Helius-Key wird für Solana verwendet. Manche Dashboard- und Importfunktionen
akzeptieren alternativ eine manuell konfigurierte `SOLANA_RPC_URL`; der
Installer fragt stattdessen den Helius-Key ab und schreibt ihn in die
vorgesehenen Konfigurationsdateien.

CoinTracking-Zugangsdaten und die Aerodrome-Staker-Adresse sind optional. Ohne
CoinTracking werden keine CoinTracking-Bestände in die Portfolio-Gesamtsumme
einbezogen; ohne Staker-Adresse entfallen die betreffenden Aerodrome-Rewards.
Kursdaten von Chainlink, DexScreener, Pyth oder Jupiter benötigen in der
aktuellen Konfiguration keine zusätzlichen Zugangsdaten.

Der Webdienst kann zwar auch ohne API-Keys starten, aber Live-Details und
betroffene Berichte benötigen die jeweiligen RPC-Zugänge. Für die Aktivierung
der Monitoring- und Positionsimport-Timer verlangt der Installer alle als
erforderlich markierten Werte der mitgelieferten Konfigurationen, darunter
beide RPC-Keys und die Telegram-Angaben. CoinTracking und die
Aerodrome-Staker-Adresse blockieren die Timer nicht.

## Telegram

Für Telegram-Berichte werden ein **Bot-Token** und die **Chat-ID des
Ziel-Chats** benötigt. Den Bot und sein Token richtest du mit BotFather ein:
[Telegram: BotFather](https://core.telegram.org/bots/features#botfather).
Damit der Bot Nachrichten an einen privaten Chat senden kann, muss der Chat
den Bot zuerst starten. Für eine Gruppe muss der Bot Mitglied der Gruppe sein.

Der Installer fragt folgende Werte ab:

- `token`: Bot-Token von BotFather
- `chat_id`: ID des privaten Chats oder der Gruppe, an die Berichte gesendet
  werden sollen

Ein gemeinsamer Bot und Ziel-Chat werden in die Root-Konfiguration und in die
`telegram.conf`-Dateien der Notify-Verzeichnisse übernommen. Die
Root-Konfiguration wird auch für Meldungen des Positionsimports verwendet.
Ein Bot-Token ist ein Geheimnis und darf nicht veröffentlicht werden.

## Konfigurationsdateien und Positionslisten

Der Installer erstellt private `.env`- und `telegram.conf`-Dateien aus den
jeweiligen Beispielen. Dazu gehören die Root-Konfiguration sowie die
Konfigurationen unter `notify/`. Die erstellten Dateien erhalten restriktive
Dateirechte und gehören nicht in Git oder öffentliche Backups.

Für geplante Notify-Läufe müssen außerdem die passenden `positions`-Dateien
mit den zu überwachenden Positions-IDs beziehungsweise -Adressen befüllt sein.
Die Zugangsdaten allein bestimmen nicht, welche Positionen überwacht werden.
Fehlende oder übersprungene Credentials listet `install.sh` am Ende mit dem
Pfad der betroffenen Datei auf. Erst wenn die erforderlichen Werte vollständig
sind, können die optional gewählten Monitoring-Timer aktiviert werden. Das
betrifft auch Werte für Skripte oder Netzwerke, deren Timer du eventuell nicht
verwenden möchtest.

## Optional: Zugriff über Apache

Der Dashboard-Webdienst bindet standardmäßig ausschließlich an
`127.0.0.1:8000`. Für Zugriff von anderen Rechnern kann Apache als
Reverse-Proxy mit HTTPS und Basic Authentication eingerichtet werden. Dafür
benötigst du zusätzlich:

- einen DNS-Namen, der auf den Server zeigt
- ein gültiges TLS-Zertifikat und den zugehörigen privaten Schlüssel
- Apache mit den Modulen `ssl`, `rewrite`, `proxy`, `proxy_http` und `headers`
- eine mit `htpasswd` angelegte Passwortdatei

Eine anonymisierte Beispielkonfiguration und die Aktivierungsschritte stehen
in [apache-lp-dashboard.conf](apache-lp-dashboard.conf) und im Abschnitt
[Apache Reverse Proxy der README](README.md#apache-reverse-proxy).
