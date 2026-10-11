"""Private, per-account cookie storage shared by successive command runs."""

import fcntl
import hashlib
import os
import tempfile
from http.cookiejar import LWPCookieJar, LoadError
from pathlib import Path

from django.conf import settings


class CookieStore:
    def __init__(self, login):
        directory = Path(settings.LIBRUS_SESSION_DIR)
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if directory.is_symlink():
            raise RuntimeError('Katalog sesji Librusa nie może być dowiązaniem.')
        directory.chmod(0o700)
        identifier = hashlib.sha256(login.encode('utf-8')).hexdigest()
        self.path = directory / f'{identifier}.cookies'
        fd = os.open(directory / f'{identifier}.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        self.lock = os.fdopen(fd, 'a')
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError('Synchronizacja tego konta Librusa już trwa.') from None

    def load(self):
        jar = LWPCookieJar(str(self.path))
        try:
            if self.path.is_symlink():
                raise RuntimeError('Plik sesji Librusa nie może być dowiązaniem.')
            jar.load(ignore_discard=True)
        except (FileNotFoundError, LoadError):
            # A missing/damaged cache is not a failed login attempt.
            jar.clear()
        return jar

    def save(self, jar):
        fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.cookies-')
        os.close(fd)
        try:
            jar.save(temporary, ignore_discard=True)
            os.replace(temporary, self.path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def close(self):
        self.lock.close()
