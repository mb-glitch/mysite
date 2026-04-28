import logging
from django.core.management.base import BaseCommand
from monitoring.models import MonitoredService

logger = logging.getLogger(__name__)

class Command(BaseCommand):
    help = 'Sprawdza usługi na monitoringu i wysyła powiadomienia mailowe'
 
    def handle(self, *args, **options):
        logger.info("--- START PROGRAMU ---")

        for m in MonitoredService.objects.all():
            try:
                logger.info(m)
                m.sprawdz_status_i_wyslij_powiadomienie()
            except Exception as e:
                logger.error(f"Błąd podczas obsługi monitoringu {m.id}: {e}")

        logger.info("--- KONIEC PROGRAMU ---")
