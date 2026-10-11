from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.db.models.functions import Coalesce
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache

from monitoring.models import LogEntry, MonitoredService
from .grades import summarize_grades
from .models import Dziecko, HistoriaOceny


@never_cache
@login_required(login_url='backup_login')
def dashboard(request):
    service = None
    if settings.LIBRUS_MONITORING_TOKEN:
        service = MonitoredService.objects.filter(
            service_user__auth_token__key=settings.LIBRUS_MONITORING_TOKEN,
        ).first()
    report_filter = {'service': service} if service else {'service__service_name__istartswith': 'librus'}
    latest_report = (
        LogEntry.objects.filter(**report_filter)
        .order_by('-checked_at', '-pk')
        .first()
    )
    results_by_child = {}
    if latest_report and isinstance(latest_report.message, dict):
        results = latest_report.message.get('results')
        for result in results if isinstance(results, list) else []:
            if isinstance(result, dict) and isinstance(result.get('dziecko'), str):
                results_by_child[result['dziecko'].casefold()] = result

    children = []
    for child in Dziecko.objects.select_related('user').order_by('user__first_name', 'user__username'):
        grade_summary = summarize_grades(child.oceny.filter(archiwizowano_at__isnull=True))
        messages = child.wiadomosci.annotate(display_date=Coalesce('librus_data', 'created')).order_by('-display_date', '-pk')[:10]
        announcements = child.ogloszenia.annotate(display_date=Coalesce('librus_data', 'created')).order_by('-display_date', '-pk')[:10]
        activity = [
            {'kind': 'Wiadomość', 'date': message.display_date, 'sender': message.nadawca,
             'title': message.temat, 'content': message.tresc, 'pk': message.pk}
            for message in messages
        ] + [
            {'kind': 'Ogłoszenie', 'date': announcement.display_date, 'sender': announcement.nadawca,
             'title': announcement.tytul, 'content': announcement.tresc, 'pk': announcement.pk}
            for announcement in announcements
        ]
        activity.sort(key=lambda entry: (entry['date'], entry['pk'], entry['kind']), reverse=True)
        children.append({
            'child': child,
            'name': child.user.get_full_name() or child.user.username,
            'grade_summary': grade_summary,
            'recent_grades': child.oceny.filter(archiwizowano_at__isnull=True).order_by('-data', '-pk')[:10],
            'sync_result': results_by_child.get(child.user.username.casefold()),
            'activity': activity[:10],
        })

    return render(request, 'librus/dashboard.html', {
        'children': children,
        'latest_report': latest_report,
    })


@never_cache
@login_required(login_url='backup_login')
def grades(request, child_id):
    child = get_object_or_404(Dziecko.objects.select_related('user'), pk=child_id)
    current_grades = list(child.oceny.filter(archiwizowano_at__isnull=True))
    return render(request, 'librus/grades.html', {
        'child': child,
        'name': child.user.get_full_name() or child.user.username,
        'grades': current_grades,
        'grade_summary': summarize_grades(current_grades),
        'archived_grades': child.oceny.filter(archiwizowano_at__isnull=False),
        'history': HistoriaOceny.objects.filter(ocena__dziecko=child)[:100],
    })
