"""Mobile authentication and read-only dashboards using existing DRF tokens."""
import re

from django.conf import settings
from django.contrib.auth import authenticate
from django.db.models.functions import Coalesce
from django.utils import timezone
from rest_framework import serializers
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.parsers import JSONParser
from rest_framework.throttling import AnonRateThrottle
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


def mobile_permissions(user):
    if is_dedicated_service_account(user) or MonitoredService.objects.filter(service_user=user).exists():
        return {'librus': False, 'monitoring': False}
    groups = set(user.groups.values_list('name', flat=True))
    return {'librus': user.is_superuser or 'Librus' in groups,
            'monitoring': user.is_superuser or 'Monitoring' in groups}


class MobileProfileView(DashboardView):
    def get(self, request):
        return Response({'id': request.user.pk, 'name': request.user.get_full_name() or request.user.username,
                         'permissions': mobile_permissions(request.user)})


class MobileLoginThrottle(AnonRateThrottle):
    scope = 'mobile_login'
    rate = '5/min'


class MobileLoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150)
    password = serializers.CharField(max_length=1024, trim_whitespace=False, write_only=True)


class MobileLoginView(DashboardView):
    authentication_classes = []
    permission_classes = [AllowAny]
    parser_classes = [JSONParser]
    throttle_classes = [MobileLoginThrottle]
    http_method_names = ['post', 'options']

    def post(self, request):
        serializer = MobileLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(request=request._request, **serializer.validated_data)
        if user is None or not user.is_active:
            return Response({'detail': 'Nieprawidłowa nazwa użytkownika lub hasło.'}, status=401)
        permissions = mobile_permissions(user)
        if not any(permissions.values()):
            raise PermissionDenied('Konto nie ma dostępu do Librusa ani Monitoringu.')
        token, _ = Token.objects.get_or_create(user=user)
        return Response({'token': token.key, 'name': user.get_full_name() or user.username,
                         'permissions': permissions})


class LibrusDashboardView(DashboardView):
    def get(self, request):
        user = request.user
        if not mobile_permissions(user)['librus']:
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
        rows = service_rows(services)
        return dashboard_response(rows)


def report_summary(report):
    """Publish known report fields only; arbitrary logs and credential fields stay private."""
    message = report.message
    lines = []
    if isinstance(message, dict):
        for key in ('summary', 'error'):
            if isinstance(message.get(key), str) and message[key].strip():
                lines.append(message[key][:4000])
        failed_tasks = message.get('failed_tasks')
        for task in failed_tasks[:10] if isinstance(failed_tasks, list) else []:
            if isinstance(task, dict) and isinstance(task.get('script'), str):
                code = task.get('exit_code')
                lines.append(f"{task['script'][:100]}: kod {code if type(code) is int else 'brak'}")
        results = message.get('results')
        for result in results[:10] if isinstance(results, list) else []:
            if isinstance(result, dict) and isinstance(result.get('dziecko'), str):
                code = result.get('status_code')
                lines.append(f"{result['dziecko'][:150]}: wynik {code if type(code) is int else 'brak'}")
    summary = '\n'.join(lines) or 'Raport nie zawiera podsumowania.'
    return re.sub(r'(?<![a-fA-F0-9])[a-fA-F0-9]{40}(?![a-fA-F0-9])', '[ukryty token]', summary)


def service_rows(services, *, include_report=False):
    rows = []
    for service in services.order_by('owner__username', 'service_name'):
        status = service.status
        report = service.logs.order_by('-checked_at', '-pk').first()
        row = {'id': service.pk, 'name': service.service_name,
               'description': service.description,
               'owner': service.owner.get_full_name() or service.owner.username,
               'status_code': status, 'status': service.STATUS_LABELS[status],
               'last_check_at': report.checked_at if report else None,
               'last_result_code': report.status_code if report else None}
        if include_report:
            row['latest_report'] = {'checked_at': report.checked_at, 'status_code': report.status_code,
                                    'summary': report_summary(report)} if report else None
        rows.append(row)
    return rows


def dashboard_response(rows):
    return Response({'generated_at': timezone.now(),
                     'summary': {'total': len(rows), 'healthy': sum(row['status_code'] == 0 for row in rows)},
                     'services': rows})


class MonitoringDashboardView(DashboardView):
    def get(self, request):
        if not mobile_permissions(request.user)['monitoring']:
            raise PermissionDenied('Wymagane członkostwo w grupie Monitoring.')
        services = MonitoredService.objects.filter(is_archived=False).select_related('owner', 'service_user')
        return dashboard_response(service_rows(services, include_report=True))
