# Docker und CapRover

Scanner und UI laufen gemeinsam in einem Image auf Port **8765**. Das Image
verwendet Python 3.13, einen Benutzer ohne Root-Rechte und einen HTTP-Healthcheck.
Lokale `config.json`, Zugangsdaten, Git-Dateien und historische Signale werden
dank expliziter COPY-Liste und `.dockerignore` nicht in das Image übernommen.
Die öffentliche `config.example.json` liefert die Standardwerte.

## 1. Docker Hub und GitHub konfigurieren

Erstelle auf Docker Hub ein öffentliches Repository, beispielsweise
`DEINNAME/okx-futures-finder` (DEINNAME durch deinen tatsächlichen, kleingeschriebenen
Docker-Hub-Namespace ersetzen).

Im GitHub-Repository unter **Settings → Secrets and variables → Actions**:

| Typ | Name | Wert |
|---|---|---|
| Secret | `DOCKERHUB_USERNAME` | Docker-Hub-Benutzername |
| Secret | `DOCKERHUB_TOKEN` | Docker-Hub Access Token mit Schreibrecht für das Repository |
| Variable | `DOCKERHUB_IMAGE` | `deinname/okx-futures-finder`, ohne Tag |

Der Workflow `.github/workflows/docker-build-and-push.yml` orientiert sich am
[verlinkten Beispiel](https://github.com/code-by-fh/ai-job-analyzer/blob/main/.github/workflows/docker-build-and-push.yml).
Er prüft Python-Tests und JavaScript-Syntax, baut ein Testimage und prüft dessen
UI, Status-API und Healthcheck auch ohne Internetzugriff. Erst danach werden
Images für **linux/amd64 und linux/arm64** veröffentlicht.

Alle benötigten neuen Anwendungsdateien zusammen mit den Docker-Dateien committen
und ins eigene GitHub-Repository pushen. Veröffentlichungen entstehen so:

| Auslöser | Docker-Tags |
|---|---|
| Push auf `main` | `latest` und `sha-<erste 12 Commit-Zeichen>` |
| Git-Tag, etwa `v1.0.0` | `v1.0.0` und `sha-<erste 12 Commit-Zeichen>` |
| Manuell über Actions → Run workflow | Auf `main`: `latest`; auf anderen Branches: SHA-Tag |
| Pull Request auf `main` | Tests, keine Veröffentlichung |

Git-Tags müssen gültige Docker-Tags sein, beispielsweise `v1.0.0` ohne `/`.
Der Workflow veröffentlicht auf Docker Hub; das CapRover-Deployment erfolgt
anschließend separat. Für nachvollziehbare Releases einen Versions- oder SHA-Tag
in CapRover verwenden.

## 2. App in CapRover anlegen

1. Unter **Apps** eine App wie `okx-finder` anlegen, ohne persistente Daten.
2. Unter **HTTP Settings → Container HTTP Port** den Wert **8765** eintragen und speichern.
3. Unter **App Config** die **Instance Count auf 1** lassen: Jede Instanz würde
   unabhängig scannen und zusätzliche OKX-Anfragen erzeugen.
4. Keine Host-Port-Zuordnung und keine persistenten Verzeichnisse hinzufügen.
   CapRovers Proxy erreicht die App über den Container-Port.

Die Optionen sind in der [CapRover App Configuration](https://caprover.com/docs/app-configuration.html)
beschrieben. Der Finder speichert seine Ergebnisse und Kerzen im Arbeitsspeicher;
nach einem Neustart baut er den Cache neu auf.

## 3. Laufzeitkonfiguration

Die Standardwerte funktionieren ohne zusätzliche Variablen. Optional unter
**App Config → Environment Variables** setzen und speichern:

```text
OKX_MARKET=eea_xperp
OKX_API_BASE=https://eea.okx.com
MAX_PAIRS=400
MAX_SPREAD_PCT=0.15
REFRESH_MINUTES=1
```

Die Tabelle enthält alle unterstützten Overrides. Umgebungsvariablen haben
Vorrang vor der Konfigurationsdatei; sie werden beim Prozessstart ausgewertet.

| Variable | Standard im Image | Bedeutung |
|---|---|---|
| `OKX_MARKET` | `eea_xperp` | EEA-Krypto-XPerps; alternativ `usdt_swap` |
| `OKX_API_BASE` | `https://eea.okx.com` | HTTPS-API-Endpunkt; für globale Swaps ggf. `https://www.okx.com` |
| `MAX_PAIRS` | `400` | Maximale Anzahl pro Ansicht, ganze Zahl ab 1 |
| `MAX_SPREAD_PCT` | `0.15` | Maximaler Spread in Prozent |
| `MIN_5M_TURNOVER` | `0` | Mindestumsatz der letzten 5 Minuten in USD bzw. USDT |
| `MIN_24H_TURNOVER` | `0` | Optionaler näherungsweiser 24h-Umsatz-Vorfilter |
| `REFRESH_MINUTES` | `1` | Pause zwischen Messungen, mindestens 1 Minute |
| `DASHBOARD_HOST` | `0.0.0.0` | Bindung für den Containerzugriff |
| `PORT` | `8765` | HTTP-Port; bei Änderung auch Container HTTP Port in CapRover anpassen |
| `OKX_CONFIG_FILE` | `/app/config.json` | Pfad zur Konfigurationsdatei im Container |

Keine OKX-API-Schlüssel oder Pushover-Zugangsdaten erforderlich. Die App zeigt
weiterhin ausschließlich Krypto-Futures; ein Container-Deployment aktiviert
keine alten Signalstrategien.

## 4. Veröffentlichtes Image deployen

Nach einem erfolgreichen GitHub-Workflow in der CapRover-App den Tab **Deployment**
öffnen. Im Bereich für die Captain Definition folgenden Inhalt einfügen und deployen:

```json
{
  "schemaVersion": 2,
  "imageName": "deinname/okx-futures-finder:latest"
}
```

`deinname/okx-futures-finder` muss genau `DOCKERHUB_IMAGE` entsprechen. Optional
`latest` durch den veröffentlichten Versions- oder SHA-Tag ersetzen. CapRover
unterstützt diese [Image-only Definition offiziell](https://caprover.com/docs/captain-definition-file.html).
Bei einem privaten Image benötigt CapRover zusätzlich Zugriff auf dessen Registry;
die Anleitung oben verwendet deshalb ein öffentlich abrufbares Image.

Anschließend unter **HTTP Settings** die App-Domain öffnen, **Enable HTTPS** und
**Enforce HTTPS** aktivieren. Nach dem ersten Scan erscheinen die Handelspaare.
Der erste Abruf benötigt mehr Zeit als die folgenden Cache-Aktualisierungen.

Bei Updates einen neuen Tag deployen oder das aktualisierte `latest` erneut
deployen. Ein Docker-Hub-Push allein aktualisiert die laufende CapRover-App nicht.

Die mitgelieferte Datei `captain-definition` verweist dagegen auf das Dockerfile
und ermöglicht alternativ einen Build direkt aus dem Quellcode in CapRover.
Für das oben beschriebene Image-Deployment die Image-only Definition verwenden.

## 5. Lokal prüfen und Fehler eingrenzen

```powershell
docker build -t okx-futures-finder:local .
docker run --rm --name okx-finder -p 127.0.0.1:8876:8765 okx-futures-finder:local
```

UI: http://127.0.0.1:8876, Healthcheck: http://127.0.0.1:8876/healthz.
Mit Strg+C beenden. Auf Systemen mit Bash kann zusätzlich
`bash scripts/container-smoke.sh okx-futures-finder:local` ausgeführt werden.

- **502 in CapRover:** Container HTTP Port muss zu `PORT` passen, standardmäßig
  8765; `DASHBOARD_HOST` muss im Container `0.0.0.0` sein. App-Logs prüfen.
- **Image nicht gefunden:** Workflow muss erfolgreich sein; Namespace, Repository
  und Tag müssen genau stimmen und vom CapRover-Server abrufbar sein.
- **UI erreichbar, Liste leer:** Messfortschritt und Fehler im UI sowie App-Logs
  prüfen. Ausgehende HTTPS-Verbindungen zum gewählten OKX-Endpunkt sind erforderlich.

`/healthz` prüft die Erreichbarkeit des HTTP-Prozesses, nicht die Verfügbarkeit von
OKX oder die Aktualität der Kurse. So startet eine OKX-Störung den Container nicht
unnötig neu; Datenalter und API-Probleme bleiben im UI sichtbar.
