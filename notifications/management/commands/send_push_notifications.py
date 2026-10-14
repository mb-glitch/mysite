from django.core.management.base import BaseCommand, CommandError
from notifications.services import deliver_pending


class Command(BaseCommand):
    help = 'Wysyła oczekujące powiadomienia Firebase; przy włączonym FCM powoduje prawdziwą wysyłkę.'

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=50)

    def handle(self, *args, **options):
        if not 1 <= options['limit'] <= 500:
            raise CommandError('--limit musi być pomiędzy 1 a 500.')
        self.stdout.write(f"Przyjęte przez FCM: {deliver_pending(limit=options['limit'])}")
