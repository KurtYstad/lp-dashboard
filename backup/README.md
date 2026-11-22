# Datenbank-Backup

`backup.sh` erstellt einen vollständigen Dump der in `DB_NAME` angegebenen
MariaDB-Datenbank und lädt ihn auf den FTP-Server hoch. Die lokale Datei bleibt
im Unterordner `backup/archive` liegen und erhält einen Zeitstempel im Format
`YYYY-MM-DD_HH-MM-SS_backup.sql`.

## Konfiguration

Lege `backup/.env` mit den Einstellungen aus [`.env.example`](.env.example)
an und trage dort die Datenbank- und FTP-Zugangsdaten ein. Die Datei enthält
Zugangsdaten und darf nicht ins Repository übernommen werden. `DB_NAME` ist
der Datenbankname; `FTP_REMOTE_DIR` ist optional und standardmäßig leer.

Mit `FTP_TLS=1` wird explizites FTPS auf dem angegebenen FTP-Port erzwungen.
Bei `FTP_TLS=0` werden FTP-Zugangsdaten und Backup-Inhalt unverschlüsselt
übertragen. Verwende nach Möglichkeit FTPS.

Benötigt werden `mariadb-dump` (MariaDB-Clientpaket) und `curl`. Das Script
lädt auch bei einem fehlgeschlagenen Upload die bereits erstellte lokale
Sicherung nicht wieder herunter oder löscht sie; der Fehler wird im Exit-Code
gemeldet und ist im systemd-Journal sichtbar.

## Manuell ausführen

```sh
chmod 755 backup/backup.sh
./backup/backup.sh
```

## Tägliche Ausführung mit systemd

Die Units `systemd/lp-dashboard-backup.service` und
`systemd/lp-dashboard-backup.timer` führen das Backup täglich um 23:59 Uhr
aus. Für die Installation zusammen mit den übrigen systemd-Diensten:

```sh
sudo ./install-systemd.sh
```

Der Timer wird dabei aktiviert. Nach der Installation kann der Lauf manuell
gestartet und geprüft werden:

```sh
sudo systemctl start lp-dashboard-backup.service
systemctl list-timers lp-dashboard-backup.timer
sudo journalctl -u lp-dashboard-backup.service
```

Die Uhrzeit richtet sich nach der lokalen Zeitzone des Servers.
