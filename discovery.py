"""Public-market discovery with exact instrument identity and bounded candle caching."""
from concurrent.futures import ThreadPoolExecutor, as_completed
import math
import time
from urllib.parse import quote

from market import analyze_history, normalize_minutes

MINUTE_CACHE = {}
MARKETS = {'eea_xperp':('FUTURES','USD','EEA Krypto-XPerps'),
           'usdt_swap':('SWAP','USDT','Globale Krypto-USDT-Swaps')}


def eligible_instrument(row,market):
    if row.get('state')!='live' or str(row.get('instCategory',''))!='1':
        return False
    symbol=row.get('instId','')
    if market=='eea_xperp':
        return (row.get('instType')=='FUTURES' and row.get('ruleType')=='xperp'
                and row.get('settleCcy')=='USD' and row.get('ctType')=='linear'
                and '-USD_UM_XPERP-' in symbol)
    return market=='usdt_swap' and row.get('instType')=='SWAP' and symbol.endswith('-USDT-SWAP')


def instrument_url(symbol,market):
    if market=='eea_xperp':
        # OKX's website uses a tenor alias, not the API's YYMMDD expiry suffix.
        family,separator,expiry=symbol.rpartition('-')
        if not separator or not family.endswith('-USD_UM_XPERP') or len(expiry)!=6 or not expiry.isascii() or not expiry.isdigit():
            raise ValueError('Ungueltige EEA-XPerp-Instrument-ID: '+symbol)
        slug=family.lower()+'-5yearly'
        return 'https://my.okx.com/de/trade-futures/'+quote(slug,safe='-_')
    if market=='usdt_swap':
        return 'https://www.okx.com/trade-swap/'+quote(symbol.lower(),safe='-_')
    raise ValueError('Unbekannter Markt: '+market)


def fetch_minutes(cfg,symbol,end_ts,api):
    key=(cfg.get('okx_api_base',''),cfg.get('market','eea_xperp'),symbol)
    cached=MINUTE_CACHE.get(key,{})
    if cached and end_ts-60000 in cached:
        return list(cached.values())
    latest=max(cached,default=end_ts-300*60000)
    limit=min(300,max(8,(end_ts-latest)//60000+3)) if cached else 300
    rows=api(cfg,'/api/v5/market/candles',instId=symbol,bar='1m',limit=str(limit))
    validated={ts:[str(ts),*map(str,b[1:5]),'0','0',str(b[5]),'1']
               for ts,b in normalize_minutes(rows).items()}
    combined={**cached,**validated}
    # Cache uses the same raw-compatible shape as an API candle.
    ordered=sorted(combined)[-300:]
    raw={ts:combined[ts] for ts in ordered}
    if end_ts-60000 not in raw:
        raise RuntimeError('Neueste abgeschlossene 1m-Kerze fehlt: '+symbol)
    MINUTE_CACHE[key]=raw
    return list(raw.values())


def discover(cfg,api,dashboard=None):
    market=cfg.get('market','eea_xperp')
    if market not in MARKETS:
        raise ValueError('market muss eea_xperp oder usdt_swap sein')
    inst_type,currency,label=MARKETS[market]
    instruments=api(cfg,'/api/v5/public/instruments',instType=inst_type)
    allowed={r['instId']:r for r in instruments if eligible_instrument(r,market)}
    tickers=api(cfg,'/api/v5/market/tickers',instType=inst_type)
    selection=cfg['selection']
    candidates=[]
    for item in tickers:
        symbol=item.get('instId','')
        if symbol not in allowed:
            continue
        try:
            last,bid,ask,volume=map(float,(item['last'],item['bidPx'],item['askPx'],item['volCcy24h']))
        except (KeyError,ValueError,TypeError):
            continue
        if (not all(math.isfinite(v) for v in (last,bid,ask,volume)) or
                min(last,bid,ask)<=0 or volume<0 or bid>ask):
            continue
        spread=(ask-bid)/((ask+bid)/2)*100
        turnover=volume*last
        if not math.isfinite(turnover) or spread>float(selection['max_spread_pct']) or turnover<float(selection.get('min_24h_usdt',0)):
            continue
        candidates.append((symbol,spread,turnover))
    active={c[0] for c in candidates}
    for key in list(MINUTE_CACHE):
        if key[0]!=cfg.get('okx_api_base','') or key[1]!=market or key[2] not in active:
            del MINUTE_CACHE[key]
    end_ts=int((time.time()-15)//60)*60000
    if dashboard:
        dashboard.update(market=market,market_label=label,quote_ccy=currency,processed=0,total=len(candidates))

    def measure(candidate):
        symbol,spread,turnover=candidate
        metrics=analyze_history(fetch_minutes(cfg,symbol,end_ts,api),end_ts)
        if (metrics['turnover_5m']<=0 or metrics['turnover_5m']<float(selection.get('min_5m_usdt',0))
                or not any(metrics['profiles'].values())):
            return None
        return dict(symbol=symbol,spread_pct=spread,turnover_usdt=turnover,
                    turnover_5m_usdt=metrics['turnover_5m'],turnover_60m=metrics['turnover_60m'],
                    volume_end_ts=end_ts,price=metrics['price'],profiles=metrics['profiles'],
                    quote_ccy=currency,market=market,chart_url=instrument_url(symbol,market),
                    volatility_1m_pct=(metrics['profiles']['1m'] or {}).get('atr_pct'))

    measured=[];failures=0;processed=0
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(measure,c):c[0] for c in candidates}
        for future in as_completed(futures):
            try:
                result=future.result()
                if result:
                    measured.append(result)
                    if dashboard:dashboard.update(last_success=time.time())
            except (RuntimeError,ValueError,TypeError,KeyError,OSError) as err:
                failures+=1
                print('DATEN-FEHLER',futures[future],str(err))
                if dashboard:dashboard.report_error('Marktdaten',str(err),futures[future])
            processed+=1
            if dashboard:dashboard.update(processed=processed)
    if candidates and failures==len(candidates):
        raise RuntimeError('Keine vollstaendigen aktuellen Minutenkerzen erhalten')
    # Keep the union of the leaders for each view, not just the 1m leaders.
    selected={}
    for profile in ('1m','5m'):
        ranked=sorted([p for p in measured if p['profiles'][profile]],
                      key=lambda p:(p['profiles'][profile]['atr_pct'],p['turnover_5m_usdt']),reverse=True)
        for rank,pair in enumerate(ranked,1):pair['profiles'][profile]['rank']=rank
        for pair in ranked[:int(selection['max_pairs'])]:selected[pair['symbol']]=pair
    result=sorted(selected.values(),key=lambda p:(p['volatility_1m_pct'] or 0,p['turnover_5m_usdt']),reverse=True)
    if dashboard:
        from datetime import datetime,timezone
        dashboard.update(volume_end=datetime.fromtimestamp(end_ts/1000,timezone.utc).strftime('%Y-%m-%d %H:%M UTC'),
                         volume_end_ts=end_ts/1000,candidates=len(candidates),ranking_errors=failures,
                         filtered_count=len(candidates)-len(measured)-failures,
                         profile_limit=int(selection['max_pairs']),ranking_time=time.time())
    return result
