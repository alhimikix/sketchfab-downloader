"""Public release discovery, verified downloads and native update staging."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from app_version import VERSION
from https_support import ssl_context
from i18n import t
from platform_support import resource_root, settings_directory
from updater_helper import PROTOCOL, STAGE_PREFIX, child_environment, write_json

REPOSITORY = 'alhimikix/sketchfab-downloader'
RELEASE_PAGE = 'https://github.com/' + REPOSITORY + '/releases/latest'
API_ROOT = 'https://api.github.com/repos/' + REPOSITORY
MAX_PACKAGE = 2 * 1024**3
MAX_EXTRACTED = 8 * 1024**3
DOWNLOAD_HOSTS = {'api.github.com', 'github.com', 'release-assets.githubusercontent.com',
                  'objects.githubusercontent.com', 'github-releases.githubusercontent.com'}


class UpdateError(RuntimeError):
    def __init__(self, key):
        self.key = key
        super().__init__(t(key))


def version_tuple(value):
    match = re.fullmatch(r'v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value)
    if not match:
        raise UpdateError('update.invalid_release')
    return tuple(map(int, match.groups()))


def native_target():
    machine = platform.machine().lower()
    if machine in ('x86_64', 'amd64', 'x64'):
        architecture = 'x64'
    elif machine in ('arm64', 'aarch64'):
        architecture = 'arm64'
    else:
        raise UpdateError('update.unsupported_platform')
    system = {'win32': 'windows', 'darwin': 'macos', 'linux': 'linux'}.get(sys.platform)
    target = f'{system}-{architecture}'
    if target not in ('windows-x64', 'linux-x64', 'macos-x64', 'macos-arm64'):
        raise UpdateError('update.unsupported_platform')
    return target


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != 'https' or parsed.hostname not in DOWNLOAD_HOSTS or parsed.username or parsed.password:
            raise UpdateError('update.network_error')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected:
            for key in ('Authorization', 'Cookie', 'Proxy-authorization'):
                redirected.remove_header(key)
        return redirected


@dataclass(frozen=True)
class Release:
    version: str
    tag: str
    target: str
    asset_id: int
    name: str
    size: int
    digest: str

    @property
    def page_url(self):
        return 'https://github.com/' + REPOSITORY + '/releases/tag/' + self.tag


class ReleaseClient:
    def __init__(self, opener=None):
        self.opener = opener or urllib.request.build_opener(SafeRedirect(),urllib.request.HTTPSHandler(context=ssl_context()))

    def open(self, endpoint, binary=False):
        request = urllib.request.Request(API_ROOT + endpoint, headers={
            'Accept': 'application/octet-stream' if binary else 'application/vnd.github+json',
            'X-GitHub-Api-Version': '2026-03-10', 'User-Agent': 'SketchfabLocal/' + VERSION})
        try:
            return self.opener.open(request, timeout=45)
        except urllib.error.HTTPError as exc:
            key = {401: 'update.auth_error', 403: 'update.rate_limit', 404: 'update.not_found'}.get(exc.code, 'update.network_error')
            raise UpdateError(key) from None
        except (OSError, urllib.error.URLError):
            raise UpdateError('update.network_error') from None

    def latest(self, current_version=VERSION, target=None):
        with self.open('/releases/latest') as response:
            try:
                data = json.loads(response.read(2 * 1024 * 1024))
                tag = data['tag_name']
                remote_version = version_tuple(tag)
                if data.get('draft') or data.get('prerelease'):
                    raise ValueError('Expected a stable published release')
                if remote_version <= version_tuple(current_version):
                    return None
                version = '.'.join(map(str, remote_version))
                if tag != 'v' + version:
                    raise ValueError('Expected a version tag')
                target = target or native_target()
                extension = '.exe' if target.startswith('windows-') else '.zip' if target.startswith('macos-') else '.tar.gz'
                name = f'SketchfabLocal-{version}-{target}{extension}'
                matches = [a for a in data['assets'] if a.get('name') == name and a.get('state') == 'uploaded']
                if len(matches) != 1:
                    raise UpdateError('update.unsupported_platform')
                asset = matches[0]
                size, asset_id = int(asset['size']), int(asset['id'])
                digest = asset.get('digest', '')
                if not 0 < size <= MAX_PACKAGE or asset_id <= 0 or not re.fullmatch(r'sha256:[0-9a-f]{64}', digest):
                    raise ValueError('Missing or invalid digest/size')
                return Release(version, tag, target, asset_id, name, size, digest.split(':')[1])
            except (KeyError, ValueError, TypeError, AttributeError):
                raise UpdateError('update.invalid_release') from None

    def download(self, release, destination, progress=lambda value: None):
        destination = Path(destination)
        temporary = destination.with_name(destination.name + '.part')
        digest, total, last = hashlib.sha256(), 0, 0.0
        try:
            with self.open('/releases/assets/' + str(release.asset_id), binary=True) as response, temporary.open('xb') as stream:
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > release.size:
                        raise UpdateError('update.checksum_error')
                    stream.write(chunk)
                    digest.update(chunk)
                    now = time.monotonic()
                    if now - last >= .15 or total == release.size:
                        progress(t('update.downloading', version=release.version, percent=total / release.size * 100,
                                   megabytes=total / 1024**2))
                        last = now
            if total != release.size or digest.hexdigest() != release.digest:
                raise UpdateError('update.checksum_error')
            if destination.exists():
                raise FileExistsError('Update download already exists')
            temporary.replace(destination)
            return destination
        except (OSError, urllib.error.URLError):
            raise UpdateError('update.network_error') from None
        finally:
            temporary.unlink(missing_ok=True)


def installed_application():
    if not getattr(sys, 'frozen', False):
        raise UpdateError('update.packaged_only')
    executable = Path(sys.executable).absolute()
    if sys.platform == 'win32':
        return executable, 'windows'
    if sys.platform == 'darwin':
        if executable.parent.name != 'MacOS' or executable.parents[2].suffix != '.app':
            raise UpdateError('update.packaged_only')
        return executable.parents[2], 'macos'
    return executable.parent, 'linux'


def checked_zip_members(archive):
    total = 0
    for member in archive.infolist():
        total += member.file_size
        name = PurePosixPath(member.filename)
        if total > MAX_EXTRACTED or len(archive.infolist()) > 100000:
            raise UpdateError('update.unsafe_archive')
        if name.is_absolute() or '..' in name.parts or '\\' in member.filename or not name.parts:
            raise UpdateError('update.unsafe_archive')
        if name.parts[0] not in ('SketchfabLocal.app', '__MACOSX'):
            raise UpdateError('update.unsafe_archive')
        if (member.external_attr >> 16) & 0o170000 == 0o120000:
            if member.file_size > 4096:
                raise UpdateError('update.unsafe_archive')
            link = archive.read(member).decode('utf-8')
            combined = posixpath.normpath(str(name.parent / link))
            if link.startswith('/') or '\\' in link or combined == '..' or combined.startswith('../'):
                raise UpdateError('update.unsafe_archive')


def extract_package(package, folder, kind):
    folder.mkdir()
    try:
        if kind == 'macos':
            with zipfile.ZipFile(package) as archive:
                checked_zip_members(archive)
            subprocess.run(['/usr/bin/ditto', '-x', '-k', str(package), str(folder)],
                           check=True, env=child_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return folder / 'SketchfabLocal.app'
        if kind == 'linux':
            with tarfile.open(package) as archive:
                members = archive.getmembers()
                if len(members) > 100000 or sum(m.size for m in members) > MAX_EXTRACTED:
                    raise UpdateError('update.unsafe_archive')
                if any(not PurePosixPath(m.name).parts or PurePosixPath(m.name).parts[0] != 'SketchfabLocal' for m in members):
                    raise UpdateError('update.unsafe_archive')
                archive.extractall(folder, filter='data')
            return folder / 'SketchfabLocal'
    except (tarfile.TarError, zipfile.BadZipFile, ValueError, OSError, subprocess.CalledProcessError):
        raise UpdateError('update.unsafe_archive') from None
    raise UpdateError('update.unsupported_platform')


def verify_payload(prepared, kind, version, report):
    from updater_helper import application_executable
    try:
        completed = subprocess.run([str(application_executable(prepared, kind)), '--self-test', str(report)],
                                   cwd=prepared.parent, env=child_environment(), timeout=180,
                                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        data = json.loads(report.read_text(encoding='utf-8'))
        if completed.returncode or not data.get('success') or not data.get('frozen') or data.get('version') != version:
            raise ValueError('Payload smoke test failed')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise UpdateError('update.payload_failed') from None


def prepare_update(release, client, progress=lambda value: None):
    target, kind = installed_application()
    if release.target != native_target() or version_tuple(release.version) <= version_tuple(VERSION):
        raise UpdateError('update.invalid_release')
    try:
        stage = Path(tempfile.mkdtemp(prefix=STAGE_PREFIX, dir=target.parent))
    except OSError:
        raise UpdateError('update.no_write') from None
    try:
        if sys.platform != 'win32':
            stage.chmod(0o700)
        package = client.download(release, stage / release.name, progress)
        progress(t('update.unpacking'))
        prepared = package if kind == 'windows' else extract_package(package, stage / 'unpacked', kind)
        progress(t('update.verifying'))
        verify_payload(prepared, kind, release.version, stage / 'preflight.json')
        helper_name = 'SketchfabUpdateHelper' + ('.exe' if sys.platform == 'win32' else '')
        shutil.copy2(resource_root() / 'update-helper' / helper_name, stage / helper_name)
        manifest = stage / 'manifest.json'
        write_json(manifest, {'protocol': PROTOCOL, 'target': str(target), 'prepared': str(prepared),
                             'kind': kind, 'version': release.version, 'parent_pid': os.getpid(),
                             'result': str(settings_directory() / 'update-result.json')})
        return manifest
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise


def start_installer(manifest):
    helper = Path(manifest).parent / ('SketchfabUpdateHelper' + ('.exe' if sys.platform == 'win32' else ''))
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0) | getattr(subprocess, 'DETACHED_PROCESS', 0)
    subprocess.Popen([str(helper), '--manifest', str(manifest)], cwd=helper.parent, env=child_environment(),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=flags, start_new_session=sys.platform != 'win32')


def cleanup_completed_updates():
    """Remove only marked, completed stages belonging to this installed app."""
    try:
        target, _ = installed_application()
        for stage in target.parent.glob(STAGE_PREFIX + '*'):
            if stage.is_symlink() or not stage.is_dir() or time.time() - stage.stat().st_mtime < 120:
                continue
            marker = stage / 'completed.json'
            if not marker.is_file():
                continue
            completed = json.loads(marker.read_text(encoding='utf-8'))
            backup = Path(completed['backup'])
            if Path(completed['target']) != target or backup.parent != target.parent:
                continue
            if not re.fullmatch(re.escape(target.name) + r'\.previous-[0-9a-f]{32}', backup.name):
                continue
            if backup.is_symlink():
                continue
            if backup.is_dir():
                shutil.rmtree(backup)
            else:
                backup.unlink(missing_ok=True)
            shutil.rmtree(stage)
    except (OSError, ValueError, KeyError, UpdateError):
        pass
