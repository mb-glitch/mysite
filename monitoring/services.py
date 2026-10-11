"""Tworzenie kont technicznych i ich poświadczeń dla aplikacji projektu."""
from django.contrib.auth.models import User
from django.db import transaction
from rest_framework.authtoken.models import Token

from .models import MonitoredService


@transaction.atomic
def provision_service_account(*, owner, username, service_name, description=''):
    account, created = User.objects.get_or_create(username=username)
    if created:
        account.set_unusable_password()
        account.save(update_fields=['password'])
    elif account.is_staff or account.is_superuser or account.has_usable_password():
        raise ValueError('Wybrana nazwa nie należy do dedykowanego konta technicznego.')

    service, _ = MonitoredService.objects.get_or_create(
        owner=owner, service_name=service_name,
        defaults={'service_user': account, 'description': description},
    )
    if service.service_user_id is None:
        service.service_user = account
        service.save(update_fields=['service_user'])
    elif service.service_user_id != account.pk:
        raise ValueError('Usługa ma już inne konto techniczne.')
    token, _ = Token.objects.get_or_create(user=account)
    return service, token
