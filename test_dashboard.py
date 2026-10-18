import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.request import urlopen

from dashboard import Dashboard, HealthMonitor
from scanner import flush_summary


class DashboardTests(unittest.TestCase):
    def test_local_status_does_not_expose_config(self):
        root = Path(__file__).parent
        dashboard = Dashboard(root, 0)
        dashboard.start()
        try:
            base = f"http://127.0.0.1:{dashboard.server.server_port}"
            with urlopen(base + '/healthz') as response:
                self.assertEqual(json.load(response), {'status': 'ok'})
            with urlopen(base + "/api/status") as response:
                data = json.load(response)
            self.assertIn("pairs", data)
            self.assertNotIn("pushover", data)
            with urlopen(base) as response:
                self.assertIn(b"ATR(14)", response.read())
            for asset,content_type in [('/dashboard.css','text/css'),('/dashboard.js','text/javascript')]:
                with urlopen(base+asset) as response:
                    self.assertIn(content_type,response.headers['Content-Type'])
            from urllib.error import HTTPError
            for private in ('/config.json','/state.json','/../scanner.py'):
                with self.assertRaises(HTTPError) as error:
                    urlopen(base+private)
                self.assertEqual(error.exception.code,404)
        finally:
            dashboard.close()

    def test_outage_once_and_recovery(self):
        dashboard = Dashboard(Path('.'))
        dashboard.update(started=100)
        messages = []
        monitor = HealthMonitor(dashboard, 180, lambda *args: messages.append(args))
        monitor.check(279)
        self.assertEqual(messages, [])
        monitor.check(280)
        monitor.check(300)
        self.assertEqual(len(messages), 1)
        dashboard.update(last_success=301)
        monitor.check(302)
        monitor.check(303)
        self.assertEqual(len(messages), 2)
        self.assertIn("Entwarnung", messages[-1][0])

    def test_failed_outage_push_retries(self):
        dashboard = Dashboard(Path('.'))
        dashboard.update(started=100)
        with patch('scanner.push_notification', side_effect=[RuntimeError('offline'), None]) as push:
            monitor = HealthMonitor(dashboard, 180, push)
            monitor.check(280)
            self.assertFalse(monitor.reported)
            monitor.check(300)
            self.assertEqual(push.call_count, 1)
            monitor.check(340)
            self.assertTrue(monitor.reported)

    def test_summary_keeps_unsent_chunk_after_failure(self):
        events = [dict(symbol='BTC-USDT-SWAP', kind='BULL_BOS', close=100,
                       time='2026-10-14 12:00 UTC',
                       url='https://www.okx.com/trade-swap/btc-usdt-swap') for _ in range(15)]
        state = dict(pending=events, sent=[], last_seen={})
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'state.json'
            with patch('scanner.STATE_FILE', path), patch('scanner.push_notification',
                    side_effect=[None, RuntimeError('offline')]) as push:
                with self.assertRaises(RuntimeError):
                    flush_summary({}, state)
                self.assertGreater(len(state['pending']), 0)
                self.assertLess(len(state['pending']), 15)
                self.assertEqual(json.loads(path.read_text())['pending'], state['pending'])
                self.assertLessEqual(len(push.call_args_list[0].args[2].encode('utf-8')), 900)

    def test_finder_does_not_load_historical_signals(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            (root/'signals.csv').write_text('time_utc,symbol,event,close\nnow,BTC,BULL_SWEEP,100\nold,ETH,BEAR_BOS,50\n',encoding='utf-8')
            dashboard=Dashboard(root)
            dashboard.update(pending_signals=[dict(symbol='BTC',kind='BULL_SWEEP',time='now')])
            self.assertNotIn('signals',dashboard.snapshot())


if __name__ == '__main__':
    unittest.main()
