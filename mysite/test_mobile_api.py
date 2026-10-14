from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import Group, User
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
        with patch('monitoring.models.send_mail') as mail, patch('librus.models.send_mail') as librus_mail:
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
