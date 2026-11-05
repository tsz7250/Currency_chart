import re
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import smtplib
import ssl
from flask import Flask

from app.alerts import AlertService, TAIPEI, bp
from app.exchange_rate_manager import ExchangeRateManager
from app.routes import bp as main_bp


class AlertsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.now = datetime(2026, 11, 4, 9, tzinfo=TAIPEI)
        self.clock = patch('app.alerts.now_taipei', side_effect=lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.mail = patch('app.alerts.smtplib.SMTP_SSL')
        self.smtp_factory = self.mail.start()
        self.addCleanup(self.mail.stop)
        self.smtp = self.smtp_factory.return_value
        self.send = self.smtp.send_message
        self.send.return_value = {}
        self.service = AlertService(Path(self.temp.name) / 'alerts.sqlite3',
                                    smtp_username='sender@example.com', smtp_password='test-password',
                                    base_url='http://192.168.1.10:5000')
        root = Path(__file__).resolve().parent.parent
        self.app = Flask(__name__, template_folder=str(root / 'templates'),
                         static_folder=str(root / 'static'))
        self.app.testing = True
        self.app.alerts = self.service
        self.app.register_blueprint(bp)
        self.app.register_blueprint(main_bp)
        self.client = self.app.test_client()
        self.payload = dict(email='user@example.com', buy_currency='USD',
                            sell_currency='HKD', direction='gte', target_rate='7.8')
        self.manager = Mock()
        self.manager.get_current_rate.return_value = {'date': '2026-11-04', 'rate': 7.8}

    def apply(self, **changes):
        return self.client.post('/api/alerts', json={**self.payload, **changes})

    def links(self):
        content = self.send.call_args.args[0].get_content()
        return re.findall(r'/alerts/(?:verify|cancel)/[\w-]+', content)

    def activate(self, **changes):
        self.assertEqual(self.apply(**changes).status_code, 202)
        verify, cancel = self.links()
        self.assertEqual(self.client.post(verify).status_code, 200)
        return verify, cancel

    def check(self):
        self.service.check(self.manager)

    def test_validation(self):
        for target in ('NaN', 'Infinity', '-1', '0', '1e100000', True, [], None):
            with self.subTest(target=target):
                self.assertEqual(self.apply(target_rate=target).status_code, 400)
        for changes in ({'email': 'bad\r\n@example.com'}, {'buy_currency': 'ZZZ'},
                        {'sell_currency': 'USD'}, {'direction': 'above'}, {'buy_currency': []}):
            self.assertEqual(self.apply(**changes).status_code, 400)
        self.assertEqual(self.client.post('/api/alerts', json=[]).status_code, 400)
        self.assertEqual(self.client.post('/api/alerts', data='x' * 5000).status_code, 413)
        self.smtp_factory.assert_not_called()

    def test_verification_get_does_not_activate_and_expiry(self):
        self.assertEqual(self.apply().status_code, 202)
        verify, _ = self.links()
        response = self.client.get(verify)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')
        self.check()
        self.manager.get_current_rate.assert_not_called()
        self.now += timedelta(hours=25)
        self.assertEqual(self.client.post(verify).status_code, 400)

    def test_daily_deduplication_restart_and_old_cancel_link(self):
        _, original_cancel = self.activate()
        self.check()
        self.check()
        self.service = AlertService(self.service.database, smtp_username='sender@example.com',
                                    smtp_password='test-password', base_url='http://192.168.1.10:5000')
        self.check()
        self.assertEqual(self.send.call_count, 2)
        self.now += timedelta(days=1)
        self.manager.get_current_rate.return_value['date'] = '2026-11-05'
        self.check()
        self.assertEqual(self.send.call_count, 3)
        self.assertEqual(self.client.get(original_cancel).status_code, 200)
        self.assertEqual(self.client.post(original_cancel).status_code, 200)
        self.now += timedelta(days=1)
        self.check()
        self.assertEqual(self.send.call_count, 3)
        with self.service.connection() as db:
            self.assertNotEqual(db.execute('SELECT email FROM alerts').fetchone()[0], self.payload['email'])

    def test_decimal_boundary_and_duplicate_normalization(self):
        self.activate(target_rate='7.8000')
        self.assertEqual(self.apply(target_rate='7.8').status_code, 202)
        self.assertEqual(self.send.call_count, 1)
        self.check()
        self.assertEqual(self.send.call_count, 2)

    def test_lower_threshold_and_not_met(self):
        self.activate(direction='lte', target_rate='7.7')
        self.check()
        self.assertEqual(self.send.call_count, 1)
        self.manager.get_current_rate.return_value['rate'] = 7.7
        self.check()
        self.assertEqual(self.send.call_count, 2)

    def test_stale_rates_and_weekend(self):
        self.activate()
        self.manager.get_current_rate.return_value['date'] = '2026-11-03'
        self.check()
        self.assertEqual(self.send.call_count, 1)
        self.now = datetime(2026, 11, 7, 9, tzinfo=TAIPEI)
        self.manager.get_current_rate.return_value['date'] = '2026-11-06'
        self.check()
        self.assertEqual(self.send.call_count, 2)

    def test_pair_fetched_once_per_run(self):
        self.activate()
        self.activate(email='second@example.com')
        self.check()
        self.manager.get_current_rate.assert_called_once_with('USD', 'HKD', force_refresh=True)
        self.assertEqual(self.send.call_count, 4)

    def test_mail_failure_retry_and_timeout_no_retry(self):
        self.activate()
        self.send.side_effect = smtplib.SMTPDataError(451, b'Try later')
        self.check()
        self.send.side_effect = None
        self.check()
        self.assertEqual(self.send.call_count, 3)
        self.now += timedelta(days=1)
        self.manager.get_current_rate.return_value['date'] = '2026-11-05'
        self.send.side_effect = TimeoutError()
        self.check()
        self.check()
        self.assertEqual(self.send.call_count, 4)

    def test_smtp_ssl_login_and_utf8_message(self):
        self.activate()
        args, options = self.smtp_factory.call_args
        self.assertEqual(args, ('smtp.gmail.com', 465))
        self.assertEqual(options['timeout'], 20)
        self.assertEqual(options['context'].verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(options['context'].check_hostname)
        self.smtp.login.assert_called_once_with('sender@example.com', 'test-password')
        message = self.send.call_args.args[0]
        self.assertEqual(str(message['Subject']), '請確認匯率到價提醒')
        self.assertIn('匯率到價提醒', str(message['From']))
        self.assertEqual(str(message['To']), 'user@example.com')
        self.assertEqual(message.get_content_type(), 'text/plain')
        self.assertEqual(message.get_content_charset(), 'utf-8')
        self.assertIn('1 USD 可兌換的 HKD', message.get_content())
        self.assertEqual(self.send.call_args.kwargs,
                         {'from_addr': 'sender@example.com', 'to_addrs': ['user@example.com']})
        self.assertTrue(message['Message-ID'])

    def test_authentication_failure_is_retryable_without_logging_secrets(self):
        self.activate()
        self.smtp.login.side_effect = smtplib.SMTPAuthenticationError(535, b'private-test-password')
        with self.assertLogs('app.alerts', level='WARNING') as logs:
            self.check()
        self.assertNotIn('private-test-password', '\n'.join(logs.output))
        self.assertEqual(self.send.call_count, 1)
        self.smtp.login.side_effect = None
        self.check()
        self.assertEqual(self.send.call_count, 2)

    def test_recipient_and_data_refusal_can_retry(self):
        self.activate()
        for error in (smtplib.SMTPRecipientsRefused({'user@example.com': (550, b'Refused')}),
                      smtplib.SMTPDataError(452, b'Quota')):
            self.send.side_effect = error
            self.check()
        self.assertEqual(self.send.call_count, 3)
        self.send.side_effect = None
        self.check()
        self.assertEqual(self.send.call_count, 4)

    def test_disconnect_during_send_does_not_retry(self):
        self.activate()
        self.send.side_effect = smtplib.SMTPServerDisconnected('Connection lost')
        self.check()
        self.check()
        self.assertEqual(self.send.call_count, 2)

    def test_connection_failure_before_send_can_retry(self):
        self.activate()
        self.smtp_factory.side_effect = TimeoutError()
        self.check()
        self.assertEqual(self.send.call_count, 1)
        self.smtp_factory.side_effect = None
        self.check()
        self.assertEqual(self.send.call_count, 2)

    def test_quit_failure_after_acceptance_preserves_sent_state(self):
        self.activate()
        self.smtp.quit.side_effect = smtplib.SMTPServerDisconnected('Connection lost')
        self.smtp.close.side_effect = OSError('Socket closed')
        self.check()
        self.check()
        self.assertEqual(self.send.call_count, 2)
        with self.service.connection() as db:
            row = db.execute("SELECT state, message_id FROM deliveries WHERE key LIKE 'reminder:%'").fetchone()
        self.assertEqual(row['state'], 'sent')
        self.assertTrue(row['message_id'])

    def test_incomplete_smtp_configuration_disables_alerts(self):
        config = dict(base_url='http://192.168.1.10:5000', smtp_username='sender@example.com',
                      smtp_password='test-password')
        for invalid in ({'smtp_username': ''}, {'smtp_password': ''}, {'smtp_host': ''},
                        {'smtp_host': 'bad host'}, {'smtp_port': 'bad'}, {'smtp_port': 0},
                        {'smtp_port': 65536}, {'sender': 'bad\r\naddress'}):
            with self.subTest(invalid=invalid):
                service = AlertService(self.service.database, **{**config, **invalid})
                self.assertFalse(service.enabled)
        service = AlertService(self.service.database, **config, sender='alias@example.com')
        self.assertEqual(service.sender, 'alias@example.com')

    def test_quota_and_legacy_pause(self):
        self.activate()
        with self.service.connection() as db:
            db.executemany('INSERT INTO attempts VALUES(?,?,?,?)',
                           [('2026-11-04', self.now.timestamp(), 'hash', 'reminder')] * 249)
        self.check()
        self.assertEqual(self.send.call_count, 1)
        self.now += timedelta(days=1)
        self.manager.get_current_rate.return_value['date'] = '2026-11-05'
        with self.service.connection() as db:
            db.execute('CREATE TABLE provider_pause (day TEXT PRIMARY KEY)')
            db.execute('INSERT INTO provider_pause VALUES(?)', ('2026-11-05',))
        self.check()
        self.check()
        self.assertEqual(self.send.call_count, 2)

    def test_concurrent_checks_only_send_once(self):
        self.activate()
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.check(), range(2)))
        self.assertEqual(self.send.call_count, 2)

    def test_cooldown_and_limit(self):
        self.activate()
        self.assertEqual(self.apply(target_rate='7.9').status_code, 429)
        for i in range(4):
            self.now += timedelta(minutes=11)
            self.activate(target_rate=str(8 + i))
        self.now += timedelta(minutes=11)
        self.assertEqual(self.apply(target_rate='20').status_code, 429)

    def test_ip_limit_ignores_forwarded_header(self):
        for i in range(10):
            self.assertEqual(self.apply(email=f'user{i}@example.com').status_code, 202)
        response = self.client.post('/api/alerts', json={**self.payload, 'email': 'new@example.com'},
                                    headers={'X-Forwarded-For': '1.2.3.4'})
        self.assertEqual(response.status_code, 429)

    def test_disabled_and_homepage(self):
        self.service.enabled = False
        self.assertEqual(self.apply().status_code, 503)
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('到價提醒尚未設定', html)
        self.assertNotIn('id="alert-form"', html)
        self.assertIn('id="alert-btn"', html)
        self.assertIn('id="alert-popup-overlay"', html)
        self.assertNotIn('alert-panel', html)
        self.service.enabled = True
        html = self.client.get('/').get_data(as_text=True)
        self.assertIn('id="alert-form"', html)
        self.assertLess(html.index('id="alert-popup-overlay"'), html.index('id="alert-form"'))

    def test_force_refresh_preserves_default_cache(self):
        manager = ExchangeRateManager.__new__(ExchangeRateManager)
        manager.latest_rate_cache = Mock()
        manager.latest_rate_cache.get.return_value = {'date': '2026-11-03', 'rate': 7.7}
        manager.get_exchange_rate = Mock(return_value={'data': {'conversionRate': '7.8'}})
        manager.extract_local_rates = Mock(return_value=([], []))
        with self.app.app_context():
            self.assertEqual(manager.get_current_rate('USD', 'HKD')['rate'], 7.7)
            manager.get_exchange_rate.assert_not_called()
            self.assertEqual(manager.get_current_rate('USD', 'HKD', force_refresh=True)['rate'], 7.8)
        manager.get_exchange_rate.assert_called_once()

    def test_unknown_crash_record_is_not_retried(self):
        self.activate()
        with self.service.connection() as db:
            alert_id = db.execute('SELECT id FROM alerts').fetchone()[0]
            db.execute('INSERT INTO deliveries VALUES(?,?,?,?)',
                       (f'reminder:{alert_id}:2026-11-04', alert_id, 'sending', None))
        self.check()
        self.assertEqual(self.send.call_count, 1)

    def test_pending_resend_preserves_old_cancel_link(self):
        self.assertEqual(self.apply().status_code, 202)
        _, original_cancel = self.links()
        self.now += timedelta(minutes=11)
        self.activate()
        self.assertEqual(self.client.post(original_cancel).status_code, 200)

    def test_schedule_uses_taipei_and_runs_alerts_after_update_failure(self):
        import schedule
        from app import scheduler
        self.app.manager = self.manager
        with patch.object(scheduler, 'Thread'), \
                patch.object(scheduler, '_app', None), \
                patch('app.forecast_shadow.launch_shadow') as shadow, \
                patch('app.forecast_supervised_shadow.launch_supervised') as supervised, \
                patch.object(schedule.default_scheduler, 'jobs', []):
            scheduler.init_scheduler(self.app)
            daily = [job for job in schedule.default_scheduler.jobs if job.unit == 'days']
            self.assertEqual([str(job.at_time_zone) for job in daily], ['Asia/Taipei'] * 4)
            self.assertEqual([str(job.at_time) for job in daily], ['09:00:00', '09:30:00', '09:45:00', '18:15:00'])
            shadow.assert_called_once_with(self.app.instance_path)
            supervised.assert_called_once_with(self.app.instance_path)
        with patch.object(scheduler, '_app', self.app), \
                patch.object(scheduler, '_fetch_missing_data', side_effect=RuntimeError('test')), \
                patch.object(self.service, 'check') as check, \
                self.assertLogs('app.scheduler', level='ERROR'):
            scheduler.scheduled_update()
            check.assert_called_once_with(self.manager)

    def test_bad_config_does_not_break_charts(self):
        for url in ('http://[', 'http://localhost:bad', 'http://localhost:0',
                    'http://user:pass@localhost', 'http://localhost/extra'):
            service = AlertService(self.service.database, smtp_username='sender@example.com',
                                   smtp_password='test-password', base_url=url)
            self.assertFalse(service.enabled)

    def test_existing_api_and_sse_event(self):
        from app.sse import send_sse_event, sse_clients, sse_lock
        import queue
        self.app.manager = self.manager
        self.manager.create_chart.return_value = {'dates': ['2026-11-04'], 'rates': [7.8]}
        self.manager.extract_local_rates.return_value = (['2026-11-04'], [7.8])
        self.assertEqual(self.client.get('/api/chart').status_code, 200)
        self.assertEqual(self.client.get('/api/latest_rate').json['rate'], 7.8)
        client_queue = queue.Queue()
        with sse_lock:
            sse_clients.append(client_queue)
        try:
            send_sse_event('rate_updated', {'rate': 7.8})
            self.assertIn('event: rate_updated', client_queue.get_nowait())
        finally:
            with sse_lock:
                sse_clients.remove(client_queue)

    def test_factory_wires_alerts_without_network(self):
        import app
        with patch.dict('os.environ', {'ALERT_DATABASE': self.service.database,
                                      'SMTP_USERNAME': '', 'SMTP_PASSWORD': '', 'ALERT_SENDER_EMAIL': '',
                                      'ALERT_BASE_URL': ''}), \
                patch('app.auto_update_data'), patch('app.setup_logging'), \
                patch('app.ExchangeRateManager'), patch('app.init_scheduler'), \
                patch('app.atexit.register'), patch('app.signal.signal'), patch('builtins.print'):
            created = app.create_app()
            self.assertFalse(created.alerts.enabled)
            self.assertEqual(created.test_client().get('/').status_code, 200)

    def test_factory_loads_dotenv_and_preserves_environment(self):
        import app
        from dotenv import load_dotenv
        env_file = Path(self.temp.name) / '.env'
        env_file.write_text('SMTP_PASSWORD=file-password\nSMTP_USERNAME=sender@example.com\n'
                            'ALERT_BASE_URL=http://192.168.1.10:5000\n', encoding='utf-8')
        for existing_key, expected_key in ((None, 'file-password'), ('environment-password', 'environment-password')):
            with self.subTest(existing_key=existing_key), patch.dict(os.environ), \
                    patch('app.auto_update_data'), patch('app.setup_logging'), \
                    patch('app.ExchangeRateManager'), patch('app.init_scheduler'), \
                    patch('app.atexit.register'), patch('app.signal.signal'), patch('builtins.print'), \
                    patch('app.load_dotenv', side_effect=lambda path, override:
                          load_dotenv(env_file, override=override)) as loader:
                for key in ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USERNAME', 'SMTP_PASSWORD',
                            'ALERT_SENDER_EMAIL', 'ALERT_BASE_URL', 'PYTHON_DOTENV_DISABLED'):
                    os.environ.pop(key, None)
                os.environ['ALERT_DATABASE'] = self.service.database
                if existing_key is not None:
                    os.environ['SMTP_PASSWORD'] = existing_key
                created = app.create_app()
                loader.assert_called_once_with(os.path.join(os.path.dirname(app.__file__), '..', '.env'),
                                               override=False)
                self.assertTrue(created.alerts.enabled)
                self.assertEqual(created.alerts.smtp_password, expected_key)
                self.assertEqual(created.alerts.sender, 'sender@example.com')


if __name__ == '__main__':
    unittest.main()
