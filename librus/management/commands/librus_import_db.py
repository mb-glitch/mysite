import sqlite3
import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from librus.models import Dziecko, Wiadomosc, Ogloszenie

class Command(BaseCommand):
    help = 'Importuje dane ze starej bazy sqlite librus.db'

    def handle(self, *args, **options):
        db_path = 'librus.db'
        if not datetime.datetime.now(): # Tylko dla przykładu, sprawdzamy dostępność
             self.stdout.write(self.style.ERROR(f'Nie znaleziono pliku {db_path}'))
             return

        old_conn = sqlite3.connect(db_path)
        old_conn.row_factory = sqlite3.Row
        cur = old_conn.cursor()

        self.stdout.write(self.style.SUCCESS("Rozpoczynam import danych...wiadomości"))

        cur.execute("""
            SELECT w.*, t.tresc 
            FROM wiadomosci w 
            LEFT JOIN tresci t ON w.id = t.wiadomosc_id AND w.dziecko = t.dziecko
        """)
        
        rows = cur.fetchall()
        count = 0

        for row in rows:
            imie_z_bazy = row['dziecko'].strip()
            imie_z_bazy = 'Joanna' if imie_z_bazy == 'Asia' else 'Zuzanna'
            # 1. Pobierz lub stwórz dziecko
            dziecko_obj, created = Dziecko.objects.get_or_create(
                imie__iexact=imie_z_bazy,
                defaults={
                    'imie': imie_z_bazy.capitalize(),
                    'nazwisko': '---',
                    'librus_login': f"login_{imie_z_bazy.lower()}"
                }
            )

            # 2. Parsowanie daty
            data_otrzymania = None
            if row['data_otrzymania']:
                dt = datetime.datetime.strptime(row['data_otrzymania'], "%Y-%m-%d")
                data_otrzymania = timezone.make_aware(dt)

            # 3. Zapis wiadomości
            _, msg_created = Wiadomosc.objects.get_or_create(
                wiadomosc_id=row['id'],
                dziecko=dziecko_obj,
                defaults={
                    'nadawca': row['temat'],
                    'temat': row['nadawca'],
                    'tresc': row['tresc'] or '',
                    'sent_at': timezone.now() if row['wyslane'] == 1 else None,
                    'librus_data': data_otrzymania
                }
            )
            
            if msg_created:
                count += 1
        self.stdout.write(self.style.SUCCESS(f'Import zakończony! Dodano {count} nowych rekordów.'))
        
        self.stdout.write(self.style.SUCCESS("Rozpoczynam import danych...ogłoszenia"))

        cur.execute("""
            SELECT o.* 
            FROM ogloszenia o 
        """)
        
        rows = cur.fetchall()
        count = 0

        for row in rows:
            imie_z_bazy = row['dziecko'].strip()
            imie_z_bazy = 'Joanna' if imie_z_bazy == 'Asia' else 'Zuzanna'
            # 1. Pobierz lub stwórz dziecko
            dziecko_obj, created = Dziecko.objects.get_or_create(
                imie__iexact=imie_z_bazy,
                defaults={
                    'imie': imie_z_bazy.capitalize(),
                    'nazwisko': '---',
                    'librus_login': f"login_{imie_z_bazy.lower()}"
                }
            )

            # 2. Parsowanie daty
            data_otrzymania = None
            if row['data']:
                dt = datetime.datetime.strptime(row['data'], "%Y-%m-%d")
                data_otrzymania = timezone.make_aware(dt)

            # 3. Zapis ogłoszenia
            _, msg_created = Ogloszenie.objects.get_or_create(
                ogloszenie_id=row['id'],
                dziecko=dziecko_obj,
                defaults={
                    'tytul': row['tytul'],
                    'tresc': row['tresc'] or '',
                    'sent_at': timezone.now() if row['wyslane'] == 1 else None,
                    'librus_data': data_otrzymania
                }
            )
            
            if msg_created:
                count += 1
        self.stdout.write(self.style.SUCCESS(f'Import zakończony! Dodano {count} nowych rekordów.'))

        old_conn.close()
