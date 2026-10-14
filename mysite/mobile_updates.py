"""Read-only APK distribution from a manually uploaded, versioned release directory."""
import json
from pathlib import Path
import re

from django.conf import settings
from django.http import FileResponse
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response

from .mobile_api import DashboardView, mobile_permissions

PACKAGE_NAME = 'com.macbilicki.domowydashboard'
MAX_APK_SIZE = 100 * 1024 * 1024
APK_NAME = re.compile(r'\Adashboard-[1-9][0-9]*-[a-f0-9]{12}\.apk\Z')


def release_file(name):
    root = Path(settings.ANDROID_RELEASE_DIR).resolve()
    if not APK_NAME.fullmatch(name):
        raise ValueError('Invalid APK filename')
    path = (root / name).resolve()
    if path.parent != root or not path.is_file():
        raise ValueError('APK unavailable')
    return path


def current_release():
    path = Path(settings.ANDROID_RELEASE_DIR) / 'release.json'
    if not path.exists():
        return None
    if path.stat().st_size > 16384:
        raise ValueError('Invalid release metadata')
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict):
        raise ValueError('Invalid release metadata')
    code, size = data.get('version_code'), data.get('size')
    sha = data.get('sha256', '')
    if (type(code) is not int or not 0 < code <= 2100000000
            or type(size) is not int or not 0 < size <= MAX_APK_SIZE
            or not isinstance(sha, str) or not re.fullmatch('[a-f0-9]{64}', sha)
            or data.get('package_name') != PACKAGE_NAME
            or not isinstance(data.get('version_name'), str)
            or not 1 <= len(data['version_name']) <= 100):
        raise ValueError('Invalid release metadata')
    expected = f'dashboard-{code}-{sha[:12]}.apk'
    if data.get('apk') != expected or release_file(expected).stat().st_size != size:
        raise ValueError('APK does not match metadata')
    return {key: data[key] for key in ['version_code', 'version_name', 'package_name', 'sha256', 'size', 'apk']}


class AppUpdateView(DashboardView):
    def get(self, request):
        if not any(mobile_permissions(request.user).values()):
            raise PermissionDenied('Konto nie ma dostępu do aplikacji.')
        try:
            release = current_release()
        except (OSError, ValueError, TypeError):
            return Response({'detail': 'Wydanie aplikacji jest chwilowo niedostępne.'}, status=503)
        if release is None:
            return Response({'available': False})
        return Response({'available': True, **release,
                         'download_path': f'/api/v1/app/apk/{release["apk"]}/'})


class AppApkView(DashboardView):
    def get(self, request, filename):
        if not any(mobile_permissions(request.user).values()):
            raise PermissionDenied('Konto nie ma dostępu do aplikacji.')
        try:
            path = release_file(filename)
            if not 0 < path.stat().st_size <= MAX_APK_SIZE:
                raise ValueError('Invalid APK size')
            response = FileResponse(path.open('rb'), as_attachment=True, filename=filename,
                                    content_type='application/vnd.android.package-archive')
            response['X-Content-Type-Options'] = 'nosniff'
            return response
        except (OSError, ValueError):
            return Response({'detail': 'Nie znaleziono pliku wydania.'}, status=404)
