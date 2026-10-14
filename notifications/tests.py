from datetime import date, timedelta
import hashlib
import uuid
from unittest.mock import patch

from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient
from firebase_admin import messaging

from librus.models import Dziecko, Ocena, Wiadomosc, Ogloszenie
from monitoring.models import MonitoredService
from monitoring.services import provision_service_account
from .models import PushDelivery, PushDevice
from .services import queue_group_push, deliver_pending, send_fcm, group_notification_recipients


@override_settings(FCM_ENABLED=True, FCM_SEND_IMMEDIATELY=False,
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PushTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('viewer', password='test-password', email='viewer@example.com')
        self.librus = Group.objects.create(name='Librus')
        self.monitoring = Group.objects.create(name='Monitoring')
        self.user.groups.add(self.librus, self.monitoring)
        self.token = Token.objects.create(user=self.user)
        self.device = self.device_for(self.user, 'fake-fcm-token-1234567890')
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.url = f'/api/v1/push/devices/{self.device.pk}/'

    def device_for(self, user, token):
        return PushDevice.objects.create(user=user, token=token, token_hash=hashlib.sha256(token.encode()).hexdigest())

    def queue(self, key='event:1', group='Librus'):
        return queue_group_push(group=group, event_key=key, title='Nowy wpis', body='Otwórz aplikację.')

    def test_authenticated_device_registration_and_rotation_do_not_expose_token(self):
        new_id = uuid.uuid4()
        token = 'new-fcm-registration-token-12345'
        url = f'/api/v1/push/devices/{new_id}/'
        response = self.client.put(url, {'token': token}, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertNotIn(token, response.content.decode())
        self.assertEqual(response['Cache-Control'], 'no-store')
        self.assertEqual(PushDevice.objects.get(pk=new_id).user, self.user)
        response = self.client.put(url, {'token': token + '-rotated'}, format='json')
        self.assertEqual(response.status_code, 200)
        device = PushDevice.objects.get(pk=new_id)
        self.assertEqual(device.token, token + '-rotated')
        self.assertEqual(device.token_hash, hashlib.sha256(device.token.encode()).hexdigest())

    def test_registration_requires_membership_and_valid_payload(self):
        self.assertEqual(self.client.put(self.url, {'token': 'bad\nvalue'}, format='json').status_code, 400)
        self.user.groups.clear()
        self.assertEqual(self.client.put(self.url, {'token': self.device.token}, format='json').status_code, 403)
        self.client.credentials()
        self.assertEqual(self.client.put(self.url, {'token': self.device.token}, format='json').status_code, 401)

    def test_registration_disabled_and_duplicate_token_are_handled(self):
        with override_settings(FCM_ENABLED=False):
            self.assertEqual(self.client.put(self.url, {'token': self.device.token}, format='json').status_code, 503)
            self.assertEqual(self.queue(), 0)
            self.assertFalse(PushDelivery.objects.exists())
        response = self.client.put(f'/api/v1/push/devices/{uuid.uuid4()}/', {'token': self.device.token}, format='json')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(PushDevice.objects.count(), 1)

    def test_logout_disables_only_own_device_and_pending_deliveries(self):
        other = User.objects.create_user('other', password='test-password')
        other_device = self.device_for(other, 'another-fcm-token-1234567890')
        self.queue()
        self.assertEqual(self.client.delete(f'/api/v1/push/devices/{other_device.pk}/').status_code, 204)
        other_device.refresh_from_db()
        self.assertTrue(other_device.is_active)
        self.assertEqual(self.client.delete(self.url).status_code, 204)
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)
        self.assertEqual(PushDelivery.objects.get().state, 'skipped')

    def test_switching_users_drops_old_recipient_even_with_same_group(self):
        self.queue()
        new_user = User.objects.create_user('new-user', password='test-password')
        new_user.groups.add(self.librus)
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=new_user).key}')
        response = self.client.put(self.url, {'token': self.device.token + '-rotated'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.device.refresh_from_db()
        self.assertEqual(self.device.user, new_user)
        delivery = PushDelivery.objects.get()
        self.assertEqual(delivery.user, self.user)
        self.assertEqual(delivery.state, 'skipped')

    def test_queue_deduplicates_events_and_targets_group_members_only(self):
        other = User.objects.create_user('other', password='test-password')
        self.device_for(other, 'another-fcm-token-1234567890')
        disabled = self.device_for(self.user, 'disabled-fcm-token-1234567890')
        disabled.is_active = False
        disabled.save()
        self.assertEqual(self.queue(), 1)
        self.assertEqual(self.queue(), 0)
        self.assertEqual(PushDelivery.objects.count(), 1)
        self.assertEqual(PushDelivery.objects.get().device, self.device)

    @patch('notifications.services.send_fcm', return_value='fake-message-id')
    def test_send_checks_current_permissions_and_never_replays_success(self, send):
        self.queue()
        self.user.groups.remove(self.librus)
        self.assertEqual(deliver_pending(), 0)
        send.assert_not_called()
        self.assertEqual(PushDelivery.objects.get().state, 'skipped')
        self.user.groups.add(self.librus)
        self.queue('event:2')
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(deliver_pending(), 0)
        self.assertEqual(send.call_count, 1)

    @patch('notifications.services.send_fcm', side_effect=RuntimeError('fake network error'))
    def test_fcm_error_is_retried_three_times_without_touching_email_state(self, send):
        self.queue()
        delivery = PushDelivery.objects.get()
        for attempt in range(1, 4):
            PushDelivery.objects.filter(pk=delivery.pk).update(retry_at=timezone.now() - timedelta(seconds=1))
            self.assertEqual(deliver_pending(), 0)
            delivery.refresh_from_db()
            self.assertEqual(delivery.attempts, attempt)
        self.assertEqual(delivery.state, 'failed')
        self.assertEqual(deliver_pending(), 0)
        self.assertEqual(send.call_count, 3)
        self.assertEqual(delivery.last_error_code, 'RuntimeError')

    @patch('notifications.services.send_fcm', side_effect=messaging.UnregisteredError('fake expired token'))
    def test_unregistered_fcm_token_is_disabled(self, send):
        self.queue()
        deliver_pending()
        self.device.refresh_from_db()
        self.assertFalse(self.device.is_active)
        self.assertEqual(PushDelivery.objects.get().state, 'failed')

    def test_payload_uses_data_message_and_fixed_recipient_without_private_content(self):
        self.queue()
        delivery = PushDelivery.objects.get()
        with patch('firebase_admin.get_app', return_value='fake-app'), patch('firebase_admin.messaging.send') as send:
            send_fcm(delivery)
        message = send.call_args.args[0]
        self.assertIsNone(message.notification)
        self.assertEqual(message.data['recipient_id'], str(self.user.pk))
        self.assertEqual(message.data['target'], 'librus')
        self.assertNotIn(self.token.key, str(message.data))

    def test_dispatch_waits_for_commit_and_failure_does_not_escape(self):
        with override_settings(FCM_SEND_IMMEDIATELY=True), patch('notifications.services.deliver_pending', side_effect=RuntimeError):
            with self.captureOnCommitCallbacks(execute=True) as callbacks:
                self.assertEqual(self.queue(), 1)
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(PushDelivery.objects.get().state, 'pending')

    def test_librus_active_app_queues_without_email(self):
        child_user = User.objects.create_user('child', first_name='Dziecko')
        child = Dziecko.objects.create(user=child_user, librus_login='fake-login')
        grade = Ocena.objects.create(dziecko=child, librus_id='1', wartosc='5', przedmiot='Matematyka', data=date.today())
        message = Wiadomosc.objects.create(dziecko=child, wiadomosc_id='1', temat='Private subject', tresc='Private body')
        announcement = Ogloszenie.objects.create(dziecko=child, ogloszenie_id='1', tytul='Private title', tresc='Private announcement')
        self.assertTrue(grade.wyslij_powiadomienie())
        self.assertTrue(message.wyslij_powiadomienie())
        self.assertTrue(announcement.wyslij_powiadomienie())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(PushDelivery.objects.count(), 3)
        for delivery in PushDelivery.objects.all():
            self.assertNotIn('Private', delivery.body + delivery.title)
        self.assertFalse(grade.wyslij_powiadomienie())
        self.assertEqual(PushDelivery.objects.count(), 3)

    def test_email_failure_does_not_enqueue_push_or_mark_grade_as_sent(self):
        self.device.is_active = False
        self.device.save()
        child = Dziecko.objects.create(user=User.objects.create_user('child'), librus_login='fake-login')
        grade = Ocena.objects.create(dziecko=child, librus_id='1', wartosc='5', przedmiot='Matematyka', data=date.today())
        with patch('librus.models.send_mail', side_effect=RuntimeError('fake SMTP error')):
            with self.assertRaises(RuntimeError):
                grade.wyslij_powiadomienie()
        grade.refresh_from_db()
        self.assertEqual(grade.powiadomienie_status, Ocena.POWIADOMIENIE_OCZEKUJE)
        self.assertFalse(PushDelivery.objects.exists())

    def test_monitoring_email_transition_queues_only_once(self):
        service, _ = provision_service_account(owner=self.user, username='svc-test', service_name='backup')
        self.assertTrue(service.sprawdz_status_i_wyslij_powiadomienie())
        self.assertFalse(service.sprawdz_status_i_wyslij_powiadomienie())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(PushDelivery.objects.count(), 1)
        self.assertEqual(PushDelivery.objects.get().group, 'Monitoring')

    def test_mixed_group_gets_email_only_for_users_without_active_app(self):
        other = User.objects.create_user('email-only', email='email-only@example.com')
        other.groups.add(self.librus, self.monitoring)
        child = Dziecko.objects.create(user=User.objects.create_user('child'), librus_login='fake-login')
        events = [
            Ocena.objects.create(dziecko=child, librus_id='1', wartosc='5', przedmiot='Matematyka', data=date.today()),
            Wiadomosc.objects.create(dziecko=child, wiadomosc_id='1', temat='Subject', tresc='Body'),
            Ogloszenie.objects.create(dziecko=child, ogloszenie_id='1', tytul='Title', tresc='Body'),
        ]
        for event in events:
            self.assertTrue(event.wyslij_powiadomienie())
        service, _ = provision_service_account(owner=self.user, username='svc-mixed', service_name='backup')
        self.assertTrue(service.sprawdz_status_i_wyslij_powiadomienie())
        self.assertEqual(len(mail.outbox), 4)
        self.assertTrue(all(message.to == [other.email] for message in mail.outbox))
        self.assertEqual(PushDelivery.objects.count(), 4)
        self.assertEqual(set(PushDelivery.objects.values_list('user_id', flat=True)), {self.user.pk})

    def test_disabled_fcm_or_inactive_device_restores_email_channel(self):
        with override_settings(FCM_ENABLED=False):
            self.assertEqual(group_notification_recipients('Librus'), ([self.user.email], False))
        self.client.delete(self.url)
        self.assertEqual(group_notification_recipients('Librus'), ([self.user.email], False))

    def test_app_user_without_email_still_gets_grade_push(self):
        self.user.email = ''
        self.user.save()
        child = Dziecko.objects.create(user=User.objects.create_user('child'), librus_login='fake-login')
        grade = Ocena.objects.create(dziecko=child, librus_id='1', wartosc='5', przedmiot='Matematyka', data=date.today())
        self.assertTrue(grade.wyslij_powiadomienie())
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(PushDelivery.objects.count(), 1)
