from .send_notifications import Command as QueueCommand


class Command(QueueCommand):
    help = 'Zgodność ze starą komendą: obsługuje tylko push. Dla obu kanałów użyj send_notifications.'

    def handle(self, *args, **options):
        options['channel'] = 'push'
        return super().handle(*args, **options)
