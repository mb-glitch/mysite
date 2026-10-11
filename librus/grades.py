"""Synchronize complete grade sets, retaining previous values and removed grades."""

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re

from django.db import transaction
from django.utils import timezone

from .models import Dziecko, HistoriaOceny, Ocena


GRADE_FIELDS = ('wartosc', 'przedmiot', 'data', 'opis', 'waga', 'liczona_do_sredniej')
GRADE_PATTERN = re.compile(r'^([1-6])([+-]?)$')
QUARTER = Decimal('0.25')
TENTH = Decimal('0.1')


def grade_number(value):
    match = GRADE_PATTERN.fullmatch(value.strip())
    if not match:
        return None
    number = Decimal(match.group(1))
    if match.group(2) == '+':
        number += QUARTER
    elif match.group(2) == '-':
        number -= QUARTER
    return number


def format_mean(value):
    return str(value.quantize(TENTH, rounding=ROUND_HALF_UP)).replace('.', ',')


def summarize_grades(grades):
    subjects = {}
    excluded = 0
    for grade in grades:
        value = grade_number(grade.wartosc)
        if value is None or not grade.liczona_do_sredniej or grade.waga is None or grade.waga <= 0:
            excluded += 1
            continue
        current = subjects.setdefault(grade.przedmiot, {'sum': Decimal(0), 'weight': Decimal(0), 'count': 0})
        current['sum'] += value * grade.waga
        current['weight'] += grade.waga
        current['count'] += 1

    rows = []
    means = []
    for name, numbers in sorted(subjects.items(), key=lambda item: item[0].casefold()):
        mean = numbers['sum'] / numbers['weight']
        means.append(mean)
        rows.append({'name': name, 'mean': format_mean(mean), 'count': numbers['count']})
    projected = format_mean(sum(means) / len(means)) if means else None
    return {'subjects': rows, 'projected': projected, 'excluded': excluded}


def sync_grades(child, grades):
    # Validate the whole response before making any changes or archiving rows.
    seen = set()
    for grade in grades:
        identifier = grade.get('librus_id')
        if not isinstance(identifier, str) or not identifier or len(identifier) > 64 or identifier in seen:
            raise ValueError('Nieprawidłowy lub powtórzony identyfikator oceny.')
        if not isinstance(grade.get('data'), date):
            raise ValueError('Nieprawidłowa data oceny.')
        for field, limit in (('wartosc', 32), ('przedmiot', 255), ('opis', None)):
            value = grade.get(field)
            if not isinstance(value, str) or (field != 'opis' and not value) or (limit and len(value) > limit):
                raise ValueError('Nieprawidłowe dane oceny.')
        if not isinstance(grade.get('liczona_do_sredniej'), bool):
            raise ValueError('Brak informacji, czy ocena jest liczona do średniej.')
        if grade.get('waga') is not None:
            try:
                weight = Decimal(grade['waga'])
            except (InvalidOperation, TypeError):
                raise ValueError('Nieprawidłowa waga oceny.') from None
            if not weight.is_finite() or weight < 0 or weight >= 10000 or weight.as_tuple().exponent < -2:
                raise ValueError('Nieprawidłowa waga oceny.')
        seen.add(identifier)

    now = timezone.now()
    counts = {'new': 0, 'changed': 0, 'archived': 0, 'restored': 0, 'total': len(grades)}
    with transaction.atomic():
        # Serialize updates for the same child, including from other importers.
        locked_child = Dziecko.objects.select_for_update().get(pk=child.pk)
        cutoff = locked_child.oceny_archiwum_do
        existing = {grade.librus_id: grade for grade in child.oceny.all()}

        def remember(grade, reason):
            HistoriaOceny.objects.create(
                ocena=grade, powod=reason, zapisano_at=now,
                **{field: getattr(grade, field) for field in GRADE_FIELDS},
            )

        for item in grades:
            values = {field: item[field] for field in GRADE_FIELDS}
            grade = existing.get(item['librus_id'])
            if grade is None:
                Ocena.objects.create(
                    dziecko=child, librus_id=item['librus_id'],
                    zmieniono_at=now, ostatnio_widziana_at=now, **values,
                    archiwizowano_at=now if cutoff and item['data'] <= cutoff else None,
                )
                counts['new'] += 1
                continue
            changed = any(getattr(grade, field) != value for field, value in values.items())
            should_archive = bool(cutoff and item['data'] <= cutoff)
            restored = grade.archiwizowano_at is not None and not should_archive
            newly_archived = grade.archiwizowano_at is None and should_archive
            if changed or restored or newly_archived:
                reason = 'semestr' if newly_archived else 'powrot' if restored else 'zmiana'
                remember(grade, reason)
                for field, value in values.items():
                    setattr(grade, field, value)
                grade.zmieniono_at = now
                grade.archiwizowano_at = now if newly_archived else None if restored else grade.archiwizowano_at
                counts['archived' if newly_archived else 'restored' if restored else 'changed'] += 1
            grade.ostatnio_widziana_at = now
            grade.save()

        for identifier, grade in existing.items():
            if identifier not in seen and grade.archiwizowano_at is None:
                remember(grade, 'brak')
                grade.archiwizowano_at = now
                grade.save(update_fields=['archiwizowano_at'])
                counts['archived'] += 1
        Dziecko.objects.filter(pk=child.pk).update(oceny_pobrano_at=now)
    return counts


def archive_grades(child, cutoff):
    """Archive active grades dated on/before cutoff; keep cutoff for future syncs."""
    now = timezone.now()
    count = 0
    with transaction.atomic():
        locked_child = Dziecko.objects.select_for_update().get(pk=child.pk)
        if locked_child.oceny_archiwum_do and cutoff < locked_child.oceny_archiwum_do:
            raise ValueError('Data archiwizacji jest wcześniejsza niż poprzednio użyta.')
        for grade in child.oceny.filter(archiwizowano_at__isnull=True, data__lte=cutoff):
            HistoriaOceny.objects.create(
                ocena=grade, powod='semestr', zapisano_at=now,
                **{field: getattr(grade, field) for field in GRADE_FIELDS},
            )
            grade.archiwizowano_at = now
            grade.save(update_fields=['archiwizowano_at'])
            count += 1
        Dziecko.objects.filter(pk=child.pk).update(oceny_archiwum_do=cutoff)
    return count
