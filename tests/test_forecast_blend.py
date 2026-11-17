import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app import forecast_blend


class QuoteAgesTests(unittest.TestCase):
    def test_string_quote_dates_convert_to_day_ages(self):
        class FakeMarket:
            def known(self, source, day):
                lag = 1 if source == 'ecb' else 2  # ecb 停 1 天、cbc 停 2 天
                return {'quote_date': str(day - timedelta(days=lag))}
        day = date(2026, 11, 16)
        ages = forecast_blend.quote_ages(FakeMarket(), ('ecb', 'cbc'), [day, day - timedelta(days=1)])
        self.assertEqual(ages[day], 2)
        self.assertEqual(ages[day - timedelta(days=1)], 2)

    def test_missing_source_day_disables(self):
        class FakeMarket:
            def known(self, source, day):
                return None
        self.assertIsNone(forecast_blend.quote_ages(FakeMarket(), ('ecb',), [date(2026, 11, 16)]))


class ForecastBlendTests(unittest.TestCase):
    def test_disabled_without_models_or_unsupported_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            instance = Path(tmp)
            entries = [(date(2026, 11, 16) - timedelta(days=i), 1 + i * .0001) for i in range(60)]
            self.assertIsNone(forecast_blend.make_directions(instance, 'TWD', 'HKD', entries))
            (instance / 'models').mkdir()
            for name in forecast_blend.MODEL_FILES:
                (instance / 'models' / name).write_bytes(b'x')
            self.assertIsNone(forecast_blend.make_directions(instance, 'USD', 'CHF', entries))
            # 停用標記存在 → 即使模型齊全也不啟用。
            (instance / 'models' / 'blend-disabled').write_bytes(b'')
            self.assertTrue(forecast_blend.disabled(instance))
            self.assertFalse(forecast_blend.available(instance))
            self.assertIsNone(forecast_blend.make_directions(instance, 'TWD', 'HKD', entries))
            (instance / 'models' / 'blend-disabled').unlink()
            # 模型存在、幣對支援 → 回呼建立（推論延遲到呼叫時）。
            callback = forecast_blend.make_directions(instance, 'TWD', 'HKD', entries)
            self.assertTrue(callable(callback))
            # 模型檔無效 → 推論失敗時回傳 None，不丟例外。
            self.assertIsNone(callback(date(2026, 11, 16), 1, 7))
            self.assertIsNone(callback(date(2026, 11, 16), 1, 3))


if __name__ == '__main__':
    unittest.main()
