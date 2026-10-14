from notifications.services import deliver_pending
from notifications.models import PushDelivery
import json
import tempfile
from datetime import date
from http.cookiejar import LWPCookieJar
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from django.contrib.auth.models import Group, User
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .client import Librus, SessionRejected
from .grades import archive_grades, grade_number, summarize_grades, sync_grades
from .models import Dziecko, HistoriaOceny, Ocena
from .session import CookieStore
from .management.commands.librus_sprawdz import Command


def response(body='<a href="/wyloguj">Wyloguj</a>', status=200, url='https://synergia.librus.pl/wiadomosci/5'):
    result = requests.Response()
    result.status_code = status
    result.url = url
    result._content = body.encode('utf-8')
    result.encoding = 'utf-8'
    return result


class SessionTests(SimpleTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        override = override_settings(LIBRUS_SESSION_DIR=self.directory.name)
        override.enable()
        self.addCleanup(override.disable)

    def cache_session(self, login='test-user'):
        store = CookieStore(login)
        jar = LWPCookieJar()
        jar.set_cookie(requests.cookies.create_cookie('oauth_token', 'private-test-token', domain='synergia.librus.pl'))
        store.save(jar)
        store.close()
        return store.path

    def test_cookies_survive_restart_and_no_password_is_sent(self):
        cookie_path = self.cache_session()
        with patch('librus.client.requests.Session.get', return_value=response()), patch('librus.client.requests.Session.post') as post:
            first = Librus('test-user', 'secret', 'child')
            first.close()
            second = Librus('test-user', 'secret', 'child')
            self.assertEqual(len(second.session.cookies), 1)
            self.assertFalse(second.login_attempted)
            second.close()
            post.assert_not_called()
        self.assertEqual(cookie_path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(Path(self.directory.name).stat().st_mode & 0o777, 0o700)

    def test_expired_session_logs_in_once_and_retries_original_read(self):
        self.cache_session()
        def post(*args, **kwargs):
            return response(json.dumps({'goTo': '/OAuth/finish'}))
        def get(session, url, **kwargs):
            if url.endswith('portalRodzina'):
                return response(url='https://api.librus.pl/OAuth/Authorization')
            if url.endswith('/OAuth/finish'):
                session.cookies.set_cookie(requests.cookies.create_cookie('oauth_token', 'new-token', domain='synergia.librus.pl'))
                return response()
            if any(cookie.value == 'private-test-token' for cookie in session.cookies):
                return response('<input type="password">')
            return response()
        with patch('librus.client.requests.Session.get', autospec=True, side_effect=get), patch('librus.client.requests.Session.post', side_effect=post) as login:
            client = Librus('test-user', 'secret', 'child')
            self.assertTrue(client.login_attempted)
            login.assert_called_once()
            with patch.object(client.session, 'get', return_value=response(status=401)):
                with self.assertRaises(SessionRejected):
                    client._get('https://synergia.librus.pl/ogloszenia')
            login.assert_called_once()
            client.close()

    def test_network_and_server_failures_do_not_log_in_or_discard_cache(self):
        cookie_path = self.cache_session()
        original = cookie_path.read_bytes()
        cases = [requests.Timeout(), requests.ConnectionError(), response(status=429), response(status=503)]
        for case in cases:
            with self.subTest(case=type(case).__name__), patch('librus.client.requests.Session.get') as get, patch('librus.client.requests.Session.post') as post:
                if isinstance(case, Exception):
                    get.side_effect = case
                else:
                    get.return_value = case
                with self.assertRaises(requests.RequestException):
                    Librus('test-user', 'secret', 'child')
                post.assert_not_called()
                self.assertEqual(cookie_path.read_bytes(), original)

    def test_failed_login_is_not_retried_and_releases_lock(self):
        with patch('librus.client.requests.Session.get', return_value=response(url='https://api.librus.pl/OAuth/Authorization')), patch('librus.client.requests.Session.post', return_value=response('{}')) as post:
            with self.assertRaises(SessionRejected):
                Librus('test-user', 'secret', 'child')
            post.assert_called_once()
        store = CookieStore('test-user')
        store.close()

    def test_parallel_account_use_is_blocked_but_other_child_has_separate_cache(self):
        first = CookieStore('first')
        self.addCleanup(first.close)
        with self.assertRaises(RuntimeError):
            CookieStore('first')
        second = CookieStore('second')
        self.assertNotEqual(first.path, second.path)
        second.close()

    def test_content_of_json_is_not_mistaken_for_an_expired_session(self):
        payload = json.dumps({'Text': 'Brak dostępu <input type="password">'}, ensure_ascii=False)
        self.assertFalse(Librus._session_rejected(response(payload)))


class GradeParsingTests(SimpleTestCase):
    def setUp(self):
        self.client = Librus.__new__(Librus)
        self.grade = {
            'Id': 123, 'Grade': '4+', 'Date': '2026-10-10',
            'Subject': {'Id': 1}, 'Category': {'Id': 2}, 'Comments': [{'Id': 3}], 'IsConstituent': True,
        }
        self.payloads = [
            {'Grades': [self.grade]}, {'Subjects': [{'Id': 1, 'Name': 'Matematyka'}]},
            {'Categories': [{'Id': 2, 'Name': 'Sprawdzian', 'Weight': '3'}]},
            {'Comments': [{'Id': 3, 'Text': 'Ułamki'}]},
        ]

    def test_bulk_resources_resolve_grade_subject_date_and_description(self):
        with patch.object(self.client, '_get', side_effect=[response(json.dumps(p)) for p in self.payloads]) as get:
            result = self.client.sprawdz_oceny()
        self.assertEqual(result, [{'librus_id': '123', 'wartosc': '4+', 'data': date(2026, 10, 10), 'przedmiot': 'Matematyka', 'opis': 'Sprawdzian\nUłamki', 'waga': 3, 'liczona_do_sredniej': True}])
        self.assertEqual(get.call_count, 4)

    def test_missing_related_data_is_not_silently_imported(self):
        self.payloads[-1] = {'Comments': []}
        with patch.object(self.client, '_get', side_effect=[response(json.dumps(p)) for p in self.payloads]), self.assertRaises(ValueError):
            self.client.sprawdz_oceny()

    def test_unset_category_weight_is_preserved(self):
        for missing in (False, True):
            with self.subTest(missing=missing):
                if missing:
                    self.payloads[2]['Categories'][0].pop('Weight', None)
                else:
                    self.payloads[2]['Categories'][0]['Weight'] = None
                with patch.object(self.client, '_get', side_effect=[response(json.dumps(p)) for p in self.payloads]):
                    result = self.client.sprawdz_oceny()
                self.assertIsNone(result[0]['waga'])

    def test_login_page_invalid_schema_and_pagination_are_not_empty_grade_lists(self):
        for payload in ('<html>Logowanie</html>', '{}', '{"Grades": null}', '{"Grades": [], "Pagination": {}}'):
            with self.subTest(payload=payload), patch.object(self.client, '_get', return_value=response(payload)), self.assertRaises(ValueError):
                self.client.sprawdz_oceny()


class GradeSyncTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='child')
        self.child = Dziecko.objects.create(user=self.user, librus_login='test')
        other = User.objects.create_user(username='other')
        self.other_child = Dziecko.objects.create(user=other, librus_login='other')
        self.grade = {'librus_id': '123', 'wartosc': '4+', 'data': date(2026, 10, 10), 'przedmiot': 'Matematyka', 'opis': 'Ułamki', 'waga': 3, 'liczona_do_sredniej': True}

    def test_repeated_sync_is_idempotent_and_tracks_changes_and_removals(self):
        self.assertEqual(sync_grades(self.child, [self.grade])['new'], 1)
        sync_grades(self.child, [self.grade])
        self.assertEqual(Ocena.objects.count(), 1)
        self.assertEqual(HistoriaOceny.objects.count(), 0)
        changed = {**self.grade, 'wartosc': '5', 'opis': 'Poprawa'}
        self.assertEqual(sync_grades(self.child, [changed])['changed'], 1)
        self.assertEqual(HistoriaOceny.objects.get().wartosc, '4+')
        self.assertEqual(sync_grades(self.child, [])['archived'], 1)
        self.assertIsNotNone(Ocena.objects.get().archiwizowano_at)
        self.assertEqual(sync_grades(self.child, [])['archived'], 0)
        self.assertEqual(sync_grades(self.child, [changed])['restored'], 1)
        self.assertIsNone(Ocena.objects.get().archiwizowano_at)
        self.assertEqual(HistoriaOceny.objects.count(), 3)
        self.child.refresh_from_db()
        self.assertIsNotNone(self.child.oceny_pobrano_at)

    def test_invalid_batch_does_not_change_or_archive_existing_data(self):
        sync_grades(self.child, [self.grade])
        invalid = {**self.grade, 'librus_id': '456', 'data': None}
        with self.assertRaises(ValueError):
            sync_grades(self.child, [{**self.grade, 'wartosc': '2'}, invalid])
        with self.assertRaises(ValueError):
            sync_grades(self.child, [self.grade, self.grade])
        stored = Ocena.objects.get()
        self.assertEqual(stored.wartosc, '4+')
        self.assertIsNone(stored.archiwizowano_at)
        self.assertFalse(HistoriaOceny.objects.exists())

    def test_same_librus_id_for_other_child_remains_independent(self):
        sync_grades(self.child, [self.grade])
        sync_grades(self.other_child, [{**self.grade, 'wartosc': '6'}])
        sync_grades(self.child, [])
        self.assertIsNone(self.other_child.oceny.get().archiwizowano_at)
        self.assertEqual(self.other_child.oceny.get().wartosc, '6')

    def test_history_failure_rolls_back_the_entire_sync(self):
        sync_grades(self.child, [self.grade])
        with patch('librus.grades.HistoriaOceny.objects.create', side_effect=RuntimeError), self.assertRaises(RuntimeError):
            sync_grades(self.child, [{**self.grade, 'librus_id': 'new'}, {**self.grade, 'wartosc': '6'}])
        self.assertEqual(self.child.oceny.count(), 1)
        self.assertEqual(self.child.oceny.get().wartosc, '4+')

    def test_grade_page_shows_only_selected_child_and_escapes_description(self):
        sync_grades(self.child, [{**self.grade, 'opis': '<script>alert(1)</script>'}])
        sync_grades(self.other_child, [{**self.grade, 'przedmiot': 'Inny przedmiot'}])
        self.client.force_login(self.user)
        page = self.client.get(reverse('librus:grades', args=[self.child.pk]))
        self.assertContains(page, 'Matematyka')
        self.assertContains(page, '4+')
        self.assertNotContains(page, 'Inny przedmiot')
        self.assertNotContains(page, '<script>alert(1)</script>')

    def test_weighted_means_use_quarters_and_active_counted_numeric_grades_only(self):
        self.assertEqual(grade_number('4+'), 4.25)
        self.assertEqual(grade_number('5-'), 4.75)
        self.assertIsNone(grade_number('np'))
        records = [
            {**self.grade, 'librus_id': 'a', 'wartosc': '4+', 'waga': 3},
            {**self.grade, 'librus_id': 'b', 'wartosc': '5-', 'waga': 1},
            {**self.grade, 'librus_id': 'c', 'wartosc': '2', 'waga': 9, 'liczona_do_sredniej': False},
            {**self.grade, 'librus_id': 'd', 'wartosc': '6', 'waga': 0},
            {**self.grade, 'librus_id': 'e', 'wartosc': '5', 'przedmiot': 'Polski', 'waga': 1},
            {**self.grade, 'librus_id': 'f', 'wartosc': 'np'},
            {**self.grade, 'librus_id': 'g', 'wartosc': '4', 'waga': None},
        ]
        sync_grades(self.child, records)
        summary = summarize_grades(self.child.oceny.filter(archiwizowano_at__isnull=True))
        self.assertEqual(summary['subjects'], [
            {'name': 'Matematyka', 'mean': '4,4', 'count': 2},
            {'name': 'Polski', 'mean': '5,0', 'count': 1},
        ])
        self.assertEqual(summary['projected'], '4,7')
        self.assertEqual(summary['excluded'], 4)
        self.client.force_login(self.user)
        dashboard = self.client.get(reverse('librus:dashboard'))
        self.assertContains(dashboard, 'Szacowana średnia końcowa:')
        self.assertContains(dashboard, '4,7')

    def test_manual_archive_is_inclusive_and_not_restored_by_next_sync(self):
        older = {**self.grade, 'librus_id': 'old', 'data': date(2026, 1, 31)}
        newer = {**self.grade, 'librus_id': 'new', 'data': date(2026, 2, 1), 'wartosc': '5'}
        sync_grades(self.child, [older, newer])
        self.assertEqual(archive_grades(self.child, date(2026, 1, 31)), 1)
        self.assertEqual(archive_grades(self.child, date(2026, 1, 31)), 0)
        self.assertEqual([g.librus_id for g in self.child.oceny.filter(archiwizowano_at__isnull=True)], ['new'])
        self.assertEqual(summarize_grades(self.child.oceny.filter(archiwizowano_at__isnull=True))['projected'], '5,0')
        self.assertEqual(HistoriaOceny.objects.get().powod, 'semestr')
        sync_grades(self.child, [older, newer])
        self.assertIsNotNone(self.child.oceny.get(librus_id='old').archiwizowano_at)
        self.assertEqual(HistoriaOceny.objects.count(), 1)
        with self.assertRaises(ValueError):
            archive_grades(self.child, date(2026, 1, 30))
        late_record = {**self.grade, 'librus_id': 'late', 'data': date(2026, 1, 30)}
        sync_grades(self.child, [older, newer, late_record])
        self.assertIsNotNone(self.child.oceny.get(librus_id='late').archiwizowano_at)
        self.client.force_login(self.user)
        page = self.client.get(reverse('librus:grades', args=[self.child.pk]))
        self.assertContains(page, '2026-01-31')

    def test_manual_archive_command_rejects_invalid_date_and_filters_child(self):
        from django.core.management import call_command
        from django.core.management.base import CommandError
        from io import StringIO

        sync_grades(self.child, [self.grade])
        sync_grades(self.other_child, [self.grade])
        with self.assertRaises(CommandError):
            call_command('librus_archiwizuj_oceny', cutoff='bad', stdout=StringIO())
        call_command('librus_archiwizuj_oceny', cutoff='2026-10-10', dziecko='child', stdout=StringIO())
        self.assertIsNotNone(self.child.oceny.get().archiwizowano_at)
        self.assertIsNone(self.other_child.oceny.get().archiwizowano_at)


class GradeNotificationTests(TestCase):
    def setUp(self):
        self.child_user = User.objects.create_user(username='student', first_name='Jan')
        self.child = Dziecko.objects.create(user=self.child_user, librus_login='student-librus')
        recipient = User.objects.create_user(username='recipient', email='recipient@example.com')
        Group.objects.create(name='Librus').user_set.add(recipient)
        self.grade = {
            'librus_id': 'old', 'wartosc': '4+', 'przedmiot': 'Matematyka',
            'data': date(2026, 10, 10), 'opis': 'Sprawdzian',
            'waga': 2, 'liczona_do_sredniej': True,
        }
        self.client = Mock()
        self.command = Command()

    def test_first_import_is_silent_then_only_new_grade_sends_once(self):
        self.client.sprawdz_oceny.return_value = [self.grade]
        self.command.aktualizuj_oceny(self.client, self.child)
        old = self.child.oceny.get()
        self.assertEqual(old.powiadomienie_status, Ocena.POWIADOMIENIE_POMINIETE)
        self.assertEqual(len(mail.outbox), 0)

        current = {**self.grade, 'librus_id': 'new', 'wartosc': '5', 'opis': 'Kartkówka'}
        self.client.sprawdz_oceny.return_value = [self.grade, current]
        result = self.command.aktualizuj_oceny(self.client, self.child)
        self.assertEqual(result['notifications_queued'], 1)
        self.assertEqual(len(mail.outbox), 0)
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('Matematyka', mail.outbox[0].subject)
        self.assertIn('5', mail.outbox[0].subject)
        self.assertIn('Kartkówka', mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ['recipient@example.com'])
        notified = self.child.oceny.get(librus_id='new')
        self.assertEqual(notified.powiadomienie_status, Ocena.POWIADOMIENIE_WYSLANE)
        self.assertIsNotNone(notified.powiadomiono_at)

        self.command.aktualizuj_oceny(self.client, self.child)
        self.client.sprawdz_oceny.return_value = [self.grade, {**current, 'wartosc': '5+'}]
        self.command.aktualizuj_oceny(self.client, self.child)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.child.oceny.get(librus_id='old').powiadomienie_status, Ocena.POWIADOMIENIE_POMINIETE)

    def test_failed_enqueue_remains_pending_and_retries_next_run(self):
        sync_grades(self.child, [self.grade])
        self.client.sprawdz_oceny.return_value = [self.grade]
        with patch('notifications.services.queue_group_notifications', side_effect=RuntimeError('Database unavailable')):
            with self.assertRaisesRegex(RuntimeError, 'ponowię'):
                self.command.aktualizuj_oceny(self.client, self.child)
        self.assertEqual(self.child.oceny.get().powiadomienie_status, Ocena.POWIADOMIENIE_OCZEKUJE)
        self.client.sprawdz_oceny.return_value = [self.grade]
        self.command.aktualizuj_oceny(self.client, self.child)
        self.assertEqual(deliver_pending(), 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(self.child.oceny.get().powiadomienie_status, Ocena.POWIADOMIENIE_WYSLANE)

    def test_archived_new_grade_does_not_send_email(self):
        sync_grades(self.child, [])
        archive_grades(self.child, date(2026, 1, 31))
        historical = {**self.grade, 'data': date(2026, 1, 30)}
        self.client.sprawdz_oceny.return_value = [historical]
        self.command.aktualizuj_oceny(self.client, self.child)
        grade = self.child.oceny.get()
        self.assertIsNotNone(grade.archiwizowano_at)
        self.assertEqual(grade.powiadomienie_status, Ocena.POWIADOMIENIE_POMINIETE)
        self.assertEqual(len(mail.outbox), 0)
