from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from librus.models import Dziecko, Ocena, Wiadomosc
from monitoring.models import LogEntry
from monitoring.services import provision_service_account


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', LIBRUS_MONITORING_TOKEN='')
class MobileDashboardTests(TestCase):
    librus_url = '/api/v1/librus/dashboard/'
    backup_url = '/api/v1/backup/dashboard/'

    def setUp(self):
        self.user = User.objects.create_user('parent', password='test-password')
        self.other = User.objects.create_user('other', password='test-password')
        self.token = Token.objects.create(user=self.user)
        self.service, self.machine_token = provision_service_account(
            owner=self.user, username='svc_phone', service_name='backup')
        self.other_service, _ = provision_service_account(
            owner=self.other, username='svc_other', service_name='backup')
        self.client = APIClient()
        self.authenticate(self.token)

    def authenticate(self, token):
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')

    def grant_librus(self):
        self.user.groups.add(Group.objects.create(name='Librus'))

    def test_requires_valid_active_token(self):
        for auth in ('', 'Token invalid'):
            self.client.credentials(HTTP_AUTHORIZATION=auth)
            for url in (self.librus_url, self.backup_url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response['Cache-Control'], 'no-store')
        self.user.is_active = False
        self.user.save()
        self.authenticate(self.token)
        self.assertEqual(self.client.get(self.backup_url).status_code, 401)

    def test_librus_authorization_and_technical_token_isolation(self):
        self.assertEqual(self.client.get(self.librus_url).status_code, 403)
        self.grant_librus()
        self.assertEqual(self.client.get(self.librus_url).status_code, 200)
        self.service.service_user.groups.add(Group.objects.get(name='Librus'))
        self.authenticate(self.machine_token)
        self.assertEqual(self.client.get(self.librus_url).status_code, 403)
        response = self.client.get(self.backup_url)
        self.assertEqual([s['id'] for s in response.data['services']], [self.service.pk])

    def test_backup_scope_status_and_no_secrets(self):
        LogEntry.objects.create(service=self.service, status_code=0, message={'token': 'secret', 'log': 'private'})
        response = self.client.get(self.backup_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['summary'], {'total': 1, 'healthy': 1})
        self.assertEqual(response.data['services'][0]['status'], 'OK')
        self.assertNotIn('secret', response.content.decode())
        self.assertNotIn('private', response.content.decode())
        self.assertNotIn(self.machine_token.key, response.content.decode())
        self.user.is_superuser = True
        self.user.save()
        self.assertEqual(len(self.client.get(self.backup_url).data['services']), 2)
        self.other_service.is_archived = True
        self.other_service.save()
        self.assertEqual(len(self.client.get(self.backup_url).data['services']), 1)

    def test_librus_summary_activity_limit_and_report(self):
        self.grant_librus()
        child = Dziecko.objects.create(user=self.other, librus_login='test-child')
        for i in range(12):
            Wiadomosc.objects.create(dziecko=child, wiadomosc_id=str(i), temat=f'Message {i}')
        Ocena.objects.create(dziecko=child, librus_id='1', wartosc='5', przedmiot='Matematyka', data=date.today())
        Ocena.objects.create(dziecko=child, librus_id='2', wartosc='1', przedmiot='Matematyka',
                             data=date.today(), archiwizowano_at=timezone.now())
        service, _ = provision_service_account(owner=self.user, username='svc_librus', service_name='librus')
        LogEntry.objects.create(service=service, message={'secret': 'never expose raw reports'})
        response = self.client.get(self.librus_url)
        self.assertEqual(response.status_code, 200)
        item = response.data['children'][0]
        self.assertEqual(item['grade_summary']['projected'], '5,0')
        self.assertEqual(len(item['recent_grades']), 1)
        self.assertEqual(len(item['activity']), 10)
        self.assertEqual(item['activity'][0]['title'], 'Message 11')
        self.assertEqual(response.data['latest_report']['status_code'], 0)
        self.assertNotIn('secret', response.content.decode())

    def test_reads_do_not_write_or_send_notifications(self):
        self.grant_librus()
        with patch('notifications.services.send_mail') as mail, patch('notifications.services.send_fcm') as librus_mail:
            for url in (self.backup_url, self.librus_url):
                self.assertEqual(self.client.get(url).status_code, 200)
                self.assertEqual(self.client.post(url, {}, format='json').status_code, 405)
                self.assertEqual(self.client.delete(url).status_code, 405)
            mail.assert_not_called()
            librus_mail.assert_not_called()
        self.assertFalse(LogEntry.objects.exists())
        self.service.refresh_from_db()
        self.assertIsNone(self.service.last_check_at)
        self.assertIsNone(self.service.last_status)


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                                       'LOCATION': 'mobile-auth-tests'}})
class MobileAuthenticationTests(TestCase):
    login_url = '/api/v1/auth/login/'
    profile_url = '/api/v1/auth/me/'
    monitoring_url = '/api/v1/monitoring/dashboard/'

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user('viewer', password='test-password')
        self.librus = Group.objects.create(name='Librus')
        self.monitoring = Group.objects.create(name='Monitoring')
        self.client = APIClient()

    def login(self, password='test-password'):
        return self.client.post(self.login_url, {'username': self.user.username, 'password': password}, format='json')

    def authenticate(self):
        token, _ = Token.objects.get_or_create(user=self.user)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        return token

    def test_login_uses_password_and_reuses_existing_token(self):
        self.user.groups.add(self.librus)
        self.assertEqual(self.login('bad-password').status_code, 401)
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        with patch('notifications.services.send_mail') as mail:
            response = self.login()
            mail.assert_not_called()
        token = Token.objects.get(user=self.user)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['token'], token.key)
        self.assertEqual(response.data['permissions'], {'librus': True, 'monitoring': False})
        self.assertEqual(self.login().data['token'], token.key)
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertNotIn('test-password', response.content.decode())

    def test_login_blocks_unprivileged_inactive_and_technical_accounts(self):
        self.assertEqual(self.login().status_code, 403)
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        self.user.groups.add(self.monitoring)
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.login().status_code, 401)
        self.user.is_active = True
        self.user.save()
        service, token = provision_service_account(owner=self.user, username='svc_login', service_name='backup')
        machine = service.service_user
        machine.set_password('test-password')
        machine.save()
        machine.groups.add(self.librus, self.monitoring)
        response = self.client.post(self.login_url, {'username': machine.username, 'password': 'test-password'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {token.key}')
        self.assertEqual(self.client.get(self.profile_url).data['permissions'], {'librus': False, 'monitoring': False})
        self.assertEqual(self.client.get(self.monitoring_url).status_code, 403)

    def test_profile_permissions_reflect_group_changes_without_returning_token(self):
        token = self.authenticate()
        self.assertEqual(self.client.get(self.profile_url).data['permissions'], {'librus': False, 'monitoring': False})
        self.user.groups.add(self.librus)
        self.assertEqual(self.client.get(self.profile_url).data['permissions'], {'librus': True, 'monitoring': False})
        self.user.groups.add(self.monitoring)
        response = self.client.get(self.profile_url)
        self.assertEqual(response.data['permissions'], {'librus': True, 'monitoring': True})
        self.assertNotIn(token.key, response.content.decode())
        self.user.groups.remove(self.librus)
        self.assertEqual(self.client.get(self.profile_url).data['permissions'], {'librus': False, 'monitoring': True})
        self.assertEqual(self.client.get('/api/v1/librus/dashboard/').status_code, 403)

    def test_monitoring_group_sees_all_active_services_and_latest_report_only(self):
        token = self.authenticate()
        self.assertEqual(self.client.get(self.monitoring_url).status_code, 403)
        self.user.groups.add(self.monitoring)
        other = User.objects.create_user('another-owner', password='test-password')
        service, _ = provision_service_account(owner=other, username='svc_report', service_name='phone')
        empty, _ = provision_service_account(owner=self.user, username='svc_empty', service_name='empty')
        archived, _ = provision_service_account(owner=self.user, username='svc_archived', service_name='archived')
        archived.is_archived = True
        archived.save()
        LogEntry.objects.create(service=service, message={'summary': 'Old report'})
        report = LogEntry.objects.create(service=service, status_code=2, message={
            'error': f'Błąd backupu {token.key}', 'token': 'do-not-publish',
            'log': 'private raw log', 'failed_tasks': [{'script': 'backup_sd', 'exit_code': 42, 'password': 'secret'}],
        })
        response = self.client.get(self.monitoring_url)
        rows = {item['id']: item for item in response.data['services']}
        self.assertEqual(set(rows), {service.pk, empty.pk})
        latest = rows[service.pk]['latest_report']
        self.assertEqual(latest['checked_at'], report.checked_at)
        self.assertEqual(latest['status_code'], 2)
        self.assertIn('Błąd backupu', latest['summary'])
        self.assertIn('backup_sd: kod 42', latest['summary'])
        self.assertIsNone(rows[empty.pk]['latest_report'])
        for hidden in ('Old report', token.key, 'do-not-publish', 'private raw log', 'secret'):
            self.assertNotIn(hidden, response.content.decode())
        self.assertIn('no-store', response['Cache-Control'])
        self.assertEqual(self.client.post(self.monitoring_url, {}, format='json').status_code, 405)

    def test_login_validation_and_rate_limit(self):
        self.assertEqual(self.client.get(self.login_url).status_code, 405)
        cache.clear()
        for _ in range(5):
            self.assertEqual(self.login('wrong').status_code, 401)
        response = self.login('wrong')
        self.assertEqual(response.status_code, 429)
        self.assertFalse(Token.objects.filter(user=self.user).exists())

    def test_anonymous_profile_and_monitoring_are_rejected(self):
        for url in (self.profile_url, self.monitoring_url):
            self.assertEqual(self.client.get(url).status_code, 401)
        self.user.is_superuser = True
        self.user.save()
        self.authenticate()
        self.assertEqual(self.client.get(self.profile_url).data['permissions'], {'librus': True, 'monitoring': True})
        self.assertEqual(self.client.get(self.monitoring_url).status_code, 200)

    def test_login_rejects_malformed_fields_without_disclosing_password(self):
        response = self.client.post(self.login_url, {'username': 'viewer'}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Token.objects.filter(user=self.user).exists())
