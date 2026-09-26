# ==============================================================================
# monitoring/views.py
# Aplikacja: MONITORING
# Czysty endpoint DRF z natywną autoryzacją TokenAuthentication
# ==============================================================================
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.authentication import TokenAuthentication
from django.utils import timezone
from .models import MonitoredService, LogEntry
import logging

logger = logging.getLogger(__name__)


class MonitoringReceiverView(APIView):
    """
    Oficjalny endpoint odbiorczy DRF: POST /monitoring/
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

    def post(self, request):
        service_user = request.user

        # 1. Sprawdź czy konto techniczne ma profil MonitoredService
        try:
            service = MonitoredService.objects.select_related('user').get(
                service_user=service_user,
                is_active=True
            )
        except MonitoredService.DoesNotExist:
            return Response(
                {"error": f"Konto '{service_user.username}' nie jest zarejestrowane jako aktywna usługa w MonitoredService."},
                status=status.HTTP_403_FORBIDDEN
            )

        payload = request.data if isinstance(request.data, dict) else {}

        # 2. Status code (0 = OK, 1 = Warning, 2 = Błąd)
        status_code = payload.get("status_code")
        if status_code is None:
            if any(k in payload for k in ("error", "fail", "failed", "blad")):
                status_code = 2
            elif any(k in payload for k in ("warning", "warn", "alert")):
                status_code = 1
            else:
                status_code = 0  # Czyste puknięcie / odczyt pomiaru to sukces!

        try:
            status_code = int(status_code)
        except (ValueError, TypeError):
            status_code = 0

        # 3. Zapisujemy pełny payload do JSONField
        message_data = payload if payload else {"info": "Puste puknięcie (ping OK)"}

        log_entry = LogEntry.objects.create(
            service=service,
            status_code=status_code,
            message=message_data
        )

        # 4. Aktualizacja czasu kontaktu
        service.last_check_at = timezone.now()
        service.last_status = status_code
        service.save(update_fields=['last_check_at', 'last_status'])

        try:
            service.sprawdz_status_i_wyslij_powiadomienie()
        except Exception as e:
            logger.warning(f"Błąd powiadomień: {e}")

        return Response({
            "status": "ok",
            "service": service.service_name,
            "owner": service.user.username,
            "machine_account": service_user.username,
            "status_code": status_code,
            "checked_at": log_entry.checked_at.isoformat()
        }, status=status.HTTP_201_CREATED)

