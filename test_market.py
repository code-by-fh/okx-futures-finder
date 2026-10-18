import copy
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from dashboard import Dashboard
from market import RankingHistory, window_metrics
from scanner import Pair, loop
from scanner import read_config


def rows():
    return [[str(i*60000), str(100+i), str(103+i), str(99+i), str(101+i), '1', '1', '10', '1']
            for i in range(5)]


class MarketTests(unittest.TestCase):
    def test_metrics_use_exactly_the_completed_window(self):
        bars = rows()
        bars.append(['300000','999','1000','998','999','1','1','100000','0'])
        bars.append(['-60000','1','999','1','999','1','1','100000','1'])
        result = window_metrics(bars, 300000)
        self.assertEqual(result['price'], 105)
        self.assertAlmostEqual(result['change_5m_pct'], 5)
        self.assertAlmostEqual(result['range_5m_pct'], 8)
        self.assertEqual(result['trend'], (100,101,102,103,104,105))
        self.assertAlmostEqual(result['volatility_1m_pct'],sum(4/(100+i)*100 for i in range(5))/5)
        self.assertAlmostEqual(result['range_1m_pct'],4/104*100)
        self.assertAlmostEqual(result['change_1m_pct'],1/104*100)

    def test_back_and_forth_movement_is_visible_even_with_zero_net_change(self):
        bars=[[str(i*60000),'100','103','97','100','1','1','1000','1'] for i in range(5)]
        metrics=window_metrics(bars,300000)
        self.assertEqual(metrics['change_5m_pct'],0)
        self.assertEqual(metrics['volatility_1m_pct'],6)
        self.assertEqual(metrics['range_1m_pct'],6)

    def test_missing_invalid_and_unconfirmed_prices_are_not_invented(self):
        for column,value in [(1,'nan'),(2,'100'),(3,'999'),(8,'0')]:
            bars=rows();bars[2][column]=value
            self.assertEqual(window_metrics(bars,300000),{})
        self.assertEqual(window_metrics(rows()[:-1],300000),{})

    def test_ranking_change_uses_previous_distinct_snapshot(self):
        history=RankingHistory()
        first=history.decorate([Pair('A',1,0,100,300000),Pair('B',1,0,50,300000)])
        self.assertIsNone(first[0]['rank_change'])
        same=history.decorate([Pair('A',1,0,100,300000),Pair('B',1,0,50,300000)])
        self.assertIsNone(same[0]['turnover_change'])
        second=history.decorate([Pair('B',1,0,150,360000),Pair('C',1,0,120,360000),Pair('A',1,0,90,360000)])
        self.assertEqual(second[0]['rank_change'],1)
        self.assertEqual(second[0]['turnover_change'],200)
        self.assertIsNone(second[1]['rank_change'])
        self.assertEqual(second[2]['rank_change'],-2)

    def test_market_only_never_fetches_signal_history_or_touches_pending_delivery(self):
        cfg=dict(strategy=dict(timeframe='5m'),runtime=dict(signals_enabled=False,poll_seconds=30,refresh_pairs_minutes=1),notifications=dict(summary_seconds=120))
        state=dict(last_seen={},sent=[],pending=[dict(symbol='A',kind='BULL_SWEEP',time='old')])
        original=copy.deepcopy(state)
        dashboard=Dashboard(Path('.'))
        with patch('scanner.read_state',return_value=state),patch('scanner.find_pairs',return_value=[Pair('A',1,0,100,300000)]), \
                patch('scanner.get_bars') as fetch,patch('scanner.process_symbol') as detect, \
                patch('scanner.flush_summary') as push,patch('scanner.save_state') as save:
            loop(cfg,once=True,dashboard=dashboard)
        fetch.assert_not_called();detect.assert_not_called();push.assert_not_called();save.assert_not_called()
        self.assertEqual(state,original)
        self.assertEqual(dashboard.data['pairs'][0]['turnover_5m'],100)
        self.assertFalse(dashboard.data['signals_enabled'])

    def test_errors_are_bounded_and_timestamped(self):
        dashboard=Dashboard(Path('.'))
        for i in range(60):dashboard.report_error('Test',str(i),'BTC')
        entries=dashboard.snapshot()['error_log']
        self.assertEqual(len(entries),50)
        self.assertEqual(entries[0]['message'],'10')
        self.assertEqual(entries[-1]['symbol'],'BTC')
        self.assertIn('server_time',dashboard.snapshot())

    def test_minimal_config_needs_no_strategy_or_push_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'config.json'
            path.write_text(json.dumps(dict(okx_api_base='https://example.test')),encoding='utf-8')
            with patch('scanner.CONFIG_FILE',path):
                cfg=read_config()
            self.assertFalse(cfg['runtime']['signals_enabled'])
            self.assertNotIn('pushover',cfg)
            self.assertNotIn('strategy',cfg)


if __name__=='__main__':
    unittest.main()
