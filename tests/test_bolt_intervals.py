import unittest
from datetime import date,timedelta
from scripts.research_bolt_intervals import scale,adjustment


class IntervalTests(unittest.TestCase):
    def test_future_quotes_do_not_change_scale(self):
        day=date(2026,1,1)
        history={day-timedelta(days=i):1+i*.001 for i in range(21)}
        before=scale(history,day,30)
        history[day+timedelta(days=1)]=100
        self.assertEqual(before,scale(history,day,30))
        self.assertGreater(before,0)

    def test_only_mature_recent_results_enter_calibration(self):
        day=date(2026,1,1)
        records=[dict(target=day-timedelta(days=i+1),horizon=30,score=.1) for i in range(40)]
        before=adjustment(records,day,30)
        self.assertEqual(before,(.1,40))
        for offset in (0,1,-91):
            records.append(dict(target=day+timedelta(days=offset),horizon=30,score=100))
        self.assertEqual(before,adjustment(records,day,30))
        self.assertEqual(adjustment(records,day,7),(None,0))


if __name__=='__main__':unittest.main()
