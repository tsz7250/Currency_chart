import copy
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import Mock,patch
from datetime import date,timedelta
from app.forecast_archive import ForecastArchive
from app.forecast_release import PAIRS,ReleaseStore,bundle
from app.forecast_publisher import replay
from app.forecast_distribution import DistributedForecastService


class ReleaseTests(unittest.TestCase):
    def payload(self):
        entries=[(date(2026,1,1)+timedelta(days=i),1+i*.001) for i in range(300)]
        def predictor(batch):return [[[v[-1]*.99,v[-1],v[-1]*1.01] for _ in range(30)] for v in batch]
        report,_=replay(entries,predictor)
        return dict(schema=1,kind='official',version='official-test',published_at='2026-10-27T10:00:00+08:00',
                    forecasts={p:report for p in PAIRS})

    def test_two_deployments_exactly_equal_and_rollback_on_corruption(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);payload=self.payload();path=root/'release.zip';bundle(payload,path)
            a,b=(ReleaseStore(root/n) for n in ('a','b'))
            a.install(path.read_bytes());b.install(path.read_bytes())
            for pair in PAIRS:
                for h in (7,14,30):self.assertEqual(a.get(pair,h,date(2026,10,27)),b.get(pair,h,date(2026,10,27)))
            before=a.current()
            with self.assertRaises(Exception):a.install(path.read_bytes()[:-20])
            self.assertEqual(before,a.current())
            altered=copy.deepcopy(payload);altered['published_at']='2026-10-28T10:00:00+08:00';other=root/'other.zip';bundle(altered,other)
            with self.assertRaises(ValueError):a.install(other.read_bytes())
            self.assertEqual(before,a.current())
            newer=copy.deepcopy(payload);newer.update(version='official-new',published_at='2026-10-28T10:00:00+08:00')
            latest=root/'latest.zip';bundle(newer,latest);a.install(latest.read_bytes())
            with self.assertRaises(ValueError):a.install(path.read_bytes())
            self.assertEqual(a.current()['version'],'official-new')
            a.install(path.read_bytes(),allow_rollback=True)
            self.assertEqual(a.current(),before)

    def test_preview_and_unknown_files_not_official(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);p=self.payload();p['kind']='preview';path=root/'preview.zip';bundle(p,path)
            with self.assertRaises(ValueError):ReleaseStore(root/'client').install(path.read_bytes())
            stream=io.BytesIO()
            with zipfile.ZipFile(stream,'w') as z:z.writestr('../secret','x')
            with self.assertRaises(ValueError):ReleaseStore(root/'client').install(stream.getvalue())

    def test_archive_revisions_known_time_and_immutable_issue(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            archive=ForecastArchive(Path(tmp)/'quotes.sqlite3')
            archive.add('EUR-USD','2026-01-01',1,fetched='2026-01-02T00:00:00+00:00')
            archive.add('EUR-USD','2026-01-01',2,fetched='2026-01-03T00:00:00+00:00')
            self.assertEqual(archive.history('EUR-USD','2026-01-02T12:00:00+00:00')['2026-01-01']['rate'],1)
            self.assertEqual(archive.history('EUR-USD')['2026-01-01']['rate'],2)
            self.assertEqual(archive.records(),[])
            payload=self.payload();archive.issue('one',payload);archive.issue('one',payload)
            with self.assertRaises(ValueError):archive.issue('one',dict(payload,version='other'))
            self.assertEqual(len(archive.records()),1)

    def test_historical_forecasts_stable_after_append(self):
        entries=[(date(2026,1,1)+timedelta(days=i),1+i*.001) for i in range(340)]
        def predict(batch):return [[[v[-1]*(1+len(v)/100000)]*3 for _ in range(30)] for v in batch]
        _,a=replay(entries,predict);_,b=replay(entries+[(entries[-1][0]+timedelta(days=1),2)],predict)
        aa={r['origin']:r for r in a[7]['calibrated']};bb={r['origin']:r for r in b[7]['calibrated']}
        for origin in aa.keys()&bb.keys():self.assertEqual(aa[origin],bb[origin])

    def test_sync_failure_preserves_ready_result_and_api_avoids_local_data(self):
        from flask import Flask
        from app.routes import bp
        from app.alerts import TAIPEI
        from datetime import datetime
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);path=root/'release.zip';bundle(self.payload(),path)
            service=DistributedForecastService(root);service.store.install(path.read_bytes())
            self.addCleanup(service.shutdown)
            with patch('requests.get',side_effect=OSError('offline')):service.store.sync(force=True)
            app=Flask(__name__);app.register_blueprint(bp);app.config['FORECAST_DISTRIBUTED']=True
            app.forecasts=service;app.manager=Mock()
            with patch('app.routes.now_taipei',return_value=datetime(2026,10,27,tzinfo=TAIPEI)):
                response=app.test_client().get('/api/forecast?horizon=14').get_json()
            self.assertEqual(response['release_version'],'official-test');self.assertEqual(response['sync_state'],'offline')
            app.manager.create_chart.assert_not_called()
            self.assertEqual(service.store.get('XXX-USD',7,date(2026,10,27))[0]['status'],'unsupported')

    def test_concurrent_install_is_atomic(self):
        from concurrent.futures import ThreadPoolExecutor
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root=Path(tmp);path=root/'release.zip';bundle(self.payload(),path);content=path.read_bytes()
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures=[executor.submit(ReleaseStore(root/'shared').install,content) for _ in range(2)]
                self.assertEqual([f.result() for f in futures],['official-test']*2)
            self.assertEqual(ReleaseStore(root/'shared').current()['version'],'official-test')


if __name__=='__main__':unittest.main()
