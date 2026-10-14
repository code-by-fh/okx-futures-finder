#!/usr/bin/env python3
"""OKX 5m liquidity-sweep / structure-break scanner with Pushover notifications.

Only public market-data endpoints. No API keys from OKX, trading, or order execution.
Compatible with Python 3.10+ and its standard library.
"""

from __future__ import annotations

import argparse
import csv
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import time
import threading
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from dashboard import Dashboard, HealthMonitor

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
    return sum(volumes[ts] for ts in expected)


def find_pairs(cfg: dict, dashboard: Dashboard | None = None) -> list[Pair]:
    live_rows = okx_api(cfg, "/api/v5/public/instruments", instType="SWAP")
    live = {r.get("instId") for r in live_rows if r.get("state") == "live"}
    tickers = okx_api(cfg, "/api/v5/market/tickers", instType="SWAP")
    selection = cfg["selection"]
    candidates: list[Pair] = []
    for item in tickers:
        symbol = item.get("instId", "")
        if not symbol.endswith("-USDT-SWAP") or symbol not in live:
            continue
        try:
            last = float(item["last"])
            bid, ask = float(item["bidPx"]), float(item["askPx"])
            vol_base = float(item["volCcy24h"])
        except (ValueError, KeyError, TypeError):
            continue
        if min(last, bid, ask, vol_base) <= 0 or bid > ask:
            continue
        # For OKX derivatives, volCcy24h is in base currency (e.g., BTC).
        # Approximate USDT turnover = base volume times most recent price.
        turnover_usdt = vol_base * last
        spread_pct = (ask - bid) / ((ask + bid) / 2) * 100
        if turnover_usdt < float(selection.get("min_24h_usdt", 0)):
            continue
        if spread_pct > float(selection["max_spread_pct"]):
            continue
        candidates.append(Pair(symbol, turnover_usdt, spread_pct))
    # Do not preselect by 24h turnover: newly active coins must also be measured.
    end_ts = int((time.time() - 3) // 60) * 60_000
    ranked = []
    failures = 0

    def measure(pair):
        rows = okx_api(cfg, "/api/v5/market/candles", instId=pair.symbol, bar="1m", limit="8")
        volume = recent_turnover(rows, end_ts)
        if volume is None:
            raise RuntimeError("Unvollstaendige oder veraltete 1m-Umsatzdaten")
        if volume <= 0 or volume < float(selection.get("min_5m_usdt", 0)):
            return None
        return Pair(pair.symbol, pair.turnover_usdt, pair.spread_pct, volume, end_ts)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(measure, pair): pair.symbol for pair in candidates}
        for future in as_completed(futures):
            try:
                pair = future.result()
                if pair:
                    ranked.append(pair)
                    if dashboard:
                        dashboard.update(last_success=time.time())
            except (RuntimeError, ValueError, KeyError, TypeError) as err:
                failures += 1
                print(f"  UMSATZ-FEHLER {futures[future]}: {err}", file=sys.stderr)
    if dashboard:
        dashboard.update(ranking_errors=failures, ranking_time=time.time(),
                         volume_end=utc_time(end_ts), candidates=len(candidates))
    if candidates and failures == len(candidates):
        raise RuntimeError("Keine aktuellen 5m-Umsatzdaten erhalten")
    ranked.sort(key=lambda pair: pair.turnover_5m_usdt, reverse=True)
    return ranked[:int(selection["max_pairs"])]


def read_config() -> dict:
    if not CONFIG_FILE.exists():
        raise RuntimeError("config.json fehlt. Bitte config.example.json nach config.json kopieren und ausfüllen.")
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    cfg.setdefault("dashboard", {"enabled": True, "port": 8765})
    cfg.setdefault("notifications", {"summary_seconds": 120, "outage_seconds": 180})
    if float(cfg["notifications"].get("summary_seconds", 120)) < 0:
        raise ValueError("notifications.summary_seconds muss >= 0 sein")
    if float(cfg["notifications"].get("outage_seconds", 180)) < 10:
        raise ValueError("notifications.outage_seconds muss >= 10 sein")
    if cfg.get("strategy", {}).get("timeframe") not in ("1m", "3m", "5m", "15m"):
        raise ValueError("strategy.timeframe muss 1m, 3m, 5m oder 15m sein")
    for key in ("pivot_left", "pivot_right", "bos_window_bars", "max_pivot_age_bars"):
        if int(cfg["strategy"][key]) < 1:
            raise ValueError(f"strategy.{key} muss >= 1 sein")
    if not cfg.get("okx_api_base", "").startswith("https://"):
        raise ValueError("Bitte eine HTTPS-OKX-API-Domain angeben")
    if int(cfg["selection"]["max_pairs"]) < 1:
        raise ValueError("selection.max_pairs muss >= 1 sein")
    if float(cfg["runtime"]["refresh_pairs_minutes"]) <= 0:
        raise ValueError("runtime.refresh_pairs_minutes muss > 0 sein")
    for key in ("min_24h_usdt", "min_5m_usdt", "max_spread_pct"):
        value = float(cfg["selection"].get(key, 0))
        if not math.isfinite(value) or value < 0:
            raise ValueError(f"selection.{key} muss eine endliche Zahl >= 0 sein")
    if float(cfg["runtime"]["poll_seconds"]) < 10:
        raise ValueError("runtime.poll_seconds muss >= 10 sein")
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
        print(f"  {pair.symbol:<20} {pair.turnover_5m_usdt:,.0f} USDT/5m"
              f"  Spread {pair.spread_pct:.3f}%  {chart_link(pair.symbol)}")


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
                   bars: list[Candle] | None = None) -> tuple[int, int]:
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
                    "url": chart_link(pair.symbol)})
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
    print("Pruefe oeffentliche OKX API ...")
    pairs = find_pairs(cfg)
    if not pairs:
        raise RuntimeError("Keine geeigneten Paare. Filter oder okx_api_base pruefen.")
    print(f"Gefundene, liquide USDT-SWAPs: {len(pairs)}")
    print_pairs(pairs)
    bars = get_bars(cfg, pairs[0].symbol)
    if not bars:
        raise RuntimeError("Keine bestaetigten Candles erhalten")
    print(f"Kerzen-Test OK: {pairs[0].symbol}, letzte abgeschlossene Kerze "
          f"{utc_time(bars[-1].ts)}, Close={pretty_price(bars[-1].c)}")
    events = find_signals(bars, cfg["strategy"])
    recent = events[-3:]
    for event in recent:
        print(f"  Historisches Testsignal: {event.kind} {utc_time(event.ts)}")
    print("Kein Push versendet; keine Trades platziert.")


def loop(cfg: dict, once: bool, dashboard: Dashboard | None = None) -> None:
    state = read_state()
    pairs: list[Pair] = []
    next_refresh = 0.0
    poll = float(cfg["runtime"]["poll_seconds"])
    summary_seconds = float(cfg.get("notifications", {}).get("summary_seconds", 120))
    next_summary = time.monotonic() + summary_seconds
    print("OKX-SMC-Scanner aktiv. Abbrechen: Strg+C")
    print("Erster Durchlauf setzt Startmarken (keine alten Pushes).")
    while True:
        cycle_start = time.monotonic()
        try:
            if not pairs or time.monotonic() >= next_refresh:
                pairs = find_pairs(cfg, dashboard)
                if not pairs:
                    raise RuntimeError("Keine Paare nach Liquiditaets-/Spreadfilter")
                next_refresh = cycle_start + 60 * float(cfg["runtime"]["refresh_pairs_minutes"])
                # Evict histories for coins no longer watched to bound memory use.
                active = {pair.symbol for pair in pairs}
                for key in list(CANDLE_CACHE):
                    if key[1] not in active:
                        del CANDLE_CACHE[key]
                print(f"Watchlist aktualisiert: {len(pairs)} Paare (Top: "
                      + ", ".join(p.symbol.split('-')[0] for p in pairs[:6]) + ")")
                print_pairs(pairs)
                if dashboard:
                    dashboard.update(pairs=[{"symbol": p.symbol, "turnover": p.turnover_usdt,
                                             "turnover_5m": p.turnover_5m_usdt,
                                             "spread": p.spread_pct, "url": chart_link(p.symbol)} for p in pairs])
            good = errcount = 0
            successful = 0
            # Fetch concurrently; signal/state/file mutations stay in the main thread.
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(get_bars, cfg, pair.symbol): pair for pair in pairs}
                for future in as_completed(futures):
                    pair = futures[future]
                    try:
                        bars = future.result()
                        pushed, failed = process_symbol(cfg, pair, state, batch=summary_seconds > 0, bars=bars)
                        successful += 1
                        good += pushed
                        errcount += failed
                        if dashboard:
                            dashboard.update(last_success=time.time())
                    except (RuntimeError, ValueError, KeyError, TypeError) as err:
                        errcount += 1
                        print(f"  API-FEHLER {pair.symbol}: {err}", file=sys.stderr)
                        if dashboard:
                            dashboard.update(last_error=f"{pair.symbol}: {err}")
            save_state(state)
            if state.get("pending") and (once or time.monotonic() >= next_summary):
                try:
                    flush_summary(cfg, state)
                    next_summary = time.monotonic() + summary_seconds
                except Exception as err:
                    errcount += 1
                    print(f"Zusammenfassung fehlgeschlagen: {err}", file=sys.stderr)
                    if dashboard:
                        dashboard.update(last_error=f"Pushover: {err}")
            if dashboard:
                dashboard.update(last_scan=time.time(), errors=errcount,
                                 pending=len(state.get("pending", [])),
                                 network_requests=NETWORK_STATS["requests"],
                                 network_bytes=NETWORK_STATS["bytes"],
                                 cycle_seconds=round(time.monotonic() - cycle_start, 1))
                if successful:
                    dashboard.update(last_success=time.time())
            print(f"{datetime.now().strftime('%H:%M:%S')} | "
                  f"{len(pairs)} Coins geprueft | {good} Push(es) | {errcount} Fehler")
        except Exception as err:
            print(f"Zyklus-Fehler: {err}", file=sys.stderr)
            if dashboard:
                dashboard.update(last_error=str(err), errors=1)
        if once:
            break
        try:
            wait = min(poll - (time.monotonic() - cycle_start), next_refresh - time.monotonic())
            time.sleep(max(2.0, wait))
        except KeyboardInterrupt:
            break


def main() -> int:
    parser = argparse.ArgumentParser(description="OKX Sweep/BOS Scanner mit Pushover")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="OKX API und Filter testen, ohne Push")
    mode.add_argument("--test-push", action="store_true", help="Pushover-Testnachricht senden")
    mode.add_argument("--once", action="store_true", help="Einen kompletten Scanner-Durchlauf starten")
    args = parser.parse_args()
    try:
        cfg = read_config()
        if args.check:
            print_health_check(cfg)
        elif args.test_push:
            push_notification(cfg, "OKX Scanner: Push-Test",
                              "Deine Verbindung funktioniert. Zukuenftige Sweep/BOS-Signale kommen hier an.")
            print("Pushover-Test erfolgreich gesendet.")
        else:
            dashboard = Dashboard(ROOT, int(cfg["dashboard"].get("port", 8765)))
            stop = threading.Event()
            if cfg["dashboard"].get("enabled", True):
                dashboard.start()
            monitor = HealthMonitor(dashboard, cfg["notifications"].get("outage_seconds", 180),
                                    lambda title, message: push_notification(cfg, title, message))
            thread = threading.Thread(target=monitor.run, args=(stop,), daemon=True)
            if not args.once:
                thread.start()
            try:
                loop(cfg, args.once, dashboard)
            finally:
                stop.set()
                if thread.is_alive():
                    thread.join(timeout=15)
                dashboard.close()
        return 0
    except (RuntimeError, ValueError, KeyError, OSError, json.JSONDecodeError) as err:
        print(f"FEHLER: {err}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nScanner gestoppt.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
