#!/usr/bin/env python3
"""OKX Futures-Finder: rank USDT perpetuals by completed one-minute candle ranges.

Only public market-data endpoints. No API keys from OKX, trading, or order execution.
Compatible with Python 3.10+ and its standard library.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import sys
import time
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dashboard import Dashboard, HealthMonitor
from market import window_metrics, RankingHistory
from discovery import discover, MARKETS

ROOT = Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
STATE_FILE = ROOT / "state.json"
SIGNALS_FILE = ROOT / "signals.csv"
PUSH_URL = "https://api.pushover.net/1/messages.json"
API_LOCK = threading.Lock()
API_NEXT_REQUEST = 0.0
NETWORK_STATS = {"requests": 0, "bytes": 0}
CANDLE_CACHE: dict[tuple[str, str, str], tuple[float, list[Candle]]] = {}


@dataclass(frozen=True)
class Candle:
    ts: int  # Opening time in milliseconds UTC
    o: float
    h: float
    l: float
    c: float


@dataclass(frozen=True)
class Signal:
    kind: str  # BULL_SWEEP, BEAR_SWEEP, BULL_BOS, BEAR_BOS
    ts: int
    close: float
    sweep_level: float
    break_level: float
    sweep_ts: int


@dataclass(frozen=True)
class Pair:
    symbol: str
    turnover_usdt: float
    spread_pct: float
    turnover_5m_usdt: float = 0.0
    volume_end_ts: int = 0
    price: float | None = None
    change_5m_pct: float | None = None
    range_5m_pct: float | None = None
    trend: tuple[float, ...] = ()
    volatility_1m_pct: float | None = None
    range_1m_pct: float | None = None
    change_1m_pct: float | None = None
    profiles: dict = field(default_factory=dict)
    market: str = 'eea_xperp'
    quote_ccy: str = 'USD'
    chart_url: str = ''
    turnover_60m: float | None = None


def utc_time(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def pretty_price(price: float) -> str:
    return f"{price:.9g}"


def request_json(url: str, params: dict[str, Any] | None = None,
                 post: dict[str, Any] | None = None, retries: int = 2) -> dict:
    if params:
        url += "?" + urlencode(params)
    payload = urlencode(post).encode("utf-8") if post is not None else None
    req = Request(url, data=payload, headers={"User-Agent": "OKX-Sweep-Scanner/1.0"})
    if payload is not None:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    for attempt in range(retries + 1):
        try:
            if post is None:
                with API_LOCK:
                    NETWORK_STATS["requests"] += 1
            with urlopen(req, timeout=12) as response:
                raw = response.read()
                if post is None:
                    with API_LOCK:
                        NETWORK_STATS["bytes"] += len(raw)
                content = json.loads(raw.decode("utf-8"))
            if not isinstance(content, dict):
                raise ValueError("Unerwartete Antwort (kein JSON-Objekt)")
            return content
        except HTTPError as err:
            # Never auto-retry Pushover POSTs: a timed-out push may have been delivered.
            retryable = post is None and err.code in (429, 500, 502, 503, 504)
            if not retryable or attempt == retries:
                detail = err.read(250).decode("utf-8", errors="replace")
                raise RuntimeError(f"HTTP {err.code}: {detail}") from err
        except (URLError, TimeoutError) as err:
            if post is not None or attempt == retries:
                raise RuntimeError(f"Netzwerkfehler: {err}") from err
        if attempt < retries:
            time.sleep(1.0 + attempt * 2.0)
    raise RuntimeError("Unbekannter Netzwerkfehler")


def okx_api(cfg: dict, path: str, **params: str) -> list:
    # Across all workers: at most ~8 initial requests/second, below OKX's limit.
    global API_NEXT_REQUEST
    with API_LOCK:
        time.sleep(max(0.0, API_NEXT_REQUEST - time.monotonic()))
        API_NEXT_REQUEST = time.monotonic() + 0.125
    response = request_json(cfg["okx_api_base"].rstrip("/") + path, params)
    if response.get("code") != "0" or not isinstance(response.get("data"), list):
        raise RuntimeError(f"OKX API: {response.get('code')} {response.get('msg')}")
    return response["data"]


def parse_candles(rows: list) -> list[Candle]:
    candles = []
    for row in rows:
        if len(row) < 9 or str(row[-1]) != "1":  # Skip unconfirmed bars
            continue
        ts, o, h, l, c = int(row[0]), *map(float, row[1:5])
        if min(o, h, l, c) <= 0 or h < l:
            continue
        candles.append(Candle(ts, o, h, l, c))
    # OKX returns newest bars first; cached services can also overlap timestamps.
    return sorted({bar.ts: bar for bar in candles}.values(), key=lambda bar: bar.ts)


def is_pivot_high(bars: list[Candle], p: int, left: int, right: int) -> bool:
    value = bars[p].h
    return (all(value > bars[j].h for j in range(p - left, p)) and
            all(value >= bars[j].h for j in range(p + 1, p + right + 1)))


def is_pivot_low(bars: list[Candle], p: int, left: int, right: int) -> bool:
    value = bars[p].l
    return (all(value < bars[j].l for j in range(p - left, p)) and
            all(value <= bars[j].l for j in range(p + 1, p + right + 1)))


def find_signals(bars: list[Candle], strategy: dict) -> list[Signal]:
    """Causal algorithm: pivot levels enter the strategy only after confirmation.

    Sweep: confirmed candle wicks beyond a known pivot, closes back inside.
    BOS: later candle closes across last known opposing pivot within N bars.
    Not a complete ICT market-structure model or trade recommendation.
    """
    left = int(strategy["pivot_left"])
    right = int(strategy["pivot_right"])
    window = int(strategy["bos_window_bars"])
    pivot_age = int(strategy["max_pivot_age_bars"])
    sweep_mult = float(strategy["min_sweep_depth_pct"]) / 100
    bos_mult = float(strategy["min_bos_break_pct"]) / 100

    high_level: tuple[float, int] | None = None
    low_level: tuple[float, int] | None = None
    consumed_high: int | None = None
    consumed_low: int | None = None
    pending_long: dict | None = None
    pending_short: dict | None = None
    signals: list[Signal] = []

    for i, bar in enumerate(bars):
        p = i - right
        if p >= left:
            if is_pivot_high(bars, p, left, right):
                high_level = (bars[p].h, p)
            if is_pivot_low(bars, p, left, right):
                low_level = (bars[p].l, p)

        # BOS first: it can only use a sweep from a previous candle.
        if pending_long is not None:
            age = i - pending_long["index"]
            if age > window or bar.c <= pending_long["extreme"]:
                pending_long = None
            elif age > 0:
                threshold = pending_long["break_level"] * (1 + bos_mult)
                if bars[i - 1].c <= threshold < bar.c:
                    signals.append(Signal("BULL_BOS", bar.ts, bar.c,
                                          pending_long["sweep_level"],
                                          pending_long["break_level"],
                                          pending_long["sweep_ts"]))
                    pending_long = None

        if pending_short is not None:
            age = i - pending_short["index"]
            if age > window or bar.c >= pending_short["extreme"]:
                pending_short = None
            elif age > 0:
                threshold = pending_short["break_level"] * (1 - bos_mult)
                if bars[i - 1].c >= threshold > bar.c:
                    signals.append(Signal("BEAR_BOS", bar.ts, bar.c,
                                          pending_short["sweep_level"],
                                          pending_short["break_level"],
                                          pending_short["sweep_ts"]))
                    pending_short = None

        prev = bars[i - 1] if i > 0 else None
        valid_high = high_level is not None and i - high_level[1] <= pivot_age
        valid_low = low_level is not None and i - low_level[1] <= pivot_age
        bull = (prev is not None and valid_low and valid_high and
                low_level[1] != consumed_low and
                prev.c > low_level[0] and
                bar.l < low_level[0] * (1 - sweep_mult) and
                bar.c > low_level[0])
        bear = (prev is not None and valid_high and valid_low and
                high_level[1] != consumed_high and
                prev.c < high_level[0] and
                bar.h > high_level[0] * (1 + sweep_mult) and
                bar.c < high_level[0])

        # Broad outside bars that sweep both sides are ambiguous; ignore.
        if bull and bear:
            continue
        if bull:
            signals.append(Signal("BULL_SWEEP", bar.ts, bar.c,
                                  low_level[0], high_level[0], bar.ts))
            consumed_low = low_level[1]
            pending_long = {"index": i, "sweep_level": low_level[0],
                            "break_level": high_level[0],
                            "extreme": bar.l, "sweep_ts": bar.ts}
            pending_short = None
        if bear:
            signals.append(Signal("BEAR_SWEEP", bar.ts, bar.c,
                                  high_level[0], low_level[0], bar.ts))
            consumed_high = high_level[1]
            pending_short = {"index": i, "sweep_level": high_level[0],
                             "break_level": low_level[0],
                             "extreme": bar.h, "sweep_ts": bar.ts}
            pending_long = None
    return signals


def recent_turnover(rows: list, end_ts: int) -> float | None:
    """Quote-currency volume of exactly five completed 1m bars, same window for all coins."""
    volumes = {}
    for row in rows:
        try:
            ts = int(row[0])
            volume = float(row[7])
            if str(row[8]) == "1" and math.isfinite(volume) and volume >= 0:
                volumes[ts] = volume
        except (ValueError, TypeError, IndexError):
            continue
    expected = range(end_ts - 300_000, end_ts, 60_000)
    if not all(ts in volumes for ts in expected):
        return None
    total = sum(volumes[ts] for ts in expected)
    return total if math.isfinite(total) else None


def find_pairs(cfg: dict, dashboard: Dashboard | None = None) -> list[Pair]:
    return [Pair(**record) for record in discover(cfg, okx_api, dashboard)]


def read_config() -> dict:
    config_path = Path(os.environ.get('OKX_CONFIG_FILE', str(CONFIG_FILE)))
    if not config_path.exists():
        raise RuntimeError("config.json fehlt. config.example.json nach config.json kopieren.")
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    cfg.setdefault("dashboard", {"enabled": True, "port": 8765})
    cfg.setdefault("runtime", {})
    cfg.setdefault("selection", {})
    overrides = {
        'OKX_API_BASE': (None, 'okx_api_base', str),
        'OKX_MARKET': (None, 'market', str),
        'DASHBOARD_HOST': ('dashboard', 'host', str),
        'PORT': ('dashboard', 'port', int),
        'MAX_PAIRS': ('selection', 'max_pairs', int),
        'MIN_5M_TURNOVER': ('selection', 'min_5m_usdt', float),
        'MIN_24H_TURNOVER': ('selection', 'min_24h_usdt', float),
        'MAX_SPREAD_PCT': ('selection', 'max_spread_pct', float),
        'REFRESH_MINUTES': ('runtime', 'refresh_pairs_minutes', float),
    }
    for name, (section, key, convert) in overrides.items():
        if name in os.environ:
            try:
                target = cfg[section] if section else cfg
                target[key] = convert(os.environ[name].strip())
            except (ValueError, TypeError) as err:
                raise ValueError(f'Ungueltiger Wert fuer {name}') from err
    cfg['dashboard'].setdefault('host', '127.0.0.1')
    port = cfg['dashboard'].get('port', 8765)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise ValueError('dashboard.port muss zwischen 1 und 65535 liegen')
    if not cfg['dashboard']['host']:
        raise ValueError('dashboard.host darf nicht leer sein')
    cfg.setdefault("market", "eea_xperp")
    if cfg["market"] not in MARKETS:
        raise ValueError("market muss eea_xperp oder usdt_swap sein")
    cfg["runtime"].setdefault("refresh_pairs_minutes", 1)
    for key, default in {"max_pairs": 400, "min_24h_usdt": 0,
                         "min_5m_usdt": 0, "max_spread_pct": .15}.items():
        cfg["selection"].setdefault(key, default)
    if not cfg.get("okx_api_base", "").startswith("https://"):
        raise ValueError("Bitte eine HTTPS-OKX-API-Domain angeben")
    count = cfg["selection"]["max_pairs"]
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("selection.max_pairs muss eine ganze Zahl >= 1 sein")
    refresh = float(cfg["runtime"]["refresh_pairs_minutes"])
    if not math.isfinite(refresh) or refresh < 1:
        raise ValueError("runtime.refresh_pairs_minutes muss >= 1 sein")
    for key in ("min_24h_usdt", "min_5m_usdt", "max_spread_pct"):
        value = float(cfg["selection"][key])
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"selection.{key} muss eine endliche Zahl >= 0 sein")
    # Old local configurations remain readable, but the finder never runs strategies.
    cfg["runtime"]["signals_enabled"] = False
    return cfg


def read_state() -> dict:
    if STATE_FILE.exists():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            if isinstance(data.get("last_seen"), dict) and isinstance(data.get("sent"), list):
                return data
        except (ValueError, OSError):
            print("WARNUNG: state.json unlesbar, beginne mit neuen Startmarken.", file=sys.stderr)
    return {"last_seen": {}, "sent": []}


def save_state(state: dict) -> None:
    state["sent"] = state["sent"][-2000:]
    temp = STATE_FILE.with_suffix(".json.tmp")
    temp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    temp.replace(STATE_FILE)


def log_signal(symbol: str, signal: Signal) -> None:
    new = not SIGNALS_FILE.exists()
    with SIGNALS_FILE.open("a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        if new:
            writer.writerow(["time_utc", "symbol", "event", "close", "swept_level", "bos_level"])
        writer.writerow([utc_time(signal.ts), symbol, signal.kind,
                         signal.close, signal.sweep_level, signal.break_level])


def chart_link(symbol: str) -> str:
    return "https://www.okx.com/trade-swap/" + symbol.lower()


def print_pairs(pairs: list[Pair]) -> None:
    for pair in pairs:
        volatility = pair.volatility_1m_pct
        value = f"{volatility:.3f}%" if volatility is not None else "n/a"
        print(f"  {pair.symbol:<22} 1m-ATR: {value:>8}"
              f"  {pair.turnover_5m_usdt:,.0f} {pair.quote_ccy}/5m"
              f"  Spread {pair.spread_pct:.3f}%  {(pair.chart_url or chart_link(pair.symbol))}")


def push_notification(cfg: dict, title: str, message: str, url: str = "") -> None:
    keys = cfg["pushover"]
    if not keys.get("app_token") or not keys.get("user_key") or \
            "HIER_" in keys.get("app_token", "") or "HIER_" in keys.get("user_key", ""):
        raise RuntimeError("Pushover app_token und user_key in config.json ausfüllen")
    post = {"token": keys["app_token"], "user": keys["user_key"],
            "title": title, "message": message, "priority": "0"}
    if url:
        post.update({"url": url, "url_title": "Chart bei OKX"})
    response = request_json(PUSH_URL, post=post, retries=0)
    if response.get("status") != 1:
        raise RuntimeError(f"Pushover meldet Fehler: {response.get('errors', [])}")


def push_signal(cfg: dict, symbol: str, signal: Signal) -> None:
    is_bull = signal.kind.startswith("BULL")
    is_bos = signal.kind.endswith("BOS")
    side = "LONG" if is_bull else "SHORT"
    phase = "BOS nach Sweep" if is_bos else "Liquidity Sweep"
    title = f"OKX {cfg['strategy']['timeframe']} | {side} {phase} | {symbol.split('-')[0]}"
    extra = (f"BOS-Level: {pretty_price(signal.break_level)}\n"
             if is_bos else f"Moegliches BOS-Level: {pretty_price(signal.break_level)}\n")
    message = (f"{symbol}\n"
               f"Signal: {signal.kind.replace('_', ' ')}\n"
               f"Kerzenschluss: {pretty_price(signal.close)}\n"
               f"Gesweeptes Level: {pretty_price(signal.sweep_level)}\n"
               f"{extra}"
               f"Kurskerze: {utc_time(signal.ts)}\n"
               "Nur Setup-Kandidat; 15m-Kontext, 1m-Entry und Risiko pruefen.")
    push_notification(cfg, title, message, chart_link(symbol))


def timeframe_ms(bar: str) -> int:
    return {"1m": 60000, "3m": 180000, "5m": 300000, "15m": 900000}[bar]


def get_bars(cfg: dict, symbol: str) -> list[Candle]:
    timeframe = cfg["strategy"]["timeframe"]
    key = (cfg["okx_api_base"], symbol, timeframe)
    now = time.time()
    cached = CANDLE_CACHE.get(key)
    if cached and now < cached[0]:
        return cached[1]
    length = timeframe_ms(timeframe)
    expected_ts = int((now * 1000 - 3000) // length) * length - length
    limit = 150 if not cached else min(150, max(3, (expected_ts - cached[1][-1].ts) // length + 3))
    rows = okx_api(cfg, "/api/v5/market/candles", instId=symbol,
                   bar=timeframe, limit=str(limit))
    bars = parse_candles(rows)
    if not bars:
        raise RuntimeError("Keine bestaetigten Candles erhalten")
    combined = {bar.ts: bar for bar in (cached[1] if cached else [])}
    combined.update({bar.ts: bar for bar in bars})
    bars = sorted(combined.values(), key=lambda bar: bar.ts)[-150:]
    expiry = (bars[-1].ts + 2 * length) / 1000 + 3
    if bars[-1].ts < expected_ts:
        raise RuntimeError("Veraltete Kerzendaten erhalten")
    CANDLE_CACHE[key] = (max(now + 2, expiry), bars)
    return bars


def process_symbol(cfg: dict, pair: Pair, state: dict, batch: bool = False,
                   bars: list[Candle] | None = None, dashboard: Dashboard | None = None) -> tuple[int, int]:
    bars = get_bars(cfg, pair.symbol) if bars is None else bars
    if len(bars) < 30:
        return 0, 0
    last_ts = bars[-1].ts
    stored = state["last_seen"].get(pair.symbol)
    if stored is None:
        state["last_seen"][pair.symbol] = last_ts
        return 0, 0  # First run: do not send historical alerts.
    if last_ts <= int(stored):
        return 0, 0

    found = find_signals(bars, cfg["strategy"])
    now_ms = int(time.time() * 1000)
    max_age = float(cfg["runtime"]["max_signal_age_minutes"]) * 60_000
    bar_length = timeframe_ms(cfg["strategy"]["timeframe"])
    sent_set = set(state["sent"])
    good, errors = 0, 0
    for signal in found:
        if signal.ts <= int(stored):
            continue
        age_ms = now_ms - (signal.ts + bar_length)
        if age_ms < -30000 or age_ms > max_age:
            continue  # No stale phone notifications after an outage.
        if signal.kind.endswith("SWEEP") and not cfg["strategy"]["notify_sweeps"]:
            continue
        if signal.kind.endswith("BOS") and not cfg["strategy"]["notify_bos"]:
            continue
        key = f"{pair.symbol}:{signal.ts}:{signal.kind}"
        if key in sent_set:
            continue
        try:
            if batch:
                state.setdefault("pending", []).append({
                    "symbol": pair.symbol, "kind": signal.kind,
                    "time": utc_time(signal.ts), "close": signal.close,
                    "url": (pair.chart_url or chart_link(pair.symbol))})
            else:
                push_signal(cfg, pair.symbol, signal)
            log_signal(pair.symbol, signal)
            state["sent"].append(key)
            sent_set.add(key)
            good += 1
            print(f"  ALERT {pair.symbol:<20} {signal.kind:<12} {utc_time(signal.ts)}")
            # Persist successful message keys immediately, to prevent duplicates on restart.
            save_state(state)
        except Exception as err:
            errors += 1
            print(f"  PUSH-FEHLER {pair.symbol} {signal.kind}: {err}", file=sys.stderr)
            if dashboard:
                dashboard.report_error("Signalversand", str(err), pair.symbol)
    # When sending failed, keep prior timestamp so recent signals get another try.
    if errors == 0:
        state["last_seen"][pair.symbol] = last_ts
    return good, errors


def flush_summary(cfg: dict, state: dict) -> int:
    sent = 0
    pending = state.setdefault("pending", [])
    while pending:
        lines = []
        count = 0
        for event in pending:
            line = (f"{event['symbol']} | {event['kind']} | {event['close']}\n"
                    f"{event['time']}\n{event['url']}")
            if lines and len("\n\n".join(lines + [line]).encode("utf-8")) > 900:
                break
            lines.append(line)
            count += 1
        push_notification(cfg, f"OKX: {count} neue Signale", "\n\n".join(lines))
        del pending[:count]
        save_state(state)
        sent += 1
    return sent


def print_health_check(cfg: dict) -> None:
    print("Pruefe oeffentliche OKX-Daten fuer den Futures-Finder ...")
    pairs = find_pairs(cfg)
    print(f"{len(pairs)} geeignete Krypto-Kontrakte mit ATR-Profilen:")
    print_pairs(pairs)
    print("Keine Signalanalyse, keine Pushes, keine Trades.")


def loop(cfg: dict, once: bool, dashboard: Dashboard | None = None) -> None:
    history = RankingHistory()
    refresh = float(cfg["runtime"]["refresh_pairs_minutes"]) * 60
    if dashboard:
        dashboard.update(refresh_seconds=refresh, signals_enabled=False)
    print("OKX Futures-Finder aktiv. Abbrechen: Strg+C")
    while True:
        started = time.monotonic()
        print(f"[{utc_time(int(time.time()*1000))}] Messung gestartet.", flush=True)
        try:
            if dashboard:
                dashboard.update(phase="ATR und kurzfristige Aktivitaet werden gemessen", processed=0, total=0)
            pairs = find_pairs(cfg, dashboard)
            print_pairs(pairs)
            if dashboard:
                dashboard.update(pairs=history.decorate(pairs),
                                 last_scan=time.time(), errors=0,
                                 phase="Wartet auf naechste Messung")
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as err:
            print(f"DATEN-FEHLER: {err}", file=sys.stderr)
            if dashboard:
                dashboard.report_error("Marktdaten", str(err))
                dashboard.update(errors=1, phase="Datenfehler - naechster Versuch folgt")
        if dashboard:
            dashboard.update(network_requests=NETWORK_STATS["requests"],
                             network_bytes=NETWORK_STATS["bytes"],
                             cycle_seconds=round(time.monotonic()-started, 1))
        if once:
            print("Einmalige Messung beendet (--once).", flush=True)
            return
        wait_seconds = max(2, refresh-(time.monotonic()-started))
        print(f"Messung abgeschlossen. Naechste Messung in {wait_seconds:.0f} Sekunden; "
              "Finder und UI laufen weiter.", flush=True)
        time.sleep(wait_seconds)


def main() -> int:
    parser = argparse.ArgumentParser(description="OKX Futures-Finder: ATR fuer 1m- und 5m-Charts")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="Oeffentliche Daten pruefen und Rangliste ausgeben")
    mode.add_argument("--once", action="store_true", help="Eine Messung ausfuehren")
    parser.add_argument("--market-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    try:
        cfg = read_config()
        if args.check:
            print_health_check(cfg)
            return 0
        dashboard = Dashboard(ROOT, int(cfg["dashboard"].get("port", 8765)), cfg['dashboard']['host'])
        if cfg["dashboard"].get("enabled", True):
            dashboard.start()
        try:
            loop(cfg, args.once, dashboard)
        finally:
            dashboard.close()
        return 0
    except KeyboardInterrupt:
        print("Futures-Finder gestoppt.")
        return 0
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as err:
        print(f"FEHLER: {err}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
