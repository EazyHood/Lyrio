"""Verified, opt-out portable Windows updates from this project's GitHub releases.

Call ``check_and_download`` on a worker, then ``install_pending_on_exit`` only
when the user actually quits. Closing the window to the tray is not a quit.
No source checkout or Python interpreter is ever replaced. Release assets are
``Lyrio.exe``, ``Lyrio-Lite.exe`` and ``SHA256SUMS.txt``. Builds embed their channel
in _build_info.py so renaming an executable does not switch editions.
"""
from dataclasses import dataclass, asdict
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import unquote, urljoin, urlsplit
import uuid

import requests

try:
    from _build_info import UPDATE_CHANNEL
except ImportError:
    UPDATE_CHANNEL = None

log = logging.getLogger("lyrio.updater")
REPOSITORY = "EazyHood/Lyrio"
RELEASES_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASE_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
ASSETS = {"full": "Lyrio.exe", "lite": "Lyrio-Lite.exe"}
MAX_ASSET_BYTES = 1536 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_DOWNLOAD_SECONDS = 20 * 60
_VERSION = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$")


class UpdateError(Exception):
    """An update could not be verified or prepared; the running app is safe."""


class UpdateCancelled(UpdateError):
    pass


class _Retryable(UpdateError):
    pass


def stable_version(value):
    """Return numeric SemVer precedence for stable tags; reject prereleases."""
    match = _VERSION.fullmatch(value) if isinstance(value, str) else None
    return tuple(map(int, match.group(1, 2, 3))) if match else None


def _sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_executable(path):
    """Catch error pages and truncated downloads, in addition to checksum checks."""
    try:
        with open(path, "rb") as stream:
            header = stream.read(64)
            if len(header) < 64 or header[:2] != b"MZ":
                return False
            offset = int.from_bytes(header[60:64], "little")
            if offset < 64 or offset > 1024 * 1024:
                return False
            stream.seek(offset)
            return stream.read(4) == b"PE\0\0"
    except OSError:
        return False


def _write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _trusted_url(url, *, api=False):
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or parsed.port not in (None, 443) or parsed.fragment):
            return False
    except ValueError:
        return False
    if api:
        return url == RELEASES_URL
    if parsed.hostname in ("release-assets.githubusercontent.com", "objects.githubusercontent.com"):
        return True
    return (parsed.hostname == "github.com" and
            unquote(parsed.path).startswith(f"/{REPOSITORY}/releases/download/"))


def _asset_url(asset, tag, name):
    url = asset.get("browser_download_url", "")
    if not _trusted_url(url):
        raise UpdateError("Untrusted release asset URL")
    parsed = urlsplit(url)
    if (parsed.hostname != "github.com" or parsed.query
            or unquote(parsed.path) != f"/{REPOSITORY}/releases/download/{tag}/{name}"):
        raise UpdateError("Release asset URL does not belong to the expected repository and version")
    return url


def _manifest_hash(data, name):
    try:
        lines = data.decode("utf-8-sig").splitlines()
    except UnicodeDecodeError as exc:
        raise UpdateError("Invalid checksum manifest encoding") from exc
    matches = []
    for line in lines:
        match = re.fullmatch(r"([0-9a-fA-F]{64})[ \t]+\*?([^\r\n]+)", line.strip())
        if match and match[2] == name:
            matches.append(match[1].lower())
    if len(matches) != 1:
        raise UpdateError("Release must contain exactly one checksum for this edition")
    return matches[0]


@dataclass(frozen=True)
class PendingUpdate:
    version: str
    channel: str
    target: str
    staged: str
    sha256: str
    size: int


class AutoUpdater:
    def __init__(self, current_version, *, executable=None, cache_dir=None,
                 session=None, channel=None, frozen=None):
        self.current_version = current_version
        self.executable = Path(executable or sys.executable).resolve()
        self.channel = channel or UPDATE_CHANNEL or next(
            (kind for kind, name in ASSETS.items() if name.lower() == self.executable.name.lower()), None)
        self.enabled = (sys.platform == "win32" and
                        (getattr(sys, "frozen", False) if frozen is None else frozen) and
                        self.channel in ASSETS and self.executable.suffix.lower() == ".exe" and
                        stable_version(current_version) is not None)
        base = Path(cache_dir) if cache_dir else Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "Lyrio" / "updates"
        identity = hashlib.sha256(str(self.executable).casefold().encode("utf-8")).hexdigest()[:16]
        self.directory = base / identity
        self.pending_path = self.directory / "pending.json"
        self._session = session or requests.Session()
        self._stop = threading.Event()
        self._busy = threading.Lock()

    def close(self):
        """Permanently stop background work without deleting an already staged update."""
        self._stop.set()

    cancel = close

    def _check_cancelled(self):
        if self._stop.is_set():
            raise UpdateCancelled("Update cancelled")

    def _response(self, url, *, api=False):
        for _ in range(5):
            self._check_cancelled()
            if not _trusted_url(url, api=api):
                raise UpdateError("Untrusted update redirect")
            response = self._session.get(url, stream=True, timeout=(8, 20),
                                         allow_redirects=False,
                                         headers={"User-Agent": "Lyrio-Updater", "Accept": "application/vnd.github+json" if api else "application/octet-stream"})
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location", "")
                response.close()
                if api or not location:
                    raise UpdateError("Unexpected update redirect")
                url = urljoin(url, location)
                continue
            if response.status_code >= 500 or response.status_code in (408, 429):
                response.close()
                raise _Retryable(f"Update service temporarily unavailable ({response.status_code})")
            if response.status_code != 200:
                response.close()
                raise UpdateError(f"Update service returned HTTP {response.status_code}")
            return response
        raise UpdateError("Too many update redirects")

    def _download(self, url, limit, *, destination=None, expected_size=None, api=False, progress=None):
        """Bound memory, disk, transfer time and retries; never trust Content-Length."""
        for attempt in range(3):
            response = None
            stream = None
            try:
                self._check_cancelled()
                response = self._response(url, api=api)
                size_header = response.headers.get("Content-Length")
                if size_header and (not size_header.isdigit() or int(size_header) > limit):
                    raise UpdateError("Release download is too large")
                stream = open(destination, "wb") if destination else None
                chunks, size = [], 0
                started = time.monotonic()
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    self._check_cancelled()
                    if time.monotonic() - started > MAX_DOWNLOAD_SECONDS:
                        raise UpdateError("Update download exceeded the time limit")
                    size += len(chunk)
                    if size > limit or (expected_size is not None and size > expected_size):
                        raise UpdateError("Release download exceeds its declared size")
                    if stream:
                        stream.write(chunk)
                    else:
                        chunks.append(chunk)
                    if progress:
                        progress(size)
                if expected_size is not None and size != expected_size:
                    raise _Retryable("Incomplete update download")
                if stream:
                    stream.flush()
                    os.fsync(stream.fileno())
                return b"".join(chunks) if destination is None else size
            except (requests.RequestException, _Retryable) as exc:
                if attempt == 2:
                    raise UpdateError("Update download failed after three attempts") from exc
                if self._stop.wait(attempt + 1):
                    raise UpdateCancelled("Update cancelled") from exc
            finally:
                if stream:
                    stream.close()
                if response is not None:
                    response.close()
        raise UpdateError("Update download failed")

    def pending_update(self):
        """Validate persisted state, including its target, edition and file checksum."""
        if not self.enabled or not self.pending_path.is_file():
            return None
        try:
            if self.pending_path.stat().st_size > MAX_MANIFEST_BYTES:
                return None
            data = json.loads(self.pending_path.read_text(encoding="utf-8"))
            pending = PendingUpdate(**data)
            version = stable_version(pending.version)
            if (version is None or version <= stable_version(self.current_version)
                    or pending.channel != self.channel or Path(pending.target) != self.executable
                    or not re.fullmatch("[0-9a-f]{64}", pending.sha256)
                    or type(pending.size) is not int or not 0 < pending.size <= MAX_ASSET_BYTES):
                return None
            staged = Path(pending.staged).resolve()
            if (staged != (self.directory / pending.version / ASSETS[self.channel]).resolve()
                    or staged.stat().st_size != pending.size or not _is_executable(staged)
                    or _sha256(staged) != pending.sha256):
                return None
            return pending
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def check_and_download(self, on_status=None):
        """Return a verified update or None. Callback runs on this worker's thread."""
        def status(state, **detail):
            if on_status:
                on_status(state, detail)

        if not self.enabled:
            status("unsupported")
            return None
        if not self._busy.acquire(blocking=False):
            raise UpdateError("An update check is already running")
        part = None
        try:
            self._check_cancelled()
            pending = self.pending_update()
            # Offline starts can immediately reuse an update that was fully verified.
            if pending:
                status("ready", version=pending.version)
                return pending
            status("checking")
            try:
                release = json.loads(self._download(RELEASES_URL, MAX_METADATA_BYTES, api=True))
            except (ValueError, TypeError) as exc:
                raise UpdateError("Invalid release metadata") from exc
            if not isinstance(release, dict):
                raise UpdateError("Invalid release metadata")
            tag = release.get("tag_name", "")
            version = stable_version(tag)
            if (release.get("draft") or release.get("prerelease") or version is None
                    or version <= stable_version(self.current_version)):
                status("current")
                return None
            version_text = tag.removeprefix("v")
            name = ASSETS[self.channel]
            assets = release.get("assets", [])
            if not isinstance(assets, list):
                raise UpdateError("Invalid release assets")
            def asset_named(filename):
                matches = [a for a in assets if isinstance(a, dict) and a.get("name") == filename and a.get("state", "uploaded") == "uploaded"]
                if len(matches) != 1:
                    raise UpdateError(f"Release is missing a unique {filename}")
                return matches[0]
            asset, manifest = asset_named(name), asset_named("SHA256SUMS.txt")
            url = _asset_url(asset, tag, name)
            manifest_url = _asset_url(manifest, tag, "SHA256SUMS.txt")
            size = asset.get("size")
            if type(size) is not int or not 0 < size <= MAX_ASSET_BYTES:
                raise UpdateError("Invalid update asset size")
            expected = _manifest_hash(self._download(manifest_url, MAX_MANIFEST_BYTES), name)
            github_digest = asset.get("digest")
            if github_digest and github_digest.lower() != "sha256:" + expected:
                raise UpdateError("GitHub asset digest and release manifest disagree")
            stage_dir = self.directory / version_text
            stage_dir.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(stage_dir).free < size + 16 * 1024 * 1024:
                raise UpdateError("Not enough disk space to download this update")
            part = stage_dir / (name + ".part")
            status("downloading", version=version_text, downloaded=0, total=size)
            last_progress = [0.0]
            def progress(downloaded):
                now = time.monotonic()
                if downloaded == size or now - last_progress[0] >= 1:
                    last_progress[0] = now
                    status("downloading", version=version_text, downloaded=downloaded, total=size)
            self._download(url, MAX_ASSET_BYTES, destination=part, expected_size=size, progress=progress)
            self._check_cancelled()
            if _sha256(part) != expected or not _is_executable(part):
                raise UpdateError("Downloaded executable failed verification")
            staged = stage_dir / name
            os.replace(part, staged)
            pending = PendingUpdate(version_text, self.channel, str(self.executable), str(staged.resolve()), expected, size)
            _write_json(self.pending_path, asdict(pending))
            status("ready", version=version_text)
            return pending
        except OSError as exc:
            raise UpdateError(f"Cannot stage the update: {exc}") from exc
        finally:
            try:
                if part is not None:
                    part.unlink(missing_ok=True)
            except OSError:
                # A scanner can briefly retain the partial file after closing it.
                # Never leave the updater locked because cleanup failed.
                log.warning("Could not remove partial update %s", part, exc_info=True)
            finally:
                self._busy.release()

    def install_pending_on_exit(self, restart=False):
        """Start a hidden helper. It cannot replace files until this process exits.

        Returns False when no valid update exists. Raises UpdateError if preparation
        fails (for example a read-only portable folder). Caller must still quit.
        """
        pending = self.pending_update()
        if pending is None:
            return False
        try:
            # A writable sibling is required for an atomic same-volume replacement.
            probe = self.executable.with_name(".lyrio-write-test-" + uuid.uuid4().hex)
            try:
                with probe.open("xb") as stream:
                    stream.write(b"")
            finally:
                probe.unlink(missing_ok=True)
            if shutil.disk_usage(self.executable.parent).free < pending.size + 16 * 1024 * 1024:
                raise UpdateError("Not enough disk space to install this update")
            helper = self.directory / "install-update.ps1"
            helper.write_text(_INSTALL_SCRIPT, encoding="utf-8-sig")
            plan = self.directory / "install-plan.json"
            data = asdict(pending)
            data.update(pid=os.getpid(), restart=bool(restart), pending=str(self.pending_path.resolve()))
            _write_json(plan, data)
            powershell = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
            environment = os.environ.copy()
            environment["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
            # Do not retain references to a soon-to-be-deleted PyInstaller temp dir.
            extraction = getattr(sys, "_MEIPASS", None)
            if extraction:
                environment["PATH"] = os.pathsep.join(p for p in environment.get("PATH", "").split(os.pathsep) if not p.casefold().startswith(str(extraction).casefold()))
            subprocess.Popen([str(powershell), "-NoLogo", "-NoProfile", "-NonInteractive",
                              "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                              "-File", str(helper.resolve()), "-PlanPath", str(plan.resolve())],
                             cwd=str(self.directory), env=environment,
                             creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             close_fds=True)
            return True
        except OSError as exc:
            raise UpdateError(f"Cannot prepare the update installer: {exc}") from exc


# Paths and options are JSON data, never interpolated into PowerShell source.
# File.Replace is atomic on supported Windows volumes, and keeps the previous exe.
# If the volume does not support it, fail safely and preserve the existing app.
_INSTALL_SCRIPT = r'''param([Parameter(Mandatory=$true)][string]$PlanPath)
$ErrorActionPreference = 'Stop'
$candidate = $null
$resultPath = Join-Path (Split-Path -LiteralPath $PlanPath) 'install-result.json'
function Get-UpdateHash([string]$LiteralPath) {
    $stream = [IO.File]::OpenRead($LiteralPath)
    $algorithm = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
try {
    $plan = Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $target = [IO.Path]::GetFullPath([string]$plan.target)
    $staged = [IO.Path]::GetFullPath([string]$plan.staged)
    if ([IO.Path]::GetExtension($target) -ne '.exe' -or $plan.sha256 -notmatch '^[0-9a-f]{64}$') {
        throw 'Invalid update plan'
    }
    $process = Get-Process -Id ([int]$plan.pid) -ErrorAction SilentlyContinue
    if ($process -and -not $process.WaitForExit(120000)) {
        throw 'Lyrio is still running; update remains staged'
    }
    if ((Get-Item -LiteralPath $staged).Length -ne [long]$plan.size -or
        (Get-UpdateHash $staged) -ne $plan.sha256) {
        throw 'Staged update failed verification'
    }
    $candidate = $target + '.lyrio-new-' + [Guid]::NewGuid().ToString('N')
    $backup = $target + '.lyrio-previous'
    Copy-Item -LiteralPath $staged -Destination $candidate
    if ((Get-UpdateHash $candidate) -ne $plan.sha256) {
        throw 'Update copy failed verification'
    }
    $installed = $false
    # The one-file bootloader parent can briefly hold the executable after its
    # Python child exits. Antivirus scanners may do the same. Retry for 30 sec.
    for ($attempt = 0; $attempt -lt 60; $attempt++) {
        try {
            [IO.File]::Replace($candidate, $target, $backup, $true)
            $installed = $true
            break
        } catch {
            if ($attempt -eq 59) { throw }
            Start-Sleep -Milliseconds 500
        }
    }
    if (-not $installed) { throw 'Executable could not be replaced' }
    if ((Get-UpdateHash $target) -ne $plan.sha256) {
        [IO.File]::Replace($backup, $target, $null, $true)
        throw 'Installed update failed verification; previous version restored'
    }
    @{ status='installed'; version=$plan.version; backup=$backup } |
        ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    Remove-Item -LiteralPath $plan.pending -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue
    if ($plan.restart) {
        $env:PYINSTALLER_RESET_ENVIRONMENT = '1'
        Start-Process -FilePath $target -WorkingDirectory ([IO.Path]::GetDirectoryName($target)) -WindowStyle Hidden
    }
    exit 0
} catch {
    @{ status='failed'; error=$_.Exception.Message } |
        ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
    exit 1
} finally {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) {
        Remove-Item -LiteralPath $candidate -Force -ErrorAction SilentlyContinue
    }
}
'''
