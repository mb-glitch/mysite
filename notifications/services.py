"""FCM queue independent of email success flags. No secrets or message bodies in logs."""
from datetime import timedelta
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import PushDelivery, PushDevice

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def group_notification_recipients(group):
    """Choose push for eligible active installations, email for other group members."""
    from django.contrib.auth.models import User
    from mysite.mobile_api import mobile_permissions

    push_users = set()
    if settings.FCM_ENABLED:
        devices = PushDevice.objects.filter(
            is_active=True, user__is_active=True, user__groups__name=group,
        ).select_related('user').distinct()
        push_users = {device.user_id for device in devices
                      if mobile_permissions(device.user).get(group.lower(), False)}
    emails = list(User.objects.filter(is_active=True, groups__name=group)
                  .exclude(pk__in=push_users).exclude(email='')
                  .order_by('email').values_list('email', flat=True).distinct())
    return emails, bool(push_users)


def queue_group_push(*, group, event_key, title, body):
    if not settings.FCM_ENABLED:
        return 0
    try:
        from mysite.mobile_api import mobile_permissions
        devices = PushDevice.objects.filter(is_active=True, user__is_active=True, user__groups__name=group).select_related('user').distinct()
        delivery_ids = []
        for device in devices.iterator(chunk_size=100):
            if not mobile_permissions(device.user).get(group.lower(), False):
                continue
            delivery, created = PushDelivery.objects.get_or_create(
                device=device, event_key=event_key,
                defaults={'user': device.user, 'group': group, 'title': title[:120], 'body': body[:255]},
            )
            if created:
                delivery_ids.append(delivery.pk)
        if delivery_ids and settings.FCM_SEND_IMMEDIATELY:
            transaction.on_commit(lambda: deliver_queued_safely(delivery_ids))
        return len(delivery_ids)
    except Exception as error:
        logger.error('Nie udało się przygotować push (%s).', type(error).__name__)
        return 0


def deliver_queued_safely(delivery_ids):
    try:
        deliver_pending(delivery_ids=delivery_ids, limit=len(delivery_ids))
    except Exception as error:
        logger.error('Błąd uruchomienia wysyłki push (%s).', type(error).__name__)


def send_fcm(delivery):
    import firebase_admin
    from firebase_admin import credentials, messaging

    try:
        app = firebase_admin.get_app('mysite-push')
    except ValueError:
        if not settings.FIREBASE_CREDENTIALS_FILE:
            raise ValueError('Brak konfiguracji konta serwisowego Firebase.')
        app = firebase_admin.initialize_app(
            credentials.Certificate(settings.FIREBASE_CREDENTIALS_FILE),
            options={'httpTimeout': 10}, name='mysite-push',
        )
    # Data messages let the app check the current account and permissions before displaying anything.
    return messaging.send(messaging.Message(
        token=delivery.device.token,
        data={'delivery_id': str(delivery.pk), 'recipient_id': str(delivery.user_id),
              'target': delivery.group.lower(), 'title': delivery.title, 'body': delivery.body},
        android=messaging.AndroidConfig(priority='high', ttl=timedelta(hours=1)),
    ), app=app)


def deliver_pending(*, limit=50, delivery_ids=None):
    if not settings.FCM_ENABLED:
        return 0
    from mysite.mobile_api import mobile_permissions
    from firebase_admin import messaging

    pending = PushDelivery.objects.filter(state='pending', retry_at__lte=timezone.now())
    if delivery_ids is not None:
        pending = pending.filter(pk__in=delivery_ids)
    ids = list(pending.order_by('created_at').values_list('pk', flat=True)[:limit])
    sent = 0
    for delivery_id in ids:
        try:
            with transaction.atomic():
                delivery = PushDelivery.objects.select_for_update().select_related('device__user').get(pk=delivery_id)
                if delivery.state != 'pending' or delivery.retry_at > timezone.now():
                    continue
                device = delivery.device
                allowed = device.user_id == delivery.user_id and device.user.is_active and device.is_active and device.user.groups.filter(name=delivery.group).exists()
                if not allowed or not mobile_permissions(device.user).get(delivery.group.lower(), False):
                    delivery.state = 'skipped'
                else:
                    delivery.attempts += 1
                    try:
                        send_fcm(delivery)
                        delivery.state = 'sent'
                        delivery.sent_at = timezone.now()
                        delivery.last_error_code = ''
                        sent += 1
                    except messaging.UnregisteredError:
                        # A deleted FCM token must not disable a freshly rotated token on the same device.
                        PushDevice.objects.filter(pk=device.pk, token=device.token).update(is_active=False)
                        delivery.state = 'failed'
                        delivery.last_error_code = 'UnregisteredError'
                    except Exception as error:
                        delivery.last_error_code = type(error).__name__[:64]
                        delivery.state = 'failed' if delivery.attempts >= MAX_ATTEMPTS else 'pending'
                        delivery.retry_at = timezone.now() + timedelta(minutes=delivery.attempts)
                        logger.warning('Push nie został przyjęty przez FCM (%s).', delivery.last_error_code)
                delivery.save()
        except PushDelivery.DoesNotExist:
            continue
        except Exception as error:
            logger.error('Błąd obsługi kolejki push (%s).', type(error).__name__)
    return sent
