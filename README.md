# OKX Futures-Finder

Eine einfache Suchhilfe für volatile Krypto-Futures: **eine Liste, zwei Ansichten
für 1m- und 5m-Charts**. Keine Handelssignale, Push-Nachrichten oder Orderausführung.

## Start

Python 3.10+ installieren, bei Bedarf `config.example.json` nach `config.json`
kopieren, dann `start_windows.bat` doppelklicken. Alternativ:

```powershell
py -3 scanner.py
```

UI: **http://127.0.0.1:8765**. Auf macOS/Linux: `python3 scanner.py`.
Keine zusätzlichen Python-Pakete und keine API-Schlüssel erforderlich.
Nach Updates den Finder neu starten und die Browserseite neu laden.

Docker-Image, GitHub-Actions-Build und Deployment in CapRover:
siehe [Docker- und CapRover-Anleitung](CAPROVER.md).

## Die zwei Ansichten

| Ansicht | Rangfolge | Kurzfristige Aktivität |
|---|---|---|
| 1m | Wilder ATR(14) des 1m-Charts in % des letzten Schlusskurses | Mittlere True Range der letzten 5 Minuten / ATR |
| 5m | Wilder ATR(14) des 5m-Charts in % des letzten Schlusskurses | Mittlere True Range der letzten 15 Minuten / ATR |

Die nominelle Periodenlänge beträgt damit rund **14 bzw. 70 Minuten**.
ATR verwendet rekursive Wilder-Glättung: ältere Kerzen wirken mit abnehmendem
Gewicht weiter; es ist kein hart abgeschnittenes 14-/70-Minuten-Fenster.
Zum Aufwärmen werden beim ersten Abruf bis zu 300 abgeschlossene Minutenkerzen
geladen. Unterschiede zu einem Chart mit längerer Historie sind deshalb möglich.

True Range ist das Maximum aus Hoch−Tief, |Hoch−voriger Schlusskurs| und
|Tief−voriger Schlusskurs|. Der erste ATR ist das Mittel von 14 True Ranges;
danach gilt `ATR = (voriger ATR × 13 + neue True Range) / 14`.
Für den Vergleich verschieden teurer Coins wird ATR durch den Schlusskurs geteilt.
Grundlage: [Fidelity ATR](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atr)
und [ATRP](https://www.fidelity.com/learning-center/trading-investing/technical-analysis/technical-indicator-guide/atrp).

**Aktivität 1,5x** bedeutet: Die jüngste mittlere True Range liegt 50 % über dem
aktuellen ATR desselben Zeitrahmens. Das ist ein beschreibender Vergleich, keine
Vorhersage. ATR beschreibt Bewegung ohne Handelsrichtung. Die gewählten Profile
sind nachvollziehbare Startwerte, nicht auf Gewinne optimierte Einstellungen.

## Richtiger Markt und richtige Umsätze

Standard ist **`market: "eea_xperp"`** für den EEA-Markt:

- `instType=FUTURES`, `ruleType=xperp`, `instCategory=1`, linear und USD-abgerechnet.
- Nur Krypto; Aktien, Rohstoffe, Forex, Anleihen und unbekannte Kategorien bleiben draußen.
- Keine Vermischung mit globalen USDT-Swaps, normalen Terminfutures oder Pre-Market-Produkten.
- Vollständige API-Kontrakt-ID und Währung stehen in der Liste. Der Chartlink
  verwendet den OKX-Webalias derselben XPerp-Familie, beispielsweise
  `btc-usd_um_xperp-5yearly`. Das API-Ablaufdatum (`-310404`) ist kein gültiger
  Web-Routenalias und kann zu einem anderen Futures-Markt umgeleitet werden.
  Die Datenabfragen verwenden weiterhin die vollständige API-Instrument-ID.
  Es wird kein gleichnamiger Swap als Ersatz verwendet.

Wer ausdrücklich globale Krypto-USDT-Perpetuals sucht, kann in `config.json`
`"market": "usdt_swap"` setzen. Der Finder zeigt dann USDT statt USD an.
Ein Marktwechsel erfordert einen Neustart. Grundlage der Zuordnung sind die
[OKX-Instrumentdaten](https://www.okx.com/docs-v5/en/) und die
[EEA-XPerp-Spezifikationen](https://www.okx.com/en-sg/help/x-perps-contract-specifications).
Es werden öffentliche Instrumente geprüft, keine privaten Kontoberechtigungen.

**Umsatz / 5m und / 60m** sind die summierten `volCcyQuote`-Werte des exakten
Kontrakts über fünf bzw. sechzig abgeschlossene Minuten. Es erfolgt keine erneute
Multiplikation mit Kurs oder Kontraktgröße und keine Ersetzung durch 24h-Umsatz.
Fehlt die 60-Minuten-Historie, bleibt diese Kennzahl leer.

## Datenqualität und Bedienung

- Nach ATR, Aktivitätsfaktor, Umsatz, Preis oder Spread sortieren; nach Coin,
  minimalem ATR, Mindestumsatz und maximalem Spread filtern.
- Nur abgeschlossene, validierte Minutenkerzen werden verwendet. Fünf vollständige,
  auf UTC ausgerichtete Minuten bilden eine 5m-Kerze; laufende Blöcke zählen nicht.
- Eine Datenlücke unterbricht die Aufwärmhistorie. Reicht die lückenlose Historie
  nicht für 15 Kerzen des Zeitrahmens, erscheint der Kontrakt in dieser Ansicht nicht.
- Preis und ATR verwenden den Schlusskurs des gewählten Zeitrahmens. Im 5m-Modus
  kann dieser bis zu vier Minuten älter als das Minuten-Messfenster sein; der
  Zeitstempel steht als Hinweis am Preis und ATR. Umsätze verwenden das gemeinsame
  Minutenfenster; Spread ist eine Ticker-Momentaufnahme.
- Paare ohne Umsatz in den letzten fünf Minuten werden ausgelassen.
- Das Maximum `max_pairs` gilt **pro Ansicht**. Intern wird die Vereinigung beider
  Ranglisten aufbewahrt, damit 5m-Favoriten nicht durch die 1m-Auswahl verloren gehen.
- Filter im Browser verengen die geladene Vorauswahl, nicht den gesamten OKX-Markt.

Die erste Messung benötigt mehr Daten. Danach ergänzt ein Cache nur neue Kerzen,
statt die ganze Historie erneut zu laden. Beide Ansichten entstehen aus denselben
Minutendaten. Messung standardmäßig jede Minute, Browserabfrage alle fünf Sekunden.
15 Sekunden Karenz nach Minutenschluss berücksichtigen verzögerte Bestätigungen.
Bei vielen Paaren oder API-Problemen kann der tatsächliche Abstand größer sein.
Fortschritt, Fensterende, Datenalter und Fehler stehen unter „Messung & Datenstatus“.

## Konfiguration und Prüfung

`selection.max_spread_pct` begrenzt den Spread vor dem Kerzenabruf (Standard 0,15 %).
`selection.min_5m_usdt` ist aus Kompatibilitätsgründen so benannt, gilt aber in der
jeweiligen Kurswährung: USD für EEA-XPerps, USDT für Swaps. Dasselbe gilt für den
optionalen `min_24h_usdt`-Vorfilter, der Tagesumsatz nur näherungsweise aus Basisvolumen
und letztem Kurs schätzt. `max_pairs` ist standardmäßig 400 pro Ansicht.

Alte lokale Strategie-/Pushover-Einstellungen werden nicht ausgeführt. Historische
`state.json` und `signals.csv` bleiben unberührt. Der frühere Signalkern bleibt
zur Abwärtskompatibilität im Quellcode, wird vom Finder jedoch nicht aufgerufen.

```powershell
py -3 scanner.py --check
py -3 -m unittest discover -v
```
