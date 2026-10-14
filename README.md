# OKX Sweep/BOS-Scanner mit Pushover

Ein **lokaler Signal-Scanner** für liquide **OKX USDT-Perpetuals**. Nutzt die öffentlichen OKX-Kerzendaten (`5m`) und sendet **Pushover**-Benachrichtigungen für Liquidity Sweeps und einen nachfolgenden einfachen Strukturbruch (BOS-Vorfilter). **Kein OKX-API-Schlüssel nötig, keine Orderausführung, keine TradingView-Kosten.**

**Wichtig:** Die Python-Software und die OKX-Daten sind kostenlos. **Pushover ist nach 30 Testtagen nicht dauerhaft kostenlos**: regulär einmalig 4,99 USD pro Geräteplattform (iOS oder Android). Individuelle Pushover-Nutzer können bis zu 10.000 Push-Nachrichten im Monat ohne zusätzliche API-Gebühren senden.

## Einrichtung (Windows 10/11)

1. Python 3.10 oder neuer von [python.org](https://www.python.org/downloads/) installieren. Beim Installieren **„Add Python to PATH“** aktivieren.
2. Pushover-App auf Android/iPhone installieren und ein Konto bei [pushover.net](https://pushover.net/) anlegen. Die **User Key** steht im eingeloggten Dashboard.
3. Unter [pushover.net/apps/build](https://pushover.net/apps/build) eine neue *Application/API Token* namens beispielsweise `OKX SMC Scanner` anlegen. Anschließend deren **API Token/Key** kopieren.
4. Datei `config.example.json` in `config.json` umbenennen beziehungsweise eine Kopie anlegen; in `config.json` `app_token` und `user_key` eintragen (Anführungszeichen beibehalten!). **Nicht in Chats oder öffentlich teilen.**
5. Im Ordner eine PowerShell öffnen (Shift + Rechtsklick → *Im Terminal öffnen*). Diese Befehle ausführen:

   ```powershell
   py -3 scanner.py --check
   py -3 scanner.py --test-push
   py -3 scanner.py
   ```

   Alternativ kann `start_windows.bat` per Doppelklick gestartet werden. Wenn `py` nicht erkannt wird, stattdessen `python scanner.py` verwenden.

6. PC eingeschaltet, Internet aktiv und Terminal offen lassen. Nach dem ersten Durchlauf beginnen neue abgeschlossene 5m-Kerzen das Alarmsystem zu füttern. **Erster Start verschickt absichtlich keine alten Signale.** Bei aktivem Alarm erscheinen eine Push-Nachricht auf dem Handy und ein Eintrag in `signals.csv`.

Die Ausgabe von `--check` und jede Watchlist-Aktualisierung zeigt alle ausgewählten liquiden Coins mit 5-Minuten-Umsatz, Spread und direkter OKX-URL. In Windows Terminal kannst du den Link mit **Strg + Klick** öffnen.

## Auswahl nach aktueller Handelsaktivität

Die Rangfolge basiert auf dem **USDT-Umsatz der letzten fünf abgeschlossenen 1-Minuten-Kerzen**, also einem rollierenden Fenster, das jede Minute weiterwandert. Für alle Coins wird dasselbe Zeitfenster verwendet; die laufende Minute zählt nicht mit. Der Umsatz wird direkt aus `volCcyQuote` summiert. Fehlende, unbestätigte oder veraltete Kerzen führen zum Ausschluss aus der aktuellen Rangfolge. Das Dashboard zeigt das Ende des Messfensters und fehlgeschlagene Umsatz-Abfragen.

Standardmäßig werden **bis zu 400** live handelbare OKX-USDT-Swaps ausgewählt, nach 5-Minuten-Umsatz sortiert, mit maximal 0,15 % Spread. Es gibt keine 24h-Mindestumsatzhürde mehr (`min_24h_usdt: 0`); ein optionaler `min_5m_usdt`-Filter steht auf 0. Coins ohne Umsatz in diesem Fenster werden ausgelassen. Sind weniger als 400 geeignete Paare verfügbar, bleibt die Watchlist kleiner.

Die Rangfolge wird einmal pro Minute neu berechnet, soweit API-Antwortzeiten und Durchlaufdauer dies zulassen. Pro geeignetem Coin wird dafür eine kleine Abfrage mit acht 1m-Kerzen benötigt. Die Signalhistorie wird beim ersten Beobachten mit bis zu 150 Kerzen geladen, anschließend zwischengespeichert und nur nach Kerzenschluss ergänzt. Vier parallele Abrufe teilen sich eine Drossel auf etwa acht neue API-Anfragen pro Sekunde; Dateizugriffe und Signalverarbeitung bleiben sequenziell.

400 Coins bedeuten bei 400 geeigneten Kandidaten ungefähr 400 Umsatz-Abfragen pro Minute, zusätzlich zu Signalabfragen. Das ist kein vernachlässigbarer Dauerbetrieb: allein die Umsatzmessung entspricht bei durchgehend minutengenauer Aktualisierung rund 576.000 Abfragen pro Tag. Die empfangenen JSON-Bytes und Abfrageanzahl seit Start stehen im Dashboard; HTTP/TLS-Overhead zählt nicht mit. Der echte Verbrauch hängt von Anzahl, Antwortgröße und Laufzeit ab. Weniger Traffic: `max_pairs` reduziert nur die Signal-Watchlist; für weniger Umsatz-Abfragen den Spread-/Mindestumsatzfilter verschärfen oder `refresh_pairs_minutes` erhöhen.

## Browser-Dashboard und Benachrichtigungen

Scanner wie gewohnt starten und [Dashboard öffnen](http://127.0.0.1:8765). Die lokale Übersicht zeigt alle ausgewählten Coins mit Chartlinks, Suche, die letzten 100 erkannten Signale, den letzten Scan, wartende Signale und Fehler. Sie aktualisiert sich alle fünf Sekunden und ist nur auf diesem PC erreichbar. Pushover-Schlüssel werden nicht an den Browser übertragen.

Neue Signale werden standardmäßig alle **120 Sekunden** zusammengefasst. Jede Signalzeile enthält einen OKX-Link. Große Zusammenfassungen werden in mehrere Nachrichten aufgeteilt. Wartende Signale bleiben in `state.json` bis zum erfolgreichen Versand gespeichert. `signals.csv` protokolliert jetzt erkannte Signale, auch wenn deren Push noch aussteht. `--once` versendet die wartende Zusammenfassung am Ende des Durchlaufs.

Nach **180 Sekunden** ohne erfolgreiche Verarbeitung eines Coins versucht der Scanner eine Störungsmeldung zu senden. Nach erneuter erfolgreicher Verarbeitung folgt eine Entwarnung. Eine Meldung kann nur zugestellt werden, wenn Pushover erreichbar ist und der Scanner noch läuft; bei beendetem Programm zeigt das geöffnete Dashboard „Scanner nicht erreichbar“. Einzelne API- oder Push-Fehler erscheinen zusätzlich in der Übersicht.

Die folgenden optionalen Blöcke können in `config.json` ergänzt werden; bestehende Konfigurationen verwenden diese Standardwerte automatisch:

```json
"dashboard": {"enabled": true, "port": 8765},
"notifications": {"summary_seconds": 120, "outage_seconds": 180}
```

`summary_seconds: 0` aktiviert wieder einzelne Signal-Pushes. `dashboard.enabled: false` deaktiviert den Browser-Server. Änderungen werden nach einem Neustart übernommen.

## Einrichtung (macOS/Linux)

Python 3.10+ installieren, Pushover-App/User Key/App Token wie oben einrichten, `config.example.json` nach `config.json` kopieren und dann im Verzeichnis:

```bash
python3 scanner.py --check
python3 scanner.py --test-push
python3 scanner.py
```

Kein `pip install` nötig; es werden ausschließlich Python-Standardbibliotheken verwendet.

## Was wird tatsächlich erkannt?

- **Bullish Sweep:** Eine abgeschlossene 5m-Kerze sticht unter ein **bereits bestätigtes Swing-Tief** und schließt darüber. Ein zuvor bestätigtes Gegenswing-Hoch muss vorhanden sein.
- **Bearish Sweep:** Spiegelbildlich über ein bestätigtes Swing-Hoch, Schlusskurs darunter.
- **Bullish BOS-Vorfilter:** Innerhalb von acht 5m-Kerzen nach dem Bull-Sweep schließt eine spätere Kerze oberhalb des beim Sweep bekannten Swing-Hochs. Eine Schließung unter dem Sweep-Extrem invalidiert den Kandidaten.
- **Bearish BOS-Vorfilter:** Spiegelbildlich unter das beim Sweep bekannte Swing-Tief.

Die Pivot-Bestätigung erfolgt mit `pivot_left=3`, `pivot_right=3`. Ein Level wird erst nach drei weiteren Kerzen als Pivot erkannt; der Scanner benutzt **kein zukünftiges Wissen beim Alarmsignal**. Der BOS-Vorfilter ist **kein vollständiger ICT-MSS/CHoCH-Algorithmus** und schließt FVG, Displacement, Orderflow sowie höhere Timeframes nicht automatisch ein. Ein Push ist **kein Kauf- oder Verkaufssignal**.

## Filter, Kosten, technische Grenzen

- Standard: **bis zu 400** USDT-Perpetual-Paare (`-USDT-SWAP`) von OKX, nach gemessenem USDT-Umsatz der letzten fünf abgeschlossenen Minuten sortiert, mit maximal **0,15 % Spread** (Momentaufnahme). Für den optionalen 24h-Vorfilter wird der Umsatz weiterhin aus `volCcy24h * last` geschätzt; standardmäßig ist dieser Vorfilter deaktiviert.
- API-Domain: Für OKX-Konten in der EU standardmäßig `https://eea.okx.com`. Für globale OKX-Registrierungen gegebenenfalls auf `https://www.okx.com`, USA/Australien `https://us.okx.com` ändern.
- Der Scan wird etwa alle **30 Sekunden** neu ausgeführt und verwendet ausschließlich von OKX als **abgeschlossen** (`confirm=1`) bestätigte Kerzen. Ein 5m-Signal kommt also **nach dem Kerzenschluss**, nicht beim Wick in Echtzeit.
- Keine Benachrichtigungen für Signale, deren Kerzenschluss mehr als sieben Minuten zurückliegt; so bleiben alte Treffer nach Internetausfällen stumm. Erfolgreiche Benachrichtigungen werden dedupliziert und in `state.json` gespeichert.
- Unvorhersehbare Ausfälle (PC-Schlafmodus, Netzprobleme, OKX/Pushover-Störungen) können Alarme verzögern oder verpassen; es gibt keine garantierte Zustellung. Keine automatischen Trades.
- `config.json` enthält geheime Pushover-Schlüssel. **Nicht veröffentlichen.** Die Dateien `state.json` und `signals.csv` entstehen beim Betrieb im selben Ordner.

## Nützliche Einstellungen in config.json

| Einstellung | Wirkung |
|---|---|
| `selection.max_pairs` | Max. Anzahl beobachteter USDT-SWAP-Paare (Standard: 400) |
| `selection.min_24h_usdt` | Optionaler 24h-Vorfilter, Standard 0 = deaktiviert |
| `selection.min_5m_usdt` | Mindestumsatz der letzten fünf abgeschlossenen Minuten (Standard: 0) |
| `runtime.refresh_pairs_minutes` | Abstand der Umsatzmessungen in Minuten (Standard: 1) |
| `selection.max_spread_pct` | Max. Bid/Ask-Spread in Prozent |
| `strategy.timeframe` | `1m`, `3m`, `5m` oder `15m` (Start: `5m`) |
| `strategy.pivot_left` / `pivot_right` | Empfindlichkeit der Swings, je höher desto weniger Signale |
| `strategy.bos_window_bars` | Zeitfenster nach Sweep für BOS (Anzahl Kerzen) |
| `strategy.min_sweep_depth_pct` | Mindestdurchstich hinter das Sweep-Level in Prozent |
| `strategy.notify_sweeps` | `false` für nur BOS-Pushes (weniger Benachrichtigungen) |
| `strategy.notify_bos` | `false` für nur Sweep-Pushes |

### Push-Beispiel (fiktiv)

```text
OKX 5m | LONG BOS nach Sweep | SOL
SOL-USDT-SWAP
Signal: BULL BOS
Kerzenschluss: 150.12
Gesweeptes Level: 148.8
BOS-Level: 150.0
Kurskerze: 2026-10-14 12:35 UTC
Nur Setup-Kandidat; 15m-Kontext, 1m-Entry und Risiko pruefen.
```

Öffne den enthaltenen OKX-Chartlink, prüfe die 15m-Zone, das Preisverhalten auf 1m/3m, Stop, Gebühren, Funding und Slippage. **Vor Echtgeld-Nutzung zunächst Paper-Trading und Auswertung der Signale** (`signals.csv`).

## Problembehandlung

- **`--check` findet 0 Paare:** `min_24h_usdt` und `min_5m_usdt` prüfen (Standard jeweils 0), `max_spread_pct` prüfen oder `okx_api_base` der Kontoregion anpassen. Umsatzdaten müssen für alle fünf abgeschlossenen Minuten vorhanden sein.
- **HTTP 403 / 451 / DNS:** Regionale API-Domain und Internetzugang prüfen. API-Abfragen sind kein Umgehen regionaler Produktbeschränkungen.
- **Pushover-Test scheitert:** App-Token (`app_token`) und User Key (`user_key`) unterscheiden; beide im Dashboard kontrollieren, App auf dem Handy anmelden.
- **Keine Pushes trotz funktionierendem Test:** Es gab noch kein passendes neues 5m-Signal; der erste Lauf unterdrückt historische Signale. Wenn du nur bestätigte BOS bekommen willst, `notify_sweeps=false` setzen.
- **PC geht schlafen:** Energiesparmodus deaktivieren, solange gescannt werden soll.
- **Neustart nach längerer Pause:** Gespeicherte `state.json` verhindert normalerweise Dopplungen; ältere Signale werden absichtlich nicht nachträglich zugestellt.

API-Referenzen: [OKX V5 Dokumentation](https://www.okx.com/docs-v5/en/), [Pushover Nachrichten-API](https://pushover.net/api), [Pushover Preise](https://pushover.net/pricing).
