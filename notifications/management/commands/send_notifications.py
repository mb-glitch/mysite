import fcntl
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from notifications.models import PushDelivery
from notifications.services import deliver_pending


class Command(BaseCommand):
    help = 'Obsługuje kolejkę e-mail i push. Uruchamiaj przez cron co 5 minut; powoduje rzeczywistą wysyłkę.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)
        parser.add_argument('--channel', choices=['push', 'email'])
        parser.add_argument('--dry-run', action='store_true', help='Tylko liczy wpisy; nie zmienia danych ani nie wysyła.')

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 500:
            raise CommandError('--limit musi być pomiędzy 1 a 500.')
        if options['dry_run']:
            rows = PushDelivery.objects.all()
            if options['channel']:
                rows = rows.filter(channel=options['channel'])
            for state in ['pending', 'processing', 'sent', 'skipped', 'failed']:
                self.stdout.write(f'{state}: {rows.filter(state=state).count()}')
            return
        # flock is released by the OS even if cron kills this process.
        lock_path = Path(settings.BASE_DIR) / 'logs' / 'notifications.lock'
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                self.stdout.write('Inny proces obsługuje kolejkę; pomijam uruchomienie.')
                return
            count = deliver_pending(limit=options['limit'], channel=options['channel'])
            self.stdout.write(f'Przyjęte przez usługę: {count}')
