from datetime import date

from django.core.management.base import BaseCommand, CommandError

from librus.grades import archive_grades
from librus.models import Dziecko


class Command(BaseCommand):
    help = 'Ręcznie archiwizuje oceny do wskazanej daty włącznie.'

    def add_arguments(self, parser):
        parser.add_argument('--do', required=True, dest='cutoff', help='Data graniczna w formacie RRRR-MM-DD.')
        parser.add_argument('--dziecko', help='Opcjonalna nazwa użytkownika dziecka; domyślnie wszystkie dzieci.')

    def handle(self, *args, **options):
        try:
            cutoff = date.fromisoformat(options['cutoff'])
        except ValueError:
            raise CommandError('Podaj poprawną datę w formacie RRRR-MM-DD.') from None
        if cutoff > date.today():
            raise CommandError('Data archiwizacji nie może być w przyszłości.')

        children = Dziecko.objects.select_related('user').order_by('pk')
        if options.get('dziecko'):
            children = children.filter(user__username__iexact=options['dziecko'])
        children = list(children)
        if not children:
            raise CommandError('Nie znaleziono dzieci do archiwizacji.')
        # Validate every cutoff before changing any child.
        for child in children:
            if child.oceny_archiwum_do and cutoff < child.oceny_archiwum_do:
                raise CommandError(f'Data dla {child.user.username} jest wcześniejsza niż poprzednia archiwizacja.')
        for child in children:
            count = archive_grades(child, cutoff)
            self.stdout.write(f'{child.user.username}: zarchiwizowano {count} ocen do {cutoff}.')
