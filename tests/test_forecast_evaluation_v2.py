import unittest
from datetime import date,timedelta
from unittest.mock import patch
import numpy as np
from app import forecast_evaluation as e
from app import forecast_candidates as c
from app.forecast_supervised import PAIRS,HORIZONS


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(pair=p,origin=date(2026,1,1)+timedelta(days=i),horizon=h,base=1,
            actual=(.99,1,1.01)[i%3],label=i%3,x=[i,h,0,0,.01]) for p in PAIRS for h in HORIZONS for i in range(90)]
        self.ev=e.Evaluation(self.rows)
        self.q=np.tile([.9,1,1.1],(len(self.rows),1))

    def test_absent_class_primary_and_supplementary(self):
        m=e.classification(np.diag([2,0,2]))
        self.assertEqual(m['f1'],2/3);self.assertEqual(m['active_label_f1'],1)
        m=e.classification(np.array([[2,1,0],[0,0,0],[0,0,2]]))
        self.assertLess(m['active_label_f1'],1)

    def test_missing_interval_fields_are_null_and_common_mask(self):
        changed=self.q.copy();changed[0,[0,2]]=np.nan
        common,counts=e.common_intervals(dict(a=self.q,b=changed))
        self.assertEqual(counts['a']['excluded'],1)
        self.assertTrue(np.isnan(common['a'][0,0]))
        missing=self.q.copy();missing[:,[0,2]]=np.nan
        m=self.ev.price(missing)['EUR-USD/7']
        for field in ('coverage80','mean_width','interval_score80','normalized_width','normalized_interval_score','lower_miss_rate','upper_miss_rate'):
            self.assertIsNone(m[field])
        self.assertEqual(m['interval_samples'],0);self.assertEqual(m['samples'],90)
        bad=self.q.copy();bad[0,0]=np.nan
        with self.assertRaises(ValueError):e.common_intervals(dict(a=self.q,b=bad))
        bad[0,2]=float('inf')
        with self.assertRaises(ValueError):e.common_intervals(dict(a=self.q,b=bad))

    def test_hand_computed_interval_score(self):
        rows=[dict(r,actual=.8) for r in self.rows]
        m=e.Evaluation(rows).price(self.q)['EUR-USD/7']
        self.assertAlmostEqual(m['interval_score80'],1.2)
        self.assertAlmostEqual(m['normalized_interval_score'],1.5)

    def test_circular_date_incidence_equal(self):
        for length in (7,14,30):
            total=np.zeros(90)
            class Rng:
                def __init__(self,start):self.start=start
                def integers(self,low,high,size):return np.full(size,self.start)
            for start in range(90):total+=e.circular_counts(90,length,Rng(start))
            np.testing.assert_equal(total,np.full(90,90))

    def test_paired_identical_predictions_have_zero_difference(self):
        p=np.eye(3)[[r['label'] for r in self.rows]]
        result=e.uncertainty(self.ev,dict(bolt=p,copy=p),dict(bolt=self.q,copy=self.q),repetitions=3)
        for run in result.values():
            for key,interval in run['intervals95'].items():
                if 'copy-minus-bolt' in key:self.assertEqual(interval,[0,0])

    def test_distinct_engineering_and_dual_goals(self):
        cells=self.ev.price(self.q)
        for v in cells.values():v['coverage80']=.77
        engineering,dual=e.interval_checks(cells,cells)
        self.assertTrue(engineering['coverage_target']);self.assertFalse(dual['coverage_target'])

    def test_group_diagnostics_preserve_empty_cells(self):
        from scripts.evaluation_group_diagnostics import diagnostics
        result=diagnostics(self.rows,dict(bolt=self.q),[dict(dates=dict(test='2026-01-01',end='2026-01-31'))])
        self.assertEqual(result['1']['negative']['samples'],0)
        self.assertIsNone(result['1']['negative']['prices']['bolt']['EUR-USD/7']['coverage80'])
        self.assertEqual(result['1']['nonnegative']['prices']['bolt']['EUR-USD/7']['interval_samples'],30)


class CandidateTests(unittest.TestCase):
    def test_single_classes_and_probability_sum(self):
        for label in (0,1,2):
            rows=[dict(pair='EUR-USD',origin=date(2026,1,1),label=label,x=[1])]*10
            p=c.direction(c.train_direction(rows),rows)
            np.testing.assert_allclose(p.sum(axis=1),1)
            self.assertTrue(np.all(p.argmax(axis=1)==label))

    def test_training_and_future_inference_are_separate(self):
        rows=[dict(pair='EUR-USD',origin=date(2026,1,1)+timedelta(days=i),label=i%3,x=[i%4,i/10]) for i in range(60)]
        model=c.train_direction(rows);before=c.direction(model,[rows[0]])
        c.direction(model,[dict(rows[0],x=[1000,1000])])
        np.testing.assert_equal(before,c.direction(model,[rows[0]]))

    def test_group_fallback_and_no_heldout_answers(self):
        rows=[dict(pair='EUR-USD',origin=date(2026,1,1)+timedelta(days=i),horizon=30,base=1,actual=1.01,x=[0,0,0,0,.01]) for i in range(40)]
        query=[dict(rows[0],pair='AUD-USD')]
        q,a=c.bounds(query,[1],rows,np.ones(40),True)
        self.assertTrue(a[0]['group_weight']>0);self.assertTrue(0<q[0,0]<=q[0,1]<=q[0,2])
        changed=[dict(query[0],actual=100)]
        np.testing.assert_equal(q,c.bounds(changed,[1],rows,np.ones(40),True)[0])
        q,a=c.bounds(query,[.99],rows,np.ones(40),True)
        self.assertTrue(a[0]['fallback']);self.assertEqual(a[0]['group_weight'],0)
        with self.assertRaises(ValueError):c.bounds(query,[1],rows[:39],np.ones(39),True)


if __name__=='__main__':unittest.main()
