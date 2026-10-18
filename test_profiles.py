import unittest
from unittest.mock import patch

from discovery import MINUTE_CACHE, discover, eligible_instrument, fetch_minutes, instrument_url
from market import aggregate_five_minutes, analyze_history, atr_profile


def minute_history(end,count=300,volume=10):
    return [[str(end-i*60000),'100','102','98','100','99999','88888',str(volume),'1']
            for i in range(1,count+1)]


class ProfileTests(unittest.TestCase):
    def tearDown(self):MINUTE_CACHE.clear()

    def test_wilder_seed_and_recursive_update(self):
        bars=[(i*60000,100,102,98,100,10) for i in range(15)]
        self.assertAlmostEqual(atr_profile(bars,60000,5)['atr_pct'],4)
        bars.append((15*60000,100,110,100,100,10))
        result=atr_profile(bars,60000,5)
        self.assertAlmostEqual(result['atr_pct'],(4*13+10)/14)
        self.assertAlmostEqual(result['activity_ratio'],((4*4+10)/5)/((4*13+10)/14))

    def test_true_range_includes_previous_close_gap(self):
        bars=[(0,100,100,100,100,10),(60000,110,112,109,111,10)]
        bars += [(i*60000,111,111,111,111,10) for i in range(2,15)]
        result=atr_profile(bars,60000,5)
        self.assertAlmostEqual(result['atr_pct'],(12/14)/111*100)
        self.assertEqual(result['activity_ratio'],0)

    def test_five_minute_aggregation_is_aligned_and_ignores_partial_bar(self):
        bars=[(i*60000,100+i,102+i,98+i,101+i,10) for i in range(12)]
        result=aggregate_five_minutes(bars,12*60000)
        self.assertEqual([b[0] for b in result],[0,300000])
        self.assertEqual(result[0],(0,100,106,98,105,50))

    def test_profiles_have_separate_completion_times_and_recent_windows(self):
        end=302*60000
        result=analyze_history(minute_history(end),end)
        self.assertEqual(result['profiles']['1m']['asof'],end/1000)
        self.assertEqual(result['profiles']['5m']['asof'],300*60)
        self.assertEqual(result['turnover_5m'],50)
        self.assertEqual(result['turnover_60m'],600)
        self.assertAlmostEqual(result['profiles']['1m']['atr_pct'],4)

    def test_new_listing_has_no_fabricated_five_minute_atr(self):
        result=analyze_history(minute_history(6000000,30),6000000)
        self.assertIsNotNone(result['profiles']['1m'])
        self.assertIsNone(result['profiles']['5m'])

    def test_gap_truncates_warmup_and_latest_gap_is_rejected(self):
        end=6000000;rows=minute_history(end)
        del rows[10]
        self.assertIsNone(analyze_history(rows,end)['profiles']['1m'])
        with self.assertRaises(ValueError):analyze_history(rows[1:],end)

    def test_cache_bootstraps_then_fetches_only_missing_minutes(self):
        cfg=dict(market='eea_xperp',okx_api_base='https://example.test');end=18000000
        calls=[]
        def api(cfg,path,**params):
            calls.append(params)
            return minute_history(end if len(calls)==1 else end+60000,int(params['limit']))
        rows=fetch_minutes(cfg,'BTC-USD_UM_XPERP-310404',end,api)
        self.assertEqual(len(rows),300)
        fetch_minutes(cfg,'BTC-USD_UM_XPERP-310404',end,api)
        self.assertEqual(len(calls),1)
        updated=fetch_minutes(cfg,'BTC-USD_UM_XPERP-310404',end+60000,api)
        self.assertEqual(calls[1]['limit'],'8')
        self.assertEqual(len(updated),300)
        self.assertEqual(analyze_history(updated,end+60000)['turnover_5m'],50)

    def test_eea_identity_excludes_swap_tradfi_and_dated_futures(self):
        good=dict(instId='BTC-USD_UM_XPERP-310404',instType='FUTURES',ruleType='xperp',
                  instCategory='1',settleCcy='USD',ctType='linear',state='live')
        self.assertTrue(eligible_instrument(good,'eea_xperp'))
        for key,value in [('instCategory','3'),('instCategory',''),('ruleType','normal'),
                          ('ruleType','pre_market'),('instType','SWAP'),('settleCcy','USDT')]:
            self.assertFalse(eligible_instrument({**good,key:value},'eea_xperp'))
        self.assertEqual(instrument_url(good['instId'],'eea_xperp'),
                         'https://my.okx.com/de/trade-futures/btc-usd_um_xperp-5yearly')

    def test_chart_urls_use_web_alias_without_changing_api_identity(self):
        self.assertEqual(instrument_url('SOL-USD_UM_XPERP-310404','eea_xperp'),
                         'https://my.okx.com/de/trade-futures/sol-usd_um_xperp-5yearly')
        self.assertEqual(instrument_url('BTC-USDT-SWAP','usdt_swap'),
                         'https://www.okx.com/trade-swap/btc-usdt-swap')
        for symbol in ('BTC-USDT-SWAP','BTC-USD_UM_XPERP','BTC-USD_UM_XPERP-not-a-date'):
            with self.assertRaises(ValueError):instrument_url(symbol,'eea_xperp')

    def test_eea_quote_volume_and_exact_link_never_use_swap_market(self):
        symbol='BTC-USD_UM_XPERP-310404'
        cfg=dict(market='eea_xperp',okx_api_base='https://example.test',
                 selection=dict(max_pairs=1,min_24h_usdt=0,min_5m_usdt=0,max_spread_pct=.15))
        requests=[]
        def api(cfg,path,**params):
            requests.append(params)
            if 'instruments' in path:
                return [dict(instId=symbol,instType='FUTURES',ruleType='xperp',instCategory='1',
                             settleCcy='USD',ctType='linear',state='live')]
            if 'tickers' in path:
                return [dict(instId=symbol,last='100',bidPx='100',askPx='100.01',volCcy24h='100')]
            return minute_history(18000000)
        with patch('discovery.time.time',return_value=18020):
            result=discover(cfg,api)
        self.assertEqual(result[0]['quote_ccy'],'USD')
        self.assertEqual(result[0]['turnover_5m_usdt'],50)
        self.assertEqual(requests[0]['instType'],'FUTURES')
        self.assertEqual(requests[1]['instType'],'FUTURES')
        self.assertEqual(requests[2]['instId'],symbol)
        self.assertTrue(result[0]['chart_url'].endswith('btc-usd_um_xperp-5yearly'))
        self.assertEqual(result[0]['symbol'],symbol)

    def test_pair_limit_keeps_different_leaders_for_each_timeframe(self):
        symbols=['A-USDT-SWAP','B-USDT-SWAP']
        cfg=dict(market='usdt_swap',selection=dict(max_pairs=1,max_spread_pct=.15))
        def api(cfg,path,**params):
            if 'instruments' in path:
                return [dict(instId=s,instType='SWAP',state='live',instCategory='1') for s in symbols]
            if 'tickers' in path:
                return [dict(instId=s,last='100',bidPx='100',askPx='100.01',volCcy24h='100') for s in symbols]
            if params['instId'].startswith('A'):
                return [[str(i*60000),'100','103','97','100','1','1','10','1'] for i in range(300)]
            rows=[];previous=100
            for i in range(300):
                close=[100,105,110,105,100][i%5]
                rows.append([str(i*60000),str(previous),str(max(previous,close)+.05),
                             str(min(previous,close)-.05),str(close),'1','1','10','1'])
                previous=close
            return rows
        with patch('discovery.time.time',return_value=18020):result=discover(cfg,api)
        self.assertEqual(len(result),2)
        by_symbol={p['symbol']:p for p in result}
        self.assertEqual(by_symbol['A-USDT-SWAP']['profiles']['1m']['rank'],1)
        self.assertEqual(by_symbol['B-USDT-SWAP']['profiles']['5m']['rank'],1)


if __name__=='__main__':unittest.main()
