"""Durable email/push outbox. Network work happens outside database transactions."""
from datetime import timedelta
import hashlib
import logging
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.mail import get_connection, send_mail
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from .models import PushDelivery, PushDevice

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
LEASE_DURATION = timedelta(minutes=30)


def _push_devices(group):
    from mysite.mobile_api import mobile_permissions

    if not settings.FCM_ENABLED:
        return []
    devices = PushDevice.objects.filter(
        is_active=True, user__is_active=True, user__groups__name=group,
    ).select_related('user').distinct()
    return [device for device in devices
            if mobile_permissions(device.user).get(group.lower(), False)]


def queue_group_notifications(*, group, event_key, email_subject, email_body, title, body):
    """Snapshot recipients and the chosen channel in the same transaction as the source."""
    devices_by_user = {}
    for device in _push_devices(group):
        devices_by_user.setdefault(device.user_id, []).append(device)
    users = get_user_model().objects.filter(is_active=True, groups__name=group).distinct()
    count = 0
    with transaction.atomic():
        for user in users:
            existing = PushDelivery.objects.filter(event_key=event_key, user=user)
            if existing.exists():
                count += existing.count()
                continue
            devices = devices_by_user.get(user.pk)
            # One record per phone, or one email record per user, including missing addresses.
            for device in devices or [None]:
                channel = 'push' if device else 'email'
                key = hashlib.sha256(f'{event_key}:{user.pk}:{channel}:{device.pk if device else ""}'.encode()).hexdigest()
                PushDelivery.objects.get_or_create(
                    dedup_key=key,
                    defaults={
                        'user': user, 'device': device, 'event_key': event_key, 'group': group,
                        'channel': channel, 'recipient_name': user.username,
                        'recipient_email': user.email if channel == 'email' else '',
                        'title': title[:120] if device else email_subject,
                        'body': body[:255] if device else email_body,
                        'state': 'pending' if device or user.email else 'skipped',
                        'last_error_code': '' if device or user.email else 'MissingEmail',
                    },
                )
                # Existing rows also mean the event was durably recorded.
                count += 1
    return count


def send_fcm(delivery):
    import firebase_admin
    from firebase_admin import credentials, messaging

    try:
        app = firebase_admin.get_app('mysite-push')
    except ValueError:
        app = firebase_admin.initialize_app(
            credentials.Certificate(settings.FIREBASE_CREDENTIALS_FILE),
            options={'httpTimeout': 10}, name='mysite-push',
        )
    return messaging.send(messaging.Message(
        token=delivery.device.token,
        data={'delivery_id': str(delivery.pk), 'recipient_id': str(delivery.user_id),
              'target': delivery.group.lower(), 'title': delivery.title, 'body': delivery.body},
        android=messaging.AndroidConfig(priority='high', ttl=timedelta(hours=1)),
    ), app=app)


def _eligible(delivery):
    user = delivery.user
    if not user or not user.is_active or not user.groups.filter(name=delivery.group).exists():
        return False
    if delivery.channel == 'email':
        return bool(delivery.recipient_email)
    from mysite.mobile_api import mobile_permissions
    device = delivery.device
    return bool(device and device.is_active and device.user_id == delivery.user_id
                and mobile_permissions(user).get(delivery.group.lower(), False))


def recover_interrupted():
    stale = PushDelivery.objects.filter(state='processing', claimed_at__lt=timezone.now() - LEASE_DURATION)
    stale.filter(attempts__gte=MAX_ATTEMPTS).update(
        state='failed', claim_id=None, claimed_at=None, last_error_code='InterruptedDelivery',
    )
    return stale.filter(attempts__lt=MAX_ATTEMPTS).update(
        state='pending', claim_id=None, claimed_at=None, retry_at=timezone.now(),
        last_error_code='InterruptedDelivery',
    )


def retry_failed(queryset):
    """Only failed records can be retried; successes must never be replayed."""
    return queryset.filter(state='failed').update(
        state='pending', attempts=0, retry_at=timezone.now(), claim_id=None,
        claimed_at=None, last_error_code='',
    )


def deliver_pending(*, limit=50, delivery_ids=None, channel=None):
    recover_interrupted()
    pending = PushDelivery.objects.filter(state='pending', retry_at__lte=timezone.now())
    if not settings.FCM_ENABLED:
        pending = pending.exclude(channel='push')
    if channel:
        pending = pending.filter(channel=channel)
    if delivery_ids is not None:
        pending = pending.filter(pk__in=delivery_ids)
    ids = list(pending.order_by('created_at', 'pk').values_list('pk', flat=True)[:limit])
    sent = 0
    for delivery_id in ids:
        claim_id = uuid.uuid4()
        # Compare-and-set claims the row; no lock is held during network I/O.
        claimed = PushDelivery.objects.filter(
            pk=delivery_id, state='pending', retry_at__lte=timezone.now(), attempts__lt=MAX_ATTEMPTS,
        ).update(state='processing', claim_id=claim_id, claimed_at=timezone.now(), attempts=F('attempts') + 1)
        if not claimed:
            continue
        delivery = PushDelivery.objects.select_related('device', 'user').get(pk=delivery_id)
        update = {'claim_id': None, 'claimed_at': None}
        if not _eligible(delivery):
            update.update(state='skipped', last_error_code='RecipientUnavailable')
        else:
            try:
                if delivery.channel == 'push':
                    send_fcm(delivery)
                elif send_mail(delivery.title, delivery.body, None, [delivery.recipient_email],
                               fail_silently=False, connection=get_connection(timeout=15)) != 1:
                    raise RuntimeError('EmailNotAccepted')
                update.update(state='sent', sent_at=timezone.now(), last_error_code='')
                sent += 1
            except Exception as error:
                from firebase_admin import messaging
                code = type(error).__name__[:64]
                if delivery.channel == 'push' and isinstance(error, messaging.UnregisteredError):
                    PushDevice.objects.filter(pk=delivery.device_id, token=delivery.device.token).update(is_active=False)
                    update.update(state='failed', last_error_code=code)
                else:
                    update.update(state='failed' if delivery.attempts >= MAX_ATTEMPTS else 'pending',
                                  last_error_code=code,
                                  retry_at=timezone.now() + timedelta(minutes=5 if delivery.attempts == 1 else 15))
                logger.warning('Powiadomienie %s: %s (%s).', delivery.pk, delivery.channel, code)
        # Logout/account switching may have skipped the row while the service was called.
        PushDelivery.objects.filter(pk=delivery_id, state='processing', claim_id=claim_id).update(**update)
    return sent
