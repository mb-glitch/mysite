import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from rest_framework.authtoken.models import Token

from .models import BackupInvitation
from .views import qr_data_uri


class MobileLoginQrTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('qr_owner', password='test-password')
        User.objects.create_user('maciek', password='test-password')
        self.other = User.objects.create_user('other', password='test-password')
        self.admin = User.objects.create_superuser('admin', 'admin@example.com', 'test-password')
        self.invitation = BackupInvitation.objects.create(user=self.owner)
        self.url = reverse('create_mobile_token', args=[self.invitation.pk])
        self.dashboard = reverse('backup_dashboard')
        self.client.force_login(self.owner)

    def test_get_does_not_create_token_and_post_is_idempotent(self):
        response = self.client.get(self.dashboard)
        self.assertContains(response, 'Utwórz kod logowania')
        self.assertFalse(Token.objects.filter(user=self.owner).exists())
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertFalse(Token.objects.filter(user=self.owner).exists())
        self.assertRedirects(self.client.post(self.url), self.dashboard)
        token = Token.objects.get(user=self.owner)
        self.assertRedirects(self.client.post(self.url), self.dashboard)
        self.assertEqual(Token.objects.get(user=self.owner).key, token.key)
        self.assertEqual(Token.objects.filter(user=self.owner).count(), 1)

    def test_qr_contains_owner_token_server_and_preserves_installer(self):
        token = Token.objects.create(user=self.owner)
        with patch('backupapp.views.qr_data_uri', wraps=qr_data_uri) as encode:
            response = self.client.get(self.dashboard, secure=True)
        payloads = [call.args[0] for call in encode.call_args_list]
        mobile = next(json.loads(value) for value in payloads if value.startswith('{'))
        self.assertEqual(mobile, {'type': 'mysite-dashboard', 'version': 1,
                                 'server_url': 'https://testserver', 'token': token.key})
        self.assertNotEqual(mobile['token'], self.invitation.token_link)
        self.assertIn(f'https://testserver{reverse("setup_phone", args=[self.invitation.token_link])}', payloads)
        self.assertContains(response, 'Kod QR logowania do aplikacji')
        self.assertContains(response, 'Kod QR linku instalacyjnego')
        self.assertIn('no-store', response.headers['Cache-Control'])
        # The token belongs inside the QR, not in a URL or readable HTML field.
        self.assertNotContains(response, token.key)

    def test_other_user_cannot_view_or_create_owner_login_qr(self):
        Token.objects.create(user=self.owner)
        self.client.force_login(self.other)
        response = self.client.get(self.dashboard)
        self.assertFalse(response.context['invitations'][0]['mobile_allowed'])
        self.assertIsNone(response.context['invitations'][0]['mobile_qr_data_uri'])
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.client.force_login(self.admin)
        self.assertTrue(self.client.get(self.dashboard).context['invitations'][0]['mobile_qr_data_uri'])

    def test_used_archived_or_inactive_invitation_does_not_offer_qr(self):
        for field in ('is_used', 'is_archived'):
            with self.subTest(field=field):
                setattr(self.invitation, field, True)
                self.invitation.save()
                self.assertEqual(self.client.get(self.dashboard).context['invitations'], [])
                self.assertEqual(self.client.post(self.url).status_code, 404)
                setattr(self.invitation, field, False)
                self.invitation.save()
        self.owner.is_active = False
        self.owner.save()
        self.client.force_login(self.admin)
        self.assertFalse(self.client.get(self.dashboard).context['invitations'][0]['mobile_allowed'])
        self.assertEqual(self.client.post(self.url).status_code, 404)

    def test_creation_requires_login_and_csrf(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.owner)
        self.assertEqual(client.post(self.url).status_code, 403)
        self.assertFalse(Token.objects.filter(user=self.owner).exists())
