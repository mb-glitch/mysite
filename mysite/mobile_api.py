"""Read-only mobile dashboards; existing DRF tokens, no remote synchronization."""
from django.conf import settings
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from librus.grades import summarize_grades
from librus.models import Dziecko
from monitoring.models import LogEntry, MonitoredService, is_dedicated_service_account


class DashboardView(APIView):
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]
    renderer_classes = [JSONRenderer]
    http_method_names = ['get', 'head', 'options']

    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response


class LibrusDashboardView(DashboardView):
    def get(self, request):
        user = request.user
        # Machine tokens must never inherit their owner's access to children's data.
        if is_dedicated_service_account(user) or MonitoredService.objects.filter(service_user=user).exists():
            raise PermissionDenied('Token techniczny nie udostępnia danych Librusa.')
        if not (user.is_superuser or user.groups.filter(name='Librus').exists()):
            raise PermissionDenied('Wymagane członkostwo w grupie Librus.')
        services = MonitoredService.objects.filter(is_archived=False)
        if settings.LIBRUS_MONITORING_TOKEN:
            services = services.filter(service_user__auth_token__key=settings.LIBRUS_MONITORING_TOKEN)
        else:
            services = services.filter(service_name__istartswith='librus')
        report = LogEntry.objects.filter(service__in=services).order_by('-checked_at', '-pk').first()
        children = []
        for child in Dziecko.objects.select_related('user').order_by('user__first_name', 'pk'):
            grades = list(child.oceny.filter(archiwizowano_at__isnull=True))
            activity = []
            for relation, kind, title in ((child.wiadomosci, 'Wiadomość', 'temat'),
                                          (child.ogloszenia, 'Ogłoszenie', 'tytul')):
                for entry in relation.annotate(display_date=Coalesce('librus_data', 'created')).order_by('-display_date', '-pk')[:10]:
                    activity.append({'id': entry.pk, 'kind': kind, 'date': entry.display_date,
                                     'sender': entry.nadawca, 'title': getattr(entry, title),
                                     'content': entry.tresc})
            activity.sort(key=lambda item: (item['date'], item['id'], item['kind']), reverse=True)
            children.append({
                'id': child.pk, 'name': child.user.get_full_name() or child.user.username,
                'grades_synced_at': child.oceny_pobrano_at,
                'grade_summary': summarize_grades(grades),
                'recent_grades': [{'date': grade.data, 'subject': grade.przedmiot,
                                   'value': grade.wartosc, 'description': grade.opis} for grade in grades[:10]],
                'activity': activity[:10],
            })
        return Response({'generated_at': timezone.now(), 'latest_report': {
            'checked_at': report.checked_at, 'status_code': report.status_code,
        } if report else None, 'children': children})


class BackupDashboardView(DashboardView):
    def get(self, request):
        user = request.user
        services = MonitoredService.objects.filter(is_archived=False).select_related('owner', 'service_user')
        if MonitoredService.objects.filter(service_user=user).exists():
            services = services.filter(service_user=user)
        elif is_dedicated_service_account(user):
            raise PermissionDenied('Nieprzypisany token techniczny.')
        elif not user.is_superuser:
            services = services.filter(owner=user)
        rows = []
        for service in services.order_by('owner__username', 'service_name'):
            status = service.status
            report = service.logs.order_by('-checked_at', '-pk').first()
            # Never return arbitrary log payloads or installation links: they may contain secrets.
            rows.append({'id': service.pk, 'name': service.service_name,
                         'description': service.description,
                         'owner': service.owner.get_full_name() or service.owner.username,
                         'status_code': status, 'status': service.STATUS_LABELS[status],
                         'last_check_at': report.checked_at if report else None,
                         'last_result_code': report.status_code if report else None})
        return Response({'generated_at': timezone.now(),
                         'summary': {'total': len(rows), 'healthy': sum(row['status_code'] == 0 for row in rows)},
                         'services': rows})
