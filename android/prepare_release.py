#!/usr/bin/env python3
"""Prepare an APK and its metadata locally; upload android_releases/ manually."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parent.parent
PACKAGE_NAME = 'com.macbilicki.domowydashboard'


def sdk_directory():
    value = os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT')
    if not value:
        props = ROOT / 'android' / 'local.properties'
        if props.exists():
            for line in props.read_text().splitlines():
                if line.startswith('sdk.dir='):
                    value = line.split('=', 1)[1].replace('\\:', ':').replace('\\\\', '\\')
    if not value:
        raise ValueError('Nie znaleziono SDK: ustaw sdk.dir w android/local.properties.')
    return Path(value)


def prepare(apk):
    apk = Path(apk).resolve()
    if not apk.is_file() or not 0 < apk.stat().st_size <= 100 * 1024 * 1024:
        raise ValueError('APK nie istnieje lub przekracza limit 100 MiB.')
    candidates = list((sdk_directory() / 'build-tools').glob('*/aapt'))
    if not candidates:
        raise ValueError('Zainstaluj Android SDK Build Tools.')
    aapt = max(candidates, key=lambda path: tuple(int(x) for x in path.parent.name.split('.') if x.isdigit()))
    result = subprocess.run([str(aapt), 'dump', 'badging', str(apk)], check=True,
                            capture_output=True, text=True, timeout=30)
    match = re.search(r"^package: name='([^']+)' versionCode='([0-9]+)' versionName='([^']+)'", result.stdout, re.M)
    if not match or match[1] != PACKAGE_NAME:
        raise ValueError('APK nie jest wydaniem Domowego dashboardu.')
    code = int(match[2])
    if not 0 < code <= 2100000000 or not 1 <= len(match[3]) <= 100:
        raise ValueError('Nieprawidłowy numer wersji.')
    signer = aapt.with_name('apksigner')
    subprocess.run([str(signer), 'verify', str(apk)], check=True, capture_output=True, timeout=30)
    digest = hashlib.sha256()
    with apk.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    sha = digest.hexdigest()
    metadata = {'version_code': code, 'version_name': match[3], 'package_name': PACKAGE_NAME,
                'apk': f'dashboard-{code}-{sha[:12]}.apk', 'sha256': sha, 'size': apk.stat().st_size}
    output = ROOT / 'android_releases'
    output.mkdir(exist_ok=True)
    target = output / metadata['apk']
    # Publish metadata last, only after the complete APK is in place.
    with tempfile.NamedTemporaryFile(dir=output, delete=False) as stream:
        temporary_apk = Path(stream.name)
    try:
        shutil.copyfile(apk, temporary_apk)
        temporary_apk.replace(target)
    finally:
        temporary_apk.unlink(missing_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output, delete=False) as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        temporary_json = Path(stream.name)
    try:
        temporary_json.replace(output / 'release.json')
    finally:
        temporary_json.unlink(missing_ok=True)
    return metadata


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('apk', help='Ścieżka do podpisanego APK, debug lub release.')
    args = parser.parse_args()
    try:
        release = prepare(args.apk)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(1, f'Nie przygotowano wydania: {error if isinstance(error, ValueError) else type(error).__name__}\n')
    print(f"Przygotowano wersję {release['version_name']} ({release['version_code']}) w {ROOT / 'android_releases'}.")
    print('Wyślij APK na serwer jako pierwszy, a release.json jako ostatni (podmień atomowo).')
