from datetime import datetime, timedelta, timezone as datetime_timezone
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from backupapp.models import BackupInvitation
from .admin import MonitoredServiceAdmin, MonitoredServiceForm
from .models import LogEntry, MonitoredService
from .services import provision_service_account


class MonitoringTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner')
        self.service, self.token = provision_service_account(
            owner=self.owner, username='svc_test', service_name='test',
        )
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.url = reverse('monitoring:receive_monitoring')

    def test_route_and_api_root(self):
        self.assertEqual(self.url, '/api/monitoring/')
        for method in (self.client.get, self.client.post):
            self.assertEqual(method('/api/').status_code, 404)

    def test_empty_ping(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 201)
        log = LogEntry.objects.get()
        self.assertEqual(log.status_code, 0)
        self.assertEqual(log.message, {})

    def test_arbitrary_json_is_preserved(self):
        for payload in ({'nested': {'error': False}, 'temperature': 21.3}, [], [1, {'a': True}], 'text', 42, False):
            with self.subTest(payload=payload):
                response = self.client.post(self.url, data=payload, format='json')
                self.assertEqual(response.status_code, 201)
                self.assertEqual(LogEntry.objects.latest('id').message, payload)
        response = self.client.generic('POST', self.url, data='null', content_type='application/json')
        self.assertEqual(response.status_code, 201)
        self.assertIsNone(LogEntry.objects.latest('id').message)

    def test_bad_payload_does_not_create_log(self):
        for value in ('wrong', True, 99, None):
            response = self.client.post(self.url, {'status_code': value}, format='json')
            self.assertEqual(response.status_code, 400)
        response = self.client.generic('POST', self.url, data='{broken', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(LogEntry.objects.exists())

    def test_missing_or_invalid_token(self):
        self.client.credentials()
        self.assertEqual(self.client.post(self.url).status_code, 401)
        self.client.credentials(HTTP_AUTHORIZATION='Token invalid')
        self.assertEqual(self.client.post(self.url).status_code, 401)

    def test_disabled_account_blocks_api_and_installation(self):
        account = self.service.service_user
        account.is_active = False
        account.save(update_fields=['is_active'])
        self.assertEqual(self.client.post(self.url).status_code, 401)
        self.assertEqual(self.client.get(reverse('setup_phone', args=[self.token.key])).status_code, 404)
        self.assertFalse(LogEntry.objects.exists())
        self.assertTrue(Token.objects.filter(pk=self.token.pk).exists())
        account.is_active = True
        account.save(update_fields=['is_active'])
        self.assertEqual(self.client.post(self.url).status_code, 201)

    def test_disabled_service_and_unregistered_account(self):
        self.service.is_active = False
        self.service.save(update_fields=['is_active'])
        self.assertEqual(self.client.post(self.url).status_code, 403)
        self.assertEqual(self.service.status, 3)
        other = User.objects.create_user(username='other')
        token = Token.objects.create(user=other)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(self.client.post(self.url).status_code, 403)

    @patch('monitoring.models.send_mail')
    def test_failure_and_recovery_notifications(self, send_mail):
        self.owner.email = 'owner@example.com'
        self.owner.save(update_fields=['email'])
        for code, expected_status in ((0, 0), (2, 4), (0, 0)):
            self.assertEqual(self.client.post(self.url, {'status_code': code}, format='json').status_code, 201)
            self.service.refresh_from_db()
            self.assertEqual(self.service.last_status, expected_status)
        self.assertEqual(send_mail.call_count, 3)
        last_contact = self.service.last_check_at
        self.service.sprawdz_status_i_wyslij_powiadomienie()
        self.service.refresh_from_db()
        self.assertEqual(self.service.last_check_at, last_contact)

    def test_timeout(self):
        log = LogEntry.objects.create(service=self.service)
        LogEntry.objects.filter(pk=log.pk).update(checked_at=timezone.now() - timedelta(days=1))
        self.assertEqual(self.service.status, 2)

    def test_provisioning_and_backup_invitation(self):
        self.assertFalse(self.service.service_user.has_usable_password())
        service, token = provision_service_account(owner=self.owner, username='svc_test', service_name='test')
        self.assertEqual(service.pk, self.service.pk)
        self.assertEqual(token.pk, self.token.pk)
        invitation = BackupInvitation.objects.create(user=self.owner)
        account = User.objects.get(username='svc_owner_backup')
        self.assertFalse(account.has_usable_password())
        self.assertEqual(invitation.token_link, Token.objects.get(user=account).key)
        self.assertTrue(MonitoredService.objects.filter(service_user=account, owner=self.owner).exists())

    def test_admin_token_switch(self):
        from django.contrib.admin import site
        form = MonitoredServiceForm(instance=self.service)
        self.assertTrue(form.fields['token_enabled'].initial)
        form.cleaned_data = {'token_enabled': False}
        MonitoredServiceAdmin(MonitoredService, site).save_model(None, self.service, form, True)
        self.service.service_user.refresh_from_db()
        self.assertFalse(self.service.service_user.is_active)
        self.assertEqual(self.client.post(self.url).status_code, 401)

    def test_admin_service_save_with_large_history(self):
        from django.test import Client
        admin = User.objects.create_superuser(username='admin', email='admin@example.com', password='test-password')
        client = Client()
        client.force_login(admin)
        LogEntry.objects.bulk_create([
            LogEntry(service=self.service, message={'row': i}) for i in range(1100)
        ])
        url = reverse('admin:monitoring_monitoredservice_change', args=[self.service.pk])
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['inline_admin_formsets'], [])
        self.assertContains(response, '<tr><td>', count=15)
        self.assertContains(response, 'Pełna historia raportów')
        response = client.post(url, {
            'owner': self.owner.pk,
            'service_user': self.service.service_user_id,
            'service_name': self.service.service_name,
            'description': 'Zapis przy dużej historii',
            'timeout_ms': self.service.timeout_ms,
            'is_active': 'on',
            'token_enabled': 'on',
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        self.service.refresh_from_db()
        self.assertEqual(self.service.description, 'Zapis przy dużej historii')
        self.assertEqual(self.service.logs.count(), 1100)
        response = client.get(reverse('admin:monitoring_logentry_changelist'), {'service__id__exact': self.service.pk})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['cl'].result_count, 1100)
        self.assertEqual(len(response.context['cl'].result_list), 50)

    @patch('monitoring.models.send_mail')
    def test_archive_hides_service_and_logs_without_revoking_token(self, send_mail):
        self.owner.email = 'owner@example.com'
        self.owner.save(update_fields=['email'])
        admin = User.objects.create_superuser('admin', 'admin@example.com', 'test-password')
        admin_client = Client()
        admin_client.force_login(admin)
        change_url = reverse('admin:monitoring_monitoredservice_change', args=[self.service.pk])
        response = admin_client.post(change_url, {
            'owner': self.owner.pk,
            'service_user': self.service.service_user_id,
            'service_name': self.service.service_name,
            'timeout_ms': self.service.timeout_ms,
            'is_active': 'on',
            'is_archived': 'on',
            'token_enabled': 'on',
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        self.service.refresh_from_db()
        self.assertTrue(self.service.is_archived)
        self.assertEqual(self.service.status, 3)
        self.assertEqual(self.client.post(self.url).status_code, 201)
        call_command('monitoring_sprawdz')
        send_mail.assert_not_called()

        service_list = reverse('admin:monitoring_monitoredservice_changelist')
        log_list = reverse('admin:monitoring_logentry_changelist')
        for url in (service_list, log_list):
            self.assertEqual(admin_client.get(url).context['cl'].result_count, 0)
            self.assertEqual(admin_client.get(url, {'archive': 'archived'}).context['cl'].result_count, 1)
            self.assertEqual(admin_client.get(url, {'archive': 'all'}).context['cl'].result_count, 1)
        admin_client.force_login(self.owner)
        self.assertEqual(admin_client.get(reverse('backup_dashboard')).context['services'], [])

        admin_client.force_login(admin)
        response = admin_client.post(change_url, {
            'owner': self.owner.pk,
            'service_user': self.service.service_user_id,
            'service_name': self.service.service_name,
            'timeout_ms': self.service.timeout_ms,
            'is_active': 'on',
            'token_enabled': 'on',
            '_save': 'Zapisz',
        })
        self.assertEqual(response.status_code, 302)
        self.service.refresh_from_db()
        self.assertFalse(self.service.is_archived)
        self.assertEqual(admin_client.get(service_list).context['cl'].result_count, 1)

    def test_intervals_and_deadlines_are_the_same_on_weekdays_and_weekends(self):
        log = LogEntry.objects.create(service=self.service)
        for day in (9, 10, 11):  # piątek, sobota, niedziela
            now = datetime(2026, 10, day, 12, tzinfo=datetime_timezone.utc)
            for interval_ms in (60_000, 3_600_000, 86_400_000, 500_000):
                self.service.timeout_ms = interval_ms
                interval = interval_ms / 1000
                for delay, expected in (
                    (0, 0), (interval, 0), (interval + .001, 1),
                    (interval * 3, 1), (interval * 3 + .001, 2),
                ):
                    with self.subTest(day=day, interval=interval_ms, delay=delay):
                        LogEntry.objects.filter(pk=log.pk).update(checked_at=now - timedelta(seconds=delay))
                        with patch('monitoring.models.timezone.now', return_value=now):
                            self.assertEqual(self.service.status, expected)

    def test_error_does_not_become_warning_before_fail(self):
        now = timezone.now()
        log = LogEntry.objects.create(service=self.service, status_code=2)
        for delay, expected in ((0, 4), (61, 4), (180, 4), (181, 2)):
            with self.subTest(delay=delay):
                LogEntry.objects.filter(pk=log.pk).update(checked_at=now - timedelta(seconds=delay))
                with patch('monitoring.models.timezone.now', return_value=now):
                    self.assertEqual(self.service.status, expected)

    def test_disabled_service_or_account_overrides_error(self):
        LogEntry.objects.create(service=self.service, status_code=2)
        self.service.is_active = False
        self.assertEqual(self.service.status, 3)
        self.service.is_active = True
        self.service.service_user.is_active = False
        self.assertEqual(self.service.status, 3)

    def test_service_without_reports_is_fail(self):
        self.assertEqual(self.service.status, 2)

    def test_report_warning_and_app_specific_codes_remain_compatible(self):
        for code, expected in ((1, 1), (100, 0)):
            with self.subTest(code=code):
                response = self.client.post(self.url, {'status_code': code}, format='json')
                self.assertEqual(response.status_code, 201)
                self.service.refresh_from_db()
                self.assertEqual(self.service.status, expected)

    @patch('monitoring.models.send_mail')
    def test_periodic_command_alerts_once_per_transition_and_recovers(self, send_mail):
        self.owner.email = 'owner@example.com'
        self.owner.save(update_fields=['email'])
        self.client.post(self.url)
        send_mail.reset_mock()
        received_at = self.service.logs.latest('pk').checked_at
        for seconds, expected in ((61, 1), (120, 1), (181, 2), (240, 2)):
            with patch('monitoring.models.timezone.now', return_value=received_at + timedelta(seconds=seconds)):
                call_command('monitoring_sprawdz')
            self.service.refresh_from_db()
            self.assertEqual(self.service.last_status, expected)
            self.assertEqual(self.service.last_check_at, received_at)
        self.assertEqual(send_mail.call_count, 2)
        self.assertIn('PREFAIL', send_mail.call_args_list[0].args[0])
        self.assertIn('FAIL', send_mail.call_args_list[1].args[0])
        self.client.post(self.url)
        self.service.refresh_from_db()
        self.assertEqual(self.service.last_status, 0)
        self.assertEqual(send_mail.call_count, 3)

    def test_admin_interval_selection_preserves_existing_custom_value(self):
        self.service.timeout_ms = 500_000
        self.service.save(update_fields=['timeout_ms'])
        field = MonitoredServiceForm(instance=self.service).fields['timeout_ms']
        for interval in (60_000, 3_600_000, 86_400_000, 500_000):
            self.assertEqual(field.clean(str(interval)), interval)
        from django.core.exceptions import ValidationError
        for invalid in ('0', '-1', 'garbage'):
            with self.assertRaises(ValidationError):
                field.clean(invalid)

        data = {
            'owner': self.owner.pk, 'service_user': self.service.service_user_id,
            'service_name': self.service.service_name, 'timeout_ms': 86_400_000,
            'is_active': True, 'token_enabled': True,
        }
        form = MonitoredServiceForm(data=data, instance=self.service)
        self.assertTrue(form.is_valid(), form.errors)
        form.save()
        self.service.refresh_from_db()
        self.assertEqual(self.service.timeout_ms, 86_400_000)

    def test_dashboard_uses_current_service_state_instead_of_old_report_code(self):
        from django.test import Client
        client = Client()
        client.force_login(self.owner)
        log = LogEntry.objects.create(service=self.service, status_code=0)
        now = timezone.now()
        for delay, code, active, expected, label in (
            (0, 0, True, 0, 'OK'),
            (61, 0, True, 1, 'PREFAIL'),
            (181, 0, True, 2, 'FAIL'),
            (0, 2, True, 4, 'ERROR'),
            (0, 2, False, 3, 'INACTIVE'),
        ):
            with self.subTest(expected=expected):
                LogEntry.objects.filter(pk=log.pk).update(
                    checked_at=now - timedelta(seconds=delay), status_code=code,
                )
                MonitoredService.objects.filter(pk=self.service.pk).update(is_active=active)
                with patch('monitoring.models.timezone.now', return_value=now):
                    response = client.get(reverse('backup_dashboard'))
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['services'][0]['status_code'], expected)
                self.assertContains(response, f'>{label}</span>')
