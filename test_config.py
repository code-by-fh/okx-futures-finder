import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scanner import read_config


class ContainerConfigTests(unittest.TestCase):
    def load(self, values):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'custom.json'
            config.write_text(json.dumps({'okx_api_base': 'https://eea.okx.com'}))
            with patch.dict(os.environ, {'OKX_CONFIG_FILE': str(config), **values}, clear=True):
                return read_config()

    def test_environment_overrides_config_with_typed_values(self):
        cfg = self.load({'OKX_MARKET': 'usdt_swap', 'OKX_API_BASE': 'https://www.okx.com',
                         'PORT': '9000', 'DASHBOARD_HOST': '0.0.0.0', 'MAX_PAIRS': '25',
                         'MAX_SPREAD_PCT': '0.2', 'REFRESH_MINUTES': '2',
                         'MIN_5M_TURNOVER': '1000', 'MIN_24H_TURNOVER': '5000'})
        self.assertEqual(cfg['market'], 'usdt_swap')
        self.assertEqual(cfg['okx_api_base'], 'https://www.okx.com')
        self.assertEqual(cfg['dashboard']['port'], 9000)
        self.assertEqual(cfg['dashboard']['host'], '0.0.0.0')
        self.assertEqual(cfg['selection'], dict(max_pairs=25, max_spread_pct=.2,
                                               min_5m_usdt=1000., min_24h_usdt=5000.))
        self.assertEqual(cfg['runtime']['refresh_pairs_minutes'], 2.)
        self.assertFalse(cfg['runtime']['signals_enabled'])

    def test_invalid_environment_is_rejected(self):
        for values in ({'PORT': 'oops'}, {'PORT': '0'}, {'PORT': '65536'},
                       {'DASHBOARD_HOST': ' '}, {'REFRESH_MINUTES': 'nan'},
                       {'MIN_5M_TURNOVER': '-1'}, {'MAX_PAIRS': '1.5'}):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.load(values)

    def test_local_default_remains_loopback(self):
        self.assertEqual(self.load({})['dashboard']['host'], '127.0.0.1')
