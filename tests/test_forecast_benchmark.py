import unittest
import json
import tempfile
import time
import io
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
from datetime import date, timedelta

from scripts.benchmark_forecasts import PAIRS, benchmark, evaluate_candidate, inputs_for


class BenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2025, 11, 6)
        self.end = self.start + timedelta(days=364)
        self.holdout = self.end - timedelta(days=89)
        self.entries = [(self.start + timedelta(days=i), 100 + i / 10) for i in range(365)]

    @staticmethod
    def predict(inputs):
        return [[[values[-1] * .99, values[-1], values[-1] * 1.01] for _ in range(30)] for values in inputs]

    def test_calendar_lengths_and_common_origins(self):
        expected = None
        for window in (90, 180, 270):
            origins, inputs = inputs_for(self.entries, self.start, self.holdout, window)
            self.assertEqual(len(inputs[0]), 90)
            self.assertEqual(len(inputs[-1]), window)
            if expected is not None:
                self.assertEqual(origins, expected)
            expected = origins

    def test_development_never_sees_holdout(self):
        calls = []
        def predict(inputs):
            calls.extend(inputs)
            return self.predict(inputs)
        original = evaluate_candidate(self.entries, predict, self.start, self.holdout, self.end, 180, 'development')
        changed = [(d, r if d < self.holdout else r * 1000) for d, r in self.entries]
        other = evaluate_candidate(changed, self.predict, self.start, self.holdout, self.end, 180, 'development')
        self.assertEqual(original, other)
        self.assertLess(max(max(x) for x in calls), 128)
        for h in (7, 14, 30):
            last = date.fromisoformat(original['metrics'][str(h)]['raw']['origins'][-1])
            self.assertLess(last + timedelta(days=h), self.holdout)

    def test_long_window_reports_scored_lengths(self):
        result = evaluate_candidate(self.entries, self.predict, self.start, self.holdout, self.end, 270, 'development')
        for h in (7, 14, 30):
            context = result['metrics'][str(h)]['raw']['input_context']
            self.assertEqual(context['full_window_samples'], 0)
            self.assertEqual(context['max_days'], 275 - h)

    def test_holdout_targets_and_classification(self):
        result = evaluate_candidate(self.entries, self.predict, self.start, self.holdout, self.end, 90, 'holdout')
        for h in (7, 14, 30):
            groups = result['metrics'][str(h)]
            self.assertEqual(groups['raw']['samples'], 90 - h)
            self.assertEqual(groups['raw']['classification']['samples'], 90 - h)
            self.assertEqual(groups['raw']['origins'][0], str(self.holdout))
            self.assertEqual(groups['cumulative']['origins'], groups['rolling90']['origins'])

    def test_calibration_waits_for_realized_targets(self):
        result = evaluate_candidate(self.entries, self.predict, self.start, self.holdout, self.end, 90, 'development')
        for h in (7, 14, 30):
            first = date.fromisoformat(result['metrics'][str(h)]['cumulative']['origins'][0])
            self.assertEqual(first, self.start + timedelta(days=89 + h + 19))

    def test_full_report_and_selection_lock(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as directory:
            destination = Path(directory)
            (destination / 'history').mkdir()
            data = {str(d): {'rate': r} for d, r in self.entries}
            for pair in PAIRS:
                (destination / 'history' / f'{pair}.json').write_text(json.dumps(data))
                (destination / 'history' / f'{pair}-status.json').write_text('{}')
            manifest = {'start': str(self.start), 'holdout_start': str(self.holdout),
                        'started': time.time(), 'deadline': time.time() + 60}
            with patch('scripts.benchmark_forecasts.Predictor', return_value=self.predict), \
                 patch('torch.cuda.get_device_name', return_value='test'), \
                 patch('torch.cuda.empty_cache'), redirect_stdout(io.StringIO()):
                benchmark(destination, self.end, manifest)
            result = json.loads((destination / 'result.json').read_text())
            self.assertFalse(result['eligible_for_replacement'])
            self.assertEqual(result['improvement'], 0)
            self.assertTrue((destination / 'selection-lock.json').exists())
            self.assertEqual(len(list((destination / 'development').glob('*/*.json'))), 36)
            self.assertEqual(len(list((destination / 'holdout').glob('*/*.json'))), 12)
            self.assertIn('accuracy,precision,recall,f1', (destination / 'metrics.csv').read_text(encoding='utf-8-sig'))


if __name__ == '__main__':
    unittest.main()
