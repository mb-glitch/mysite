import hashlib

from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from mysite.mobile_api import DashboardView, mobile_permissions
from .models import PushDelivery, PushDevice


class DeviceSerializer(serializers.Serializer):
    token = serializers.RegexField(r'\A[A-Za-z0-9_:\-]+\Z', max_length=4096, min_length=20, trim_whitespace=False, write_only=True)


class PushDeviceView(DashboardView):
    http_method_names = ['put', 'delete', 'options']

    def put(self, request, device_id):
        if not settings.FCM_ENABLED:
            return Response({'detail': 'Powiadomienia push nie są jeszcze skonfigurowane.'}, status=503)
        permissions = mobile_permissions(request.user)
        if not any(permissions.values()):
            raise PermissionDenied('Konto nie ma dostępu do powiadomień.')
        serializer = DeviceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                device, created = PushDevice.objects.update_or_create(
                    pk=device_id, defaults={'user': request.user, 'token': serializer.validated_data['token'],
                                                   'token_hash': hashlib.sha256(serializer.validated_data['token'].encode()).hexdigest(),
                                                   'is_active': True},
                )
                # A phone switching accounts must not inherit the old account's pending deliveries.
                PushDelivery.objects.filter(device=device, state='pending').exclude(user=request.user).update(state='skipped')
                PushDelivery.objects.filter(device=device, state='pending').exclude(
                    group__in=[group for group in ('Librus', 'Monitoring') if permissions[group.lower()]],
                ).update(state='skipped')
        except IntegrityError:
            return Response({'detail': 'Ten token urządzenia jest już przypisany do innej instalacji.'}, status=409)
        return Response({'id': str(device.pk)}, status=201 if created else 200)

    def delete(self, request, device_id):
        device = PushDevice.objects.filter(pk=device_id, user=request.user).first()
        if device:
            device.is_active = False
            device.save(update_fields=['is_active', 'updated_at'])
            PushDelivery.objects.filter(device=device, state='pending').update(state='skipped')
        return Response(status=204)
