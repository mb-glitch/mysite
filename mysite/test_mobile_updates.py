import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from .mobile_updates import MAX_APK_SIZE, PACKAGE_NAME


class MobileUpdateTests(TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory(dir=settings.BASE_DIR / 'logs')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / 'releases'
        self.root.mkdir()
        self.override = override_settings(ANDROID_RELEASE_DIR=self.root)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.user = User.objects.create_user('app-reader', password='test-password')
        self.group = Group.objects.create(name='Librus')
        self.user.groups.add(self.group)
        self.token = Token.objects.create(user=self.user)
        self.client = APIClient()
        self.client.credentials(HTTP_AUTHORIZATION=f'Token {self.token.key}')
        self.url = '/api/v1/app/update/'
        self.bytes = b'fake APK payload for local tests'
        sha = hashlib.sha256(self.bytes).hexdigest()
        self.metadata = {'version_code': 3, 'version_name': '1.2', 'package_name': PACKAGE_NAME,
                         'sha256': sha, 'size': len(self.bytes), 'apk': f'dashboard-3-{sha[:12]}.apk'}
        self.apk_url = f'/api/v1/app/apk/{self.metadata["apk"]}/'

    def publish(self):
        (self.root / self.metadata['apk']).write_bytes(self.bytes)
        (self.root / 'release.json').write_text(json.dumps(self.metadata))

    def test_no_release_is_not_an_error(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, {'available': False})
        self.assertEqual(response['Cache-Control'], 'no-store')

    def test_metadata_and_download_are_read_only_authenticated_and_no_store(self):
        self.publish()
        response = self.client.get(self.url)
        self.assertEqual(response.data['download_path'], self.apk_url)
        self.assertEqual(response.data['sha256'], self.metadata['sha256'])
        self.assertNotIn(self.token.key, response.content.decode())
        before = (self.root / 'release.json').read_bytes()
        apk = self.client.get(self.apk_url)
        self.assertEqual(apk.status_code, 200)
        self.assertEqual(apk['Content-Type'], 'application/vnd.android.package-archive')
        self.assertEqual(apk['Cache-Control'], 'no-store')
        self.assertEqual(int(apk['Content-Length']), len(self.bytes))
        self.assertEqual(b''.join(apk.streaming_content), self.bytes)
        self.assertEqual((self.root / 'release.json').read_bytes(), before)
        self.assertEqual(self.client.post(self.url, {}).status_code, 405)
        self.assertEqual(self.client.delete(self.apk_url).status_code, 405)
        self.client.credentials()
        self.assertEqual(self.client.get(self.url).status_code, 401)
        self.assertEqual(self.client.get(self.apk_url).status_code, 401)

    def test_users_without_dashboard_access_are_rejected(self):
        self.publish()
        self.user.groups.clear()
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.apk_url).status_code, 403)

    def test_invalid_or_incomplete_publication_returns_safe_error(self):
        self.publish()
        for key, bad in [('version_code', True), ('size', MAX_APK_SIZE + 1),
                         ('sha256', 'bad'), ('package_name', 'other'), ('apk', '../private.apk')]:
            metadata = {**self.metadata, key: bad}
            (self.root / 'release.json').write_text(json.dumps(metadata))
            self.assertEqual(self.client.get(self.url).status_code, 503)
        (self.root / 'release.json').write_text('not JSON')
        self.assertEqual(self.client.get(self.url).status_code, 503)
        (self.root / 'release.json').write_text(json.dumps(self.metadata))
        (self.root / self.metadata['apk']).unlink()
        self.assertEqual(self.client.get(self.url).status_code, 503)

    def test_traversal_and_symlinks_cannot_expose_other_files(self):
        secret = Path(self.directory.name) / 'private.txt'
        secret.write_text('private test content')
        (self.root / self.metadata['apk']).symlink_to(secret)
        self.assertEqual(self.client.get(self.apk_url).status_code, 404)
        self.assertEqual(self.client.get('/api/v1/app/apk/private.txt/').status_code, 404)
        self.assertEqual(self.client.get('/api/v1/app/apk/..%2Fprivate.txt/').status_code, 404)

    def test_old_versioned_apks_remain_downloadable_after_new_publication(self):
        self.publish()
        self.metadata['version_code'] = 4
        self.metadata['apk'] = f'dashboard-4-{self.metadata["sha256"][:12]}.apk'
        self.publish()
        apk = self.client.get(self.apk_url)
        self.assertEqual(apk.status_code, 200)
        self.assertEqual(b''.join(apk.streaming_content), self.bytes)

    def test_technical_account_cannot_download_even_with_group(self):
        self.publish()
        self.user.set_unusable_password()
        self.user.save(update_fields=['password'])
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.assertEqual(self.client.get(self.apk_url).status_code, 403)
