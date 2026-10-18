"""Market presentation derived from already fetched, completed 1m candles."""
import math


def normalize_minutes(rows):
    """Validated completed OHLC + quote volume, indexed by UTC minute opening time."""
    result = {}
    for row in rows:
        try:
            ts = int(row[0])
            o,h,l,c = map(float,row[1:5])
            volume = float(row[7])
            if (str(row[8]) != '1' or ts < 0 or ts % 60000 or
                    not all(math.isfinite(v) and v > 0 for v in (o,h,l,c)) or
                    not math.isfinite(volume) or volume < 0 or
                    h < max(o,l,c) or l > min(o,c)):
                continue
            result[ts] = (ts,o,h,l,c,volume)
        except (ValueError,TypeError,IndexError):
            continue
    return result


def aggregate_five_minutes(bars, end_ts):
    """Only full UTC-aligned 5m candles; never combine across a missing minute."""
    minutes={b[0]:b for b in bars}
    result=[]
    if not bars:
        return result
    start=(bars[0][0]+299999)//300000*300000
    stop=end_ts//300000*300000
    for ts in range(start,stop,300000):
        group=[minutes.get(ts+i*60000) for i in range(5)]
        if any(b is None for b in group):
            continue
        result.append((ts,group[0][1],max(b[2] for b in group),min(b[3] for b in group),
                       group[-1][4],sum(b[5] for b in group)))
    return result


def atr_profile(bars, length, recent_count, period=14):
    """Wilder ATR seeded with the mean of 14 true ranges (15 price candles)."""
    if len(bars)<period+1 or any(b[0]-a[0]!=length for a,b in zip(bars,bars[1:])):
        return None
    ranges=[max(b[2]-b[3],abs(b[2]-a[4]),abs(b[3]-a[4])) for a,b in zip(bars,bars[1:])]
    atr=sum(ranges[:period])/period
    for value in ranges[period:]:
        atr=(atr*(period-1)+value)/period
    recent=sum(ranges[-recent_count:])/recent_count
    last=bars[-1]
    result=dict(atr_pct=atr/last[4]*100,activity_ratio=recent/atr if atr>0 else None,
                recent_pct=recent/last[4]*100,price=last[4],
                last_range_pct=(last[2]-last[3])/last[1]*100,
                change_pct=(last[4]/last[1]-1)*100,
                asof=(last[0]+length)/1000,history_bars=len(bars))
    return result if all(v is None or math.isfinite(v) for v in result.values()) else None


def analyze_history(rows,end_ts):
    minutes=normalize_minutes(rows)
    # Work exclusively with the uninterrupted suffix ending at the shared measurement time.
    bars=[]
    cursor=end_ts-60000
    while cursor in minutes:
        bars.append(minutes[cursor]);cursor-=60000
    bars.reverse()
    if len(bars)<5:
        raise ValueError('Die letzten fuenf abgeschlossenen Minuten fehlen oder sind ungueltig')
    profiles={'1m':atr_profile(bars,60000,5),
              '5m':atr_profile(aggregate_five_minutes(bars,end_ts),300000,3)}
    return dict(profiles=profiles,turnover_5m=sum(b[5] for b in bars[-5:]),
                turnover_60m=sum(b[5] for b in bars[-60:]) if len(bars)>=60 else None,
                price=bars[-1][4])


def window_metrics(rows, end_ts):
    expected = list(range(end_ts - 300_000, end_ts, 60_000))
    candles = {}
    for row in rows:
        try:
            ts = int(row[0])
            if ts not in expected or str(row[8]) != "1":
                continue
            o, h, l, c = map(float, row[1:5])
            if (not all(math.isfinite(v) and v > 0 for v in (o, h, l, c))
                    or h < max(o, l, c) or l > min(o, c)):
                return {}
            candles[ts] = (o, h, l, c)
        except (ValueError, TypeError, IndexError):
            continue
    if any(ts not in candles for ts in expected):
        return {}
    ordered = [candles[ts] for ts in expected]
    first, last = ordered[0][0], ordered[-1][3]
    minute_ranges = [(b[1]-b[2])/b[0]*100 for b in ordered]
    result = dict(price=last, change_5m_pct=(last/first-1)*100,
                range_5m_pct=(max(b[1] for b in ordered)-min(b[2] for b in ordered))/first*100,
                volatility_1m_pct=sum(minute_ranges)/len(minute_ranges),
                range_1m_pct=minute_ranges[-1],
                change_1m_pct=(last/ordered[-1][0]-1)*100,
                trend=(first, *(b[3] for b in ordered)))
    return result if all(math.isfinite(v) for k,v in result.items() if k!='trend') else {}


class RankingHistory:
    """Compare distinct snapshots, never invent changes for newly appearing coins."""
    def __init__(self):
        self.previous = {}
        self.window = None

    def decorate(self, pairs):
        if not pairs:
            return []
        window = pairs[0].volume_end_ts
        changed = self.window is not None and window > self.window
        result = []
        for rank, pair in enumerate(pairs, 1):
            old = self.previous.get(pair.symbol) if changed else None
            result.append(dict(symbol=pair.symbol, rank=rank, turnover=pair.turnover_usdt,
                               turnover_5m=pair.turnover_5m_usdt, spread=pair.spread_pct,
                               price=pair.price, change_5m=pair.change_5m_pct,
                               range_5m=pair.range_5m_pct, trend=pair.trend,
                               volatility_1m=pair.volatility_1m_pct,
                               range_1m=pair.range_1m_pct, change_1m=pair.change_1m_pct,
                               profiles=pair.profiles, market=pair.market, quote_ccy=pair.quote_ccy,
                               url=pair.chart_url, turnover_60m=pair.turnover_60m,
                               rank_change=old[0]-rank if old else None,
                               turnover_change=(pair.turnover_5m_usdt/old[1]-1)*100
                               if old and old[1] > 0 else None))
        if self.window is None or window > self.window:
            self.previous = {p.symbol: (i, p.turnover_5m_usdt) for i, p in enumerate(pairs, 1)}
            self.window = window
        return result
