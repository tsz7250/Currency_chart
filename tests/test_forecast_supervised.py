import copy
import json
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from app import forecast_supervised as s
from app.forecast_market import MarketHistory
from app.forecast_shadow import ShadowStore
from app.forecast_supervised_shadow import SupervisedShadowStore, launch_supervised
from scripts.shadow_supervised import activate, daily


class SupervisedTests(unittest.TestCase):
    def setUp(self):
        self.start = date(2025,1,1)
        self.history = {str(self.start+timedelta(days=i)):dict(rate=1+i*.001) for i in range(365)}
        self.snapshot = {'sources':{'ecb':{'rows':[dict(quote_date=str(self.start+timedelta(days=i)),
            available_date=str(self.start+timedelta(days=i+2)),values={'EUR':1,'USD':1+i*.001}) for i in range(-430,365)]}}}

    def test_future_and_unpublished_values_do_not_change_features_or_predictions(self):
        origin = self.start+timedelta(days=180)
        original = s.features(self.history,MarketHistory(self.snapshot),'EUR-USD',origin)
        changed = copy.deepcopy(self.history)
        for d,v in changed.items():
            if d>str(origin):
                v['rate']*=2
        snapshot = copy.deepcopy(self.snapshot)
        for row in snapshot['sources']['ecb']['rows']:
            if row['available_date']>str(origin):
                row['values']['USD']*=3
        after = s.features(changed,MarketHistory(snapshot),'EUR-USD',origin)
        np.testing.assert_equal(original,after)
        rows = [dict(x=original+[h],label=i%3,pair='EUR-USD',origin=origin+timedelta(days=i)) for i,h in enumerate(range(1,31))]
        model=s.train_classifier('cat4',rows)
        np.testing.assert_equal(s.probabilities(model,[dict(x=original+[7])]),s.probabilities(model,[dict(x=after+[7])]))

    def test_date_purge_and_extra_pairs_excluded(self):
        rows=[dict(pair=p,origin=self.start+timedelta(days=i),target=self.start+timedelta(days=i+h),horizon=h)
              for p in s.PAIRS for i in range(60,335) for h in s.HORIZONS]
        for split in s.splits(self.start):
            for stage,next_stage in [('train','tune'),('tune','calibration'),('calibration','test')]:
                group=s.partition(rows,split,stage)
                self.assertTrue(group)
                self.assertTrue(all(r['target']<split[next_stage] and r['pair'] in s.PAIRS[:4] for r in group))
            self.assertEqual({r['pair'] for r in s.partition(rows,split,'test')},set(s.PAIRS))

    def test_weights_balance_origins_and_pairs(self):
        rows=[dict(pair=p,origin=d) for p,count in [('A',2),('B',3)] for d in range(count) for _ in range(d+1)]
        w=s.weights(rows)
        totals={p:sum(v for r,v in zip(rows,w) if r['pair']==p) for p in ('A','B')}
        self.assertAlmostEqual(totals['A'],totals['B'])
        self.assertAlmostEqual(sum(v for r,v in zip(rows,w) if r['pair']=='A' and r['origin']==0),
                               sum(v for r,v in zip(rows,w) if r['pair']=='A' and r['origin']==1))

    def test_missing_classes_probabilities_and_fixed_scores(self):
        rows=[dict(x=[i,7],label=i%2,pair='EUR-USD',origin=self.start+timedelta(days=i)) for i in range(30)]
        for name in ('flat','majority','logistic','cat4-balanced'):
            p=s.probabilities(s.train_classifier(name,rows),rows)
            np.testing.assert_allclose(p.sum(axis=1),1)
            m=s.direction_metrics(rows,p)
            self.assertEqual(m['per_class']['up']['support'],0)
            self.assertEqual(len(m['confusion_matrix']),3)
        for r in rows:
            r['label']=2
        self.assertEqual(s.train_classifier('cat6',rows),2)

    def test_crossings_calibration_and_missing_samples(self):
        class Model:
            def predict(self,x):
                return [[.1,-.1,0] for _ in x]
        rows=[dict(x=[1],base=100,actual=110,horizon=30) for _ in range(20)]
        q,c=s.price_predictions(Model(),rows)
        self.assertEqual(c.sum(),20)
        self.assertTrue((q>0).all() and (np.diff(q,axis=1)>=0).all())
        missing=s.calibration_adjustments(rows[:19],q[:19])
        self.assertIsNone(missing[30])
        adjustments=s.calibration_adjustments(rows,q)
        expanded=s.apply_calibration(rows,q,adjustments)
        self.assertTrue(np.all(expanded[:,0]<=q[:,0]) and np.all(expanded[:,2]>=q[:,2]))

    def test_independent_promotion_gates(self):
        reference={f'{p}/{h}':dict(f1=.3,accuracy=.5) for p in s.PAIRS for h in s.HORIZONS}
        candidate={k:dict(f1=.331,accuracy=.49) for k in reference}
        self.assertTrue(all(s.direction_checks(candidate,[reference,reference]).values()))
        candidate['EUR-USD/30']['f1']=0
        self.assertFalse(all(s.direction_checks(candidate,[reference,reference]).values()))
        with patch.object(s,'replacement_checks',return_value={'complete':True,'f1_no_worse':False,'price_5_percent':True}):
            self.assertEqual(s.price_checks({}, {}, {}),{'complete':True,'price_5_percent':True})

    def test_supervised_launch_failure_does_not_escape(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'forecast-supervised-shadow'
            folder.mkdir()
            (folder/'active.json').write_text('{}')
            with patch('app.forecast_supervised_shadow.subprocess.Popen',side_effect=OSError('test')), \
                    patch('app.forecast_supervised_shadow._process',None), \
                    self.assertLogs('app.forecast_supervised_shadow',level='ERROR'):
                self.assertFalse(launch_supervised(tmp))

    def test_joint_bootstrap_retains_correlated_pairs(self):
        rows=[dict(pair=p,origin=self.start+timedelta(days=i),horizon=h,label=i%3,base=1,actual=1,
                   target=self.start+timedelta(days=i+h)) for i in range(60) for p in s.PAIRS for h in s.HORIZONS]
        probs=np.tile([.2,.3,.5],(len(rows),1))
        real=s.direction_report
        def inspect(sample,p):
            for d in {r['origin'] for r in sample}:
                counts=[sum(r['origin']==d and r['pair']==pair for r in sample) for pair in s.PAIRS]
                self.assertEqual(len(set(counts)),1)
            return real(sample,p)
        with patch.object(s,'direction_report',side_effect=inspect):
            result=s.joint_uncertainty(rows,{'candidate':probs},{},repetitions=3)
        self.assertEqual(result['status'],'estimated')

    def test_shadow_legacy_v2_immutable_and_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'shadow.sqlite3'
            old=ShadowStore(path)
            new=SupervisedShadowStore(path)
            origin=date(2026,1,1)
            config=dict(pairs=['EUR-USD'])
            point=dict(low=1,median=2,high=3)
            payload=dict(issued_at='2026-01-01T10:00:00+08:00',pairs={'EUR-USD':dict(candidate=[point]*30,baseline=[point]*30)})
            run=old.register(config,origin)
            self.assertTrue(new.append(run,origin,payload))
            self.assertEqual(new.records(run),old.records(run))
            config.update(schema_version=2,direction_eligible=True,price_eligible=True,models={'direction.cbm':'digest'})
            run=new.register(config,origin)
            payload.update(schema_version=2,versions=config['models'])
            payload['pairs']['EUR-USD'].update(direction={str(h):[.2,.3,.5] for h in s.HORIZONS},quantiles=[[1,2,3]]*30)
            self.assertTrue(new.append(run,origin,payload))
            self.assertFalse(SupervisedShadowStore(path).append(run,origin,payload))
            before=new.records(run)
            payload['pairs']['EUR-USD']['direction']['7']=[1,1,1]
            with self.assertRaises(ValueError):
                new.append(run,origin,payload)
            self.assertEqual(before,new.records(run))

    def test_failed_gates_cannot_activate(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            (folder/'candidate-lock.json').write_text(json.dumps(dict(direction_eligible=False,price_eligible=False)))
            (folder/'manifest.json').write_text('{"status":"complete"}')
            (folder/'result.json').write_text(json.dumps(dict(status='complete',direction_checks={'pass':False},price_checks={'pass':False})))
            with self.assertRaises(ValueError):
                activate(folder/'shadow',folder)
            self.assertFalse((folder/'shadow/active.json').exists())
            self.assertFalse(launch_supervised(folder))

    def test_daily_restart_and_failure_preserve_snapshot(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            folder=Path(tmp)
            origin=self.start+timedelta(days=364)
            config=dict(schema_version=2,pairs=['EUR-USD'],direction_eligible=True,price_eligible=False,
                        direction_constant=2,models={},runtime_files={},packages={})
            store=SupervisedShadowStore(folder/'shadow.sqlite3')
            run=store.register(config,origin)
            (folder/'active.json').write_text(json.dumps(dict(run=run,research=str(folder))))
            report={'points':[dict(median=2,low80=1,high80=3) for _ in range(30)]}
            predictor=lambda batch: [[[1,2,3] for _ in range(30)] for _ in batch]
            with patch('scripts.shadow_supervised.now_taipei',return_value=datetime.fromisoformat(f'{origin}T10:00:00+08:00')), \
                    patch('scripts.shadow_supervised.refresh_history',return_value={'EUR-USD':self.history}) as refresh, \
                    patch('scripts.shadow_supervised.download_market',return_value=self.snapshot), \
                    patch('scripts.shadow_supervised.evaluate',return_value=report), \
                    patch('scripts.benchmark_forecasts.Predictor',return_value=predictor):
                daily(folder)
                daily(folder)
                refresh.assert_called_once()
            saved=store.records(run)
            self.assertEqual(saved[0][1]['pairs']['EUR-USD']['direction']['30'],[0,0,1])
            tomorrow=origin+timedelta(days=1)
            with patch('scripts.shadow_supervised.now_taipei',return_value=datetime.fromisoformat(f'{tomorrow}T10:00:00+08:00')), \
                    patch('scripts.shadow_supervised.refresh_history',side_effect=RuntimeError('missing quote')):
                with self.assertRaises(RuntimeError):
                    daily(folder)
            self.assertEqual(saved,store.records(run))


if __name__=='__main__':
    unittest.main()
