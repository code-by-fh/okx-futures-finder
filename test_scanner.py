import unittest
from scanner import Candle, find_signals, parse_candles, timeframe_ms


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(pivot_left=1, pivot_right=1, bos_window_bars=8,
                        max_pivot_age_bars=50, min_sweep_depth_pct=0.02,
                        min_bos_break_pct=0)
        rows = [(100, 105, 99, 102),
                (102, 104, 95, 100),
                (100, 107, 99, 103),
                (103, 103, 98, 101),
                (101, 106, 94, 100),
                (100, 109, 99, 108)]
        self.bars = [Candle(300000*i, *row) for i, row in enumerate(rows)]

    def test_bull_sweep_and_bos(self):
        sig = find_signals(self.bars, self.cfg)
        self.assertEqual([s.kind for s in sig], ["BULL_SWEEP", "BULL_BOS"])
        self.assertEqual(sig[0].sweep_level, 95)
        self.assertEqual(sig[1].break_level, 107)

    def test_bear_sweep_and_bos(self):
        mirrored = [Candle(c.ts, 200-c.o, 200-c.l, 200-c.h, 200-c.c) for c in self.bars]
        sig = find_signals(mirrored, self.cfg)
        self.assertEqual([s.kind for s in sig], ["BEAR_SWEEP", "BEAR_BOS"])

    def test_no_bos_without_future_bar(self):
        sig = find_signals(self.bars[:-1], self.cfg)
        self.assertEqual([s.kind for s in sig], ["BULL_SWEEP"])

    def test_incomplete_candles_skipped(self):
        result = parse_candles([
            ["300000", "10", "11", "9", "10", "1", "1", "1", "0"],
            ["0", "10", "11", "9", "10", "1", "1", "1", "1"]])
        self.assertEqual([b.ts for b in result], [0])

    def test_timeframe(self):
        self.assertEqual(timeframe_ms("5m"), 300000)


if __name__ == "__main__":
    unittest.main()
