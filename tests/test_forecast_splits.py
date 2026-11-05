import copy
from datetime import date,timedelta
import unittest
from unittest.mock import patch
import numpy as np
from app import forecast_splits as s
from app import forecast_supervised as supervised
from scripts.research_nested import tuning,calibrated


class SplitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.start=date(2025,11,5);cls.end=date(2026,11,4)
        cls.rows=[dict(pair=p,origin=cls.start+timedelta(days=i),target=cls.start+timedelta(days=i+h),horizon=h,
                       base=1,actual=1.01,label=2,y=.01,x=[i,h]) for p in supervised.PAIRS for i in range(60,365)
                  for h in range(1,31) if i+h<365]

    def test_exact_dates_counts_and_no_future_targets(self):
        folds=s.folds(self.start,self.end)
        self.assertEqual([str(f['test']) for f in folds],['2026-07-08','2026-08-07','2026-09-06'])
        self.assertEqual([str(f['calibration']) for f in folds],['2026-05-09','2026-06-08','2026-07-08'])
        for i,f in enumerate(folds):
            train=s.select(self.rows,f,'train');cal=s.select(self.rows,f,'calibration');test=s.select(self.rows,f,'test')
            self.assertTrue(all(r['target']<f['calibration'] and r['pair'] in s.CORE for r in train))
            self.assertTrue(all(f['calibration']<=r['origin'] and r['target']<f['test'] and r['pair'] in s.CORE for r in cal))
            self.assertEqual(s.calibration_counts(train,30)['dates'],95+30*i)
            self.assertEqual(s.calibration_counts(cal,30),dict(samples=120,dates=30))
            s.validate_test(test,f,supervised.PAIRS)
            self.assertEqual(len(test),5400)
            for inner in range(2):
                tr=s.select(self.rows,f,'inner_train',inner);v=s.select(self.rows,f,'inner_validation',inner)
                self.assertTrue(all(r['target']<f['inner'][inner]['start'] for r in tr))
                self.assertEqual(len({r['origin'] for r in v}),15)
                self.assertTrue(all(r['target']<f['calibration'] for r in v))

    def test_same_origin_short_label_can_be_mature_while_long_is_not(self):
        f=s.folds(self.start,self.end)[0]
        origin=f['calibration']-timedelta(days=10)
        tr=s.select(self.rows,f,'train')
        self.assertTrue(any(r['origin']==origin and r['horizon']==7 for r in tr))
        self.assertFalse(any(r['origin']==origin and r['horizon']==30 for r in tr))

    def test_missing_test_and_bad_roles_fail(self):
        f=s.folds(self.start,self.end)[0];test=s.select(self.rows,f,'test')
        with self.assertRaises(ValueError):s.validate_test(test[:-1],f,supervised.PAIRS)
        with self.assertRaises(ValueError):s.validate_test(test+[test[0]],f,supervised.PAIRS)
        with self.assertRaises(ValueError):s.select(self.rows,f,'invalid')
        with self.assertRaises(ValueError):s.stage_summary(test,f['test'])

    def test_test_answers_cannot_change_selection_or_fit_data(self):
        f=s.folds(self.start,self.end)[0]
        changed=[dict(r,label=0,actual=10,y=9,x=[999,999]) if r['origin']>=f['test'] else r for r in self.rows]
        def probs(model,rows):return np.tile([.1,.2,.7],(len(rows),1))
        def price(model,rows):return np.tile([.9,1,1.1],(len(rows),1)),np.zeros(len(rows),dtype=bool)
        def train(name,rows):
            self.assertTrue(all(r['target']<f['calibration'] for r in rows))
            return name
        with patch.object(supervised,'train_classifier',side_effect=train),patch.object(supervised,'probabilities',side_effect=probs), \
                patch.object(supervised,'train_price',side_effect=train),patch.object(supervised,'price_predictions',side_effect=price):
            before=tuning(self.rows,f,float('inf'));after=tuning(changed,f,float('inf'))
        self.assertEqual(before,after)

    def test_calibration_requires_unique_dates(self):
        cal=[dict(horizon=30,origin=self.start,actual=1)]*40
        q=np.tile([.9,1,1.1],(40,1))
        result,counts,adjustments=calibrated([dict(horizon=30)],np.array([[.9,1,1.1]]),cal,q)
        self.assertIsNone(adjustments[30]);self.assertTrue(np.isnan(result[0,0]))
        self.assertEqual(counts[30]['dates'],1)


if __name__=='__main__':unittest.main()
