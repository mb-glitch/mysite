"""Raportowanie z komend Django do endpointu monitoringu z tokenem DRF."""
import logging
import time

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


class MonitoringReporter:
    def __init__(self):
        self.started_at = time.perf_counter()

    def send(self, *, status_code=0, message=None):
        token = settings.LIBRUS_MONITORING_TOKEN
        if not token:
            logger.error('Brak LIBRUS_MONITORING_TOKEN (lub dotychczasowego TOKEN); raport nie został wysłany.')
            return False
        payload = dict(message or {})
        payload['status_code'] = status_code
        payload['execute_time_ms'] = int((time.perf_counter() - self.started_at) * 1000)
        try:
            response = requests.post(
                settings.URL_MONITORING,
                json=payload,
                headers={'Authorization': f'Token {token}'},
                timeout=10,
            )
        except requests.RequestException as exc:
            logger.error('Nie udało się wysłać raportu monitoringu (%s).', type(exc).__name__)
            return False
        if response.status_code != 201:
            logger.error('Monitoring odrzucił raport: HTTP %s.', response.status_code)
            return False
        logger.info('Raport monitoringu przyjęty: HTTP 201.')
        return True
