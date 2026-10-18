import unittest
from unittest.mock import patch

from discovery import MINUTE_CACHE
from scanner import CANDLE_CACHE, Pair, find_pairs, get_bars, recent_turnover


def minute_rows(end, volume):
    rows = [[str(end-i*60000), '10', '11', '9', '10', '1', '1', str(volume), '1']
            for i in range(1, 6)]
    rows.insert(0, [str(end), '10', '11', '9', '10', '1', '1', '99999999', '0'])
    return rows


def history_rows(end, volume):
    return [[str(end-i*60000), '10', '11', '9', '10', '1', '1', str(volume), '1']
            for i in range(1,31)]


class SelectionTests(unittest.TestCase):
    def tearDown(self):
        CANDLE_CACHE.clear()
        MINUTE_CACHE.clear()

    def test_only_explicit_crypto_perpetuals_are_measured(self):
        cfg=dict(market='usdt_swap',selection=dict(max_pairs=400,min_24h_usdt=0,max_spread_pct=.15))
        instruments=[dict(instId='BTC-USDT-SWAP',state='live',instType='SWAP',instCategory='1')]
        for name,category in [('TSLA','3'),('XAU','4'),('EUR','5'),('BONDS','6'),
                              ('INDEX','7'),('UNKNOWN',''),('FUTURE_CATEGORY','99')]:
            instruments.append(dict(instId=name+'-USDT-SWAP',state='live',instType='SWAP',instCategory=category))
        instruments += [dict(instId='MISSING-USDT-SWAP',state='live',instType='SWAP'),
                        dict(instId='WRONGTYPE-USDT-SWAP',state='live',instType='SPOT',instCategory='1'),
                        dict(instId='SUSPENDED-USDT-SWAP',state='suspend',instType='SWAP',instCategory='1')]
        measured=[]
        def api(cfg,path,**params):
            if 'instruments' in path:return instruments
            if 'tickers' in path:
                return [dict(instId=i['instId'],last='10',bidPx='10',askPx='10.001',volCcy24h='1000') for i in instruments]
            measured.append(params['instId'])
            return history_rows(3600000,100)
        with patch('scanner.okx_api',side_effect=api),patch('scanner.time.time',return_value=3603):
            pairs=find_pairs(cfg)
        self.assertEqual([p.symbol for p in pairs],['BTC-USDT-SWAP'])
        self.assertEqual(measured,['BTC-USDT-SWAP'])

    def test_rolling_window_ignores_open_and_old_candles(self):
        rows = minute_rows(3600000, 12)
        rows.append(['0', '10', '11', '9', '10', '1', '1', '999999', '1'])
        rows.append(rows[1])
        self.assertEqual(recent_turnover(rows, 3600000), 60)

    def test_incomplete_or_stale_window_is_not_ranked(self):
        rows = minute_rows(3600000, 12)
        self.assertIsNone(recent_turnover(rows[:-1], 3600000))
        self.assertIsNone(recent_turnover(rows, 3660000))
        rows[1][7] = 'nan'
        self.assertIsNone(recent_turnover(rows, 3600000))

    def test_newly_active_coin_beats_24h_leader(self):
        cfg = dict(market='usdt_swap',selection=dict(max_pairs=1, min_24h_usdt=0, max_spread_pct=.15))
        def api(cfg, path, **params):
            if 'instruments' in path:
                return [dict(instId=s, state='live', instType='SWAP', instCategory='1') for s in ('OLD-USDT-SWAP', 'NEW-USDT-SWAP')]
            if 'tickers' in path:
                return [dict(instId=s, last='10', bidPx='10', askPx='10.001', volCcy24h=v)
                        for s, v in [('OLD-USDT-SWAP', '99999999'), ('NEW-USDT-SWAP', '100')]]
            return history_rows(3600000, 100 if params['instId'].startswith('NEW') else 1)
        with patch('scanner.okx_api', side_effect=api), patch('scanner.time.time', return_value=3603):
            pairs = find_pairs(cfg)
        self.assertEqual([p.symbol for p in pairs], ['NEW-USDT-SWAP'])
        self.assertEqual(pairs[0].turnover_5m_usdt, 500)

    def test_volatility_ranks_before_turnover_and_before_pair_limit(self):
        cfg=dict(market='usdt_swap',selection=dict(max_pairs=1,min_24h_usdt=0,max_spread_pct=.15))
        def api(cfg,path,**params):
            if 'instruments' in path:
                return [dict(instId=s,state='live',instType='SWAP',instCategory='1') for s in ('QUIET-USDT-SWAP','ACTIVE-USDT-SWAP')]
            if 'tickers' in path:
                return [dict(instId=s,last='10',bidPx='10',askPx='10.001',volCcy24h='1000')
                        for s in ('QUIET-USDT-SWAP','ACTIVE-USDT-SWAP')]
            active=params['instId'].startswith('ACTIVE')
            candles=history_rows(3600000,10 if active else 100000)
            for candle in candles:
                candle[2]='11' if active else '10.01'
                candle[3]='9' if active else '9.99'
            return candles
        with patch('scanner.okx_api',side_effect=api),patch('scanner.time.time',return_value=3603):
            found=find_pairs(cfg)
        self.assertEqual(found[0].symbol,'ACTIVE-USDT-SWAP')
        self.assertAlmostEqual(found[0].volatility_1m_pct,20)
        self.assertEqual(found[0].turnover_5m_usdt,50)

    def test_candle_cache_avoids_polling_and_fetches_only_new_bars(self):
        cfg = dict(okx_api_base='https://example.test', strategy=dict(timeframe='5m'))
        rows = [[str(ts), '10', '11', '9', '10', '1', '1', '1', '1']
                for ts in (2700000, 3000000, 3300000)]
        with patch('scanner.time.time', return_value=3604), patch('scanner.okx_api', return_value=rows) as api:
            first = get_bars(cfg, 'BTC-USDT-SWAP')
            self.assertEqual(get_bars(cfg, 'BTC-USDT-SWAP'), first)
            self.assertEqual(api.call_count, 1)
            self.assertEqual(api.call_args.kwargs['limit'], '150')
        with patch('scanner.time.time', return_value=3904), patch('scanner.okx_api',
                return_value=[['3600000', '10', '11', '9', '10', '1', '1', '1', '1']]) as api:
            bars = get_bars(cfg, 'BTC-USDT-SWAP')
            self.assertEqual(api.call_args.kwargs['limit'], '4')
            self.assertEqual(len(bars), 4)

    def test_400_limit_applies_after_measuring_all_candidates(self):
        cfg = dict(market='usdt_swap',selection=dict(max_pairs=400, min_24h_usdt=0, max_spread_pct=.15))
        symbols = [f'C{i}-USDT-SWAP' for i in range(405)]
        def api(cfg, path, **params):
            if 'instruments' in path:
                return [dict(instId=s, state='live', instType='SWAP', instCategory='1') for s in symbols]
            if 'tickers' in path:
                return [dict(instId=s, last='10', bidPx='10', askPx='10', volCcy24h='1') for s in symbols]
            return history_rows(3600000, int(params['instId'].split('-')[0][1:])+1)
        with patch('scanner.okx_api', side_effect=api) as mocked, patch('scanner.time.time', return_value=3603):
            pairs = find_pairs(cfg)
        self.assertEqual(len(pairs), 400)
        self.assertEqual(pairs[0].symbol, 'C404-USDT-SWAP')
        self.assertEqual(mocked.call_count, 407)

    def test_stale_signal_candles_are_rejected(self):
        cfg = dict(okx_api_base='https://example.test', strategy=dict(timeframe='5m'))
        with patch('scanner.time.time', return_value=3604), patch('scanner.okx_api',
                return_value=[['3000000', '10', '11', '9', '10', '1', '1', '1', '1']]):
            with self.assertRaisesRegex(RuntimeError, 'Veraltete'):
                get_bars(cfg, 'BTC-USDT-SWAP')


if __name__ == '__main__':
    unittest.main()
