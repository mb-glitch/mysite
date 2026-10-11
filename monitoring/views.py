# ==============================================================================
# monitoring/views.py
# Aplikacja: MONITORING
# Czysty endpoint DRF z natywną autoryzacją TokenAuthentication
# ==============================================================================
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.authentication import TokenAuthentication
from rest_framework.parsers import JSONParser
from django.db import transaction
from .models import MonitoredService, LogEntry
import logging

logger = logging.getLogger(__name__)


class MonitoringReceiverView(APIView):
    """
    Oficjalny endpoint odbiorczy DRF: POST /api/monitoring/
    Wymaga nagłówka:
        Authorization: Token <token_konta_technicznego>

    Zasada działania:
    1. DRF automatycznie uwierzytelnia konto techniczne w request.user.
    2. Usługa i opiekun są ustalani natychmiast z profilu MonitoredService.
    3. Puste puknięcie (czyste POST bez danych) -> status 0 (OK) i aktualizacja czasu.
    4. Dowolne dane w JSON (np. temperatura, logi) trafiają bezstratnie do message.
    """
    authentication_classes = [TokenAuthentication]
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request):
        service_user = request.user

        # 1. Sprawdź czy konto techniczne ma profil MonitoredService
        try:
            service = MonitoredService.objects.select_related('owner').get(
                service_user=service_user,
                is_active=True
            )
        except MonitoredService.DoesNotExist:
            return Response(
                {"error": f"Konto '{service_user.username}' nie jest zarejestrowane jako aktywna usługa w MonitoredService."},
                status=status.HTTP_403_FORBIDDEN
            )

        payload = request.data
        metadata = payload if isinstance(payload, dict) else {}

        # 2. Status code (0 = OK, 1 = Warning, 2 = Błąd)
        status_code = metadata.get("status_code", 0)
        if type(status_code) is not int or status_code not in (0, 1, 2, 100):
            return Response({'error': 'status_code musi być liczbą 0, 1, 2 lub 100.'},
                            status=status.HTTP_400_BAD_REQUEST)

        # 3. Zapisujemy pełny payload do JSONField
        message_data = payload

        with transaction.atomic():
            log_entry = LogEntry.objects.create(
                service=service, status_code=status_code, message=message_data,
            )
            service.last_check_at = log_entry.checked_at
            service.save(update_fields=['last_check_at'])

        try:
            service.sprawdz_status_i_wyslij_powiadomienie()
        except Exception as e:
            logger.warning(f"Błąd powiadomień: {e}")

        return Response({
            "status": "ok",
            "service": service.service_name,
            "owner": service.owner.username,
            "machine_account": service_user.username,
            "status_code": status_code,
            "checked_at": log_entry.checked_at.isoformat()
        }, status=status.HTTP_201_CREATED)

