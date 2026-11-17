import os
import tempfile
import time
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from app import scheduler
from app.forecast_blend import MODEL_FILES
from scripts.train_blend_models import partition_rows


def rows():
    start = date(2026, 1, 1)
    return [dict(pair='TWD-HKD', origin=start + timedelta(days=i), horizon=7, label=1, x=[0.0] * 18)
            for i in range(6)]


class TrainBlendTests(unittest.TestCase):
    def test_partition_rows_splits_on_holdout_start(self):
        holdout_start = date(2026, 1, 4)
        train, holdout = partition_rows(rows(), holdout_start)
        self.assertEqual([r['origin'] for r in train], [date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)])
        self.assertEqual([r['origin'] for r in holdout], [date(2026, 1, 4), date(2026, 1, 5), date(2026, 1, 6)])

    def test_blend_models_stale_needs_recent_pair(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.assertTrue(scheduler.blend_models_stale(root))
            folder = root / 'instance' / 'models'
            folder.mkdir(parents=True)
            old = time.time() - (scheduler.BLEND_STALE_DAYS + 1) * 86400
            for name in MODEL_FILES:
                path = folder / name
                path.write_bytes(b'x')
                os.utime(path, (old, old))
            self.assertTrue(scheduler.blend_models_stale(root))
            for name in MODEL_FILES:
                (folder / name).write_bytes(b'x')
            self.assertFalse(scheduler.blend_models_stale(root))

    def test_check_blend_training_survives_check_failure(self):
        old = scheduler._blend_process
        try:
            with patch.object(scheduler, 'blend_models_stale', side_effect=OSError('disk')):
                scheduler.check_blend_training()  # 不得拋出例外
            self.assertIsNone(scheduler._blend_process)
        finally:
            scheduler._blend_process = old

    def test_check_blend_training_launches_once_and_skips_when_running(self):
        old = scheduler._blend_process
        try:
            with patch.object(scheduler, 'blend_models_stale', return_value=True), \
                 patch.object(scheduler.subprocess, 'Popen') as popen:
                process = Mock()
                process.poll.return_value = None
                popen.return_value = process
                scheduler.check_blend_training()
                scheduler.check_blend_training()
                self.assertEqual(popen.call_count, 1)
                self.assertIs(scheduler._blend_process, process)
        finally:
            scheduler._blend_process = old

    def test_check_blend_training_skips_when_fresh(self):
        old = scheduler._blend_process
        try:
            with patch.object(scheduler, 'blend_models_stale', return_value=False), \
                 patch.object(scheduler.subprocess, 'Popen') as popen:
                scheduler.check_blend_training()
                popen.assert_not_called()
        finally:
            scheduler._blend_process = old

    def test_run_scheduler_survives_job_exception(self):
        sleeps = []
        def fake_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise KeyboardInterrupt  # 跳出 while True
        with patch.object(scheduler.schedule, 'run_pending', side_effect=RuntimeError('job 爆炸')), \
             patch.object(scheduler.time, 'sleep', fake_sleep), \
             self.assertLogs('app.scheduler', level='ERROR'):
            with self.assertRaises(KeyboardInterrupt):
                scheduler.run_scheduler()
        self.assertEqual(len(sleeps), 2)


if __name__ == '__main__':
    unittest.main()
