import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

import requests

import updater


def executable_bytes(suffix=b'new version'):
    data = bytearray(128)
    data[:2] = b'MZ'
    data[60:64] = (64).to_bytes(4, 'little')
    data[64:68] = b'PE\0\0'
    return bytes(data) + suffix


class Response:
    def __init__(self, data=b'', status=200, headers=None, error=None):
        self.data, self.status_code = data, status
        self.headers, self.error = headers or {}, error
        self.closed = False

    def iter_content(self, chunk_size):
        if self.error:
            raise self.error
        for offset in range(0, len(self.data), 32):
            yield self.data[offset:offset + 32]

    def close(self):
        self.closed = True


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _UpdaterFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='lyrio update ñ ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.root / 'Lyrio.exe'
        self.target.write_bytes(executable_bytes(b'old version'))
        self.data = executable_bytes()
        self.digest = hashlib.sha256(self.data).hexdigest()

    def release(self, tag='v1.2.0', name='Lyrio.exe', **extra):
        def asset(filename, size):
            return dict(name=filename, size=size, state='uploaded',
                        browser_download_url=f'https://github.com/EazyHood/Lyrio/releases/download/{tag}/{filename}')
        result = dict(tag_name=tag, draft=False, prerelease=False,
                      assets=[asset(name, len(self.data)), asset('SHA256SUMS.txt', 80)])
        result.update(extra)
        return result

    def client(self, *responses, channel='full', executable=None):
        return updater.AutoUpdater('1.1.0', executable=executable or self.target,
                                   cache_dir=self.root / 'cache', session=Session(*responses),
                                   frozen=True, channel=channel)

    def stage(self, client=None, name='Lyrio.exe'):
        client = client or self.client(Response(json.dumps(self.release(name=name)).encode()),
                                       Response(f'{self.digest}  {name}\n'.encode()), Response(self.data))
        return client, client.check_and_download()

class UpdaterTests(_UpdaterFixture, unittest.TestCase):
    def test_semver_stable_precedence(self):
        self.assertGreater(updater.stable_version('v1.10.0'), updater.stable_version('1.9.9'))
        self.assertEqual(updater.stable_version('1.2.0+windows.1'), (1, 2, 0))
        for value in ('v1.2.0-rc.1', '1.2', '01.2.3', '../1.2.3', '1.2.3+a..b', '', None):
            self.assertIsNone(updater.stable_version(value))

    def test_verified_download_persists_and_reuses_offline(self):
        states = []
        client = self.client(Response(json.dumps(self.release()).encode()),
                             Response(f'{self.digest}  Lyrio.exe\n'.encode()), Response(self.data))
        pending = client.check_and_download(lambda state, data: states.append(state))
        self.assertEqual(Path(pending.staged).read_bytes(), self.data)
        self.assertEqual(client.pending_update(), pending)
        self.assertEqual(client.check_and_download(), pending)
        self.assertEqual(len(client._session.calls), 3)
        self.assertEqual(states[0], 'checking')
        self.assertEqual(states[-1], 'ready')
        self.assertIn('downloading', states)
        self.assertEqual(self.target.read_bytes(), executable_bytes(b'old version'))

    def test_lite_channel_selects_only_lite(self):
        release = self.release(name='Lyrio-Lite.exe')
        client = self.client(Response(json.dumps(release).encode()),
                             Response(f'{self.digest}  Lyrio-Lite.exe\n'.encode()), Response(self.data), channel='lite')
        pending = client.check_and_download()
        self.assertEqual(Path(pending.staged).name, 'Lyrio-Lite.exe')
        self.assertEqual(pending.channel, 'lite')

    def test_embedded_channel_survives_executable_rename(self):
        with mock.patch.object(updater, 'UPDATE_CHANNEL', 'lite'):
            client = updater.AutoUpdater('1.1.0', executable=self.root / 'Mi música.exe', frozen=True)
        self.assertTrue(client.enabled)
        self.assertEqual(client.channel, 'lite')

    def test_source_and_unknown_build_are_not_updated(self):
        client = updater.AutoUpdater('1.1.0', executable=self.target, frozen=False)
        self.assertIsNone(client.check_and_download())
        self.assertFalse(client.install_pending_on_exit())
        with mock.patch.object(updater, 'UPDATE_CHANNEL', None):
            client = updater.AutoUpdater('1.1.0', executable=self.root / 'python.exe', frozen=True)
        self.assertFalse(client.enabled)

    def test_downgrade_prerelease_and_draft_are_ignored(self):
        for release in (self.release(tag='v1.0.0'), self.release(tag='v1.1.0'),
                        self.release(prerelease=True), self.release(draft=True), self.release(tag='v1.3.0-rc.1')):
            client = self.client(Response(json.dumps(release).encode()))
            self.assertIsNone(client.check_and_download())
            self.assertEqual(len(client._session.calls), 1)

    def test_missing_manifest_blocks_unverified_release(self):
        release = self.release()
        release['assets'].pop()
        client = self.client(Response(json.dumps(release).encode()))
        with self.assertRaises(updater.UpdateError):
            client.check_and_download()

    def test_wrong_edition_blocks_update(self):
        client = self.client(Response(json.dumps(self.release(name='Lyrio-Lite.exe')).encode()))
        with self.assertRaises(updater.UpdateError):
            client.check_and_download()

    def test_tampered_download_is_not_staged(self):
        client = self.client(Response(json.dumps(self.release()).encode()),
                             Response(f'{"0" * 64}  Lyrio.exe\n'.encode()), Response(self.data))
        with self.assertRaises(updater.UpdateError):
            client.check_and_download()
        self.assertFalse(client.pending_path.exists())
        self.assertFalse(list(client.directory.rglob('*.part')))

    def test_non_executable_with_matching_checksum_is_rejected(self):
        self.data = b'<html>not an executable</html>'
        self.digest = hashlib.sha256(self.data).hexdigest()
        with self.assertRaises(updater.UpdateError):
            self.stage()

    def test_github_digest_must_agree(self):
        release = self.release()
        release['assets'][0]['digest'] = 'sha256:' + '0' * 64
        client = self.client(Response(json.dumps(release).encode()), Response(f'{self.digest}  Lyrio.exe\n'.encode()))
        with self.assertRaises(updater.UpdateError):
            client.check_and_download()
        self.assertEqual(len(client._session.calls), 2)

    def test_duplicate_checksum_is_rejected(self):
        with self.assertRaises(updater.UpdateError):
            updater._manifest_hash((f'{self.digest}  Lyrio.exe\n' * 2).encode(), 'Lyrio.exe')

    def test_external_or_wrong_version_urls_are_rejected_before_request(self):
        for url in ('https://evil.test/Lyrio.exe', 'http://github.com/EazyHood/Lyrio/releases/download/v1.2.0/Lyrio.exe',
                    'https://github.com/SomeoneElse/Lyrio/releases/download/v1.2.0/Lyrio.exe',
                    'https://github.com/EazyHood/Lyrio/releases/download/v1.3.0/Lyrio.exe'):
            release = self.release()
            release['assets'][0]['browser_download_url'] = url
            client = self.client(Response(json.dumps(release).encode()))
            with self.assertRaises(updater.UpdateError):
                client.check_and_download()
            self.assertEqual(len(client._session.calls), 1)

    def test_redirect_is_validated_before_following(self):
        response = Response(status=302, headers={'Location': 'https://evil.test/payload'})
        client = self.client(response)
        with self.assertRaises(updater.UpdateError):
            client._download(self.release()['assets'][0]['browser_download_url'], 100)
        self.assertEqual(len(client._session.calls), 1)
        self.assertTrue(response.closed)

    def test_known_github_storage_redirect_is_allowed(self):
        client = self.client(Response(status=302, headers={'Location': 'https://release-assets.githubusercontent.com/github-production-release-asset/123?sig=abc'}), Response(b'asset'))
        self.assertEqual(client._download(self.release()['assets'][0]['browser_download_url'], 100), b'asset')

    def test_size_limit_applies_without_content_length(self):
        client = self.client(Response(b'x' * 200))
        with self.assertRaises(updater.UpdateError):
            client._download(self.release()['assets'][0]['browser_download_url'], 100)

    def test_retry_restarts_truncated_file(self):
        client = self.client(Response(self.data[:32]), Response(self.data))
        path = self.root / 'partial'
        with mock.patch.object(client._stop, 'wait', return_value=False):
            client._download(self.release()['assets'][0]['browser_download_url'], 1000,
                             destination=path, expected_size=len(self.data))
        self.assertEqual(path.read_bytes(), self.data)
        self.assertEqual(len(client._session.calls), 2)

    def test_network_failure_has_bounded_retries(self):
        client = self.client(*[requests.ConnectionError('offline') for _ in range(3)])
        with mock.patch.object(client._stop, 'wait', return_value=False):
            with self.assertRaises(updater.UpdateError):
                client._download(updater.RELEASES_URL, 100, api=True)
        self.assertEqual(len(client._session.calls), 3)

    def test_cancellation_interrupts_stream_and_cleans_partial(self):
        client = self.client(Response(json.dumps(self.release()).encode()),
                             Response(f'{self.digest}  Lyrio.exe\n'.encode()), Response(self.data))
        def callback(state, detail):
            if state == 'downloading' and detail['downloaded']:
                client.close()
        with self.assertRaises(updater.UpdateCancelled):
            client.check_and_download(callback)
        self.assertFalse(client.pending_path.exists())
        self.assertFalse(list(client.directory.rglob('*.part')))

    def test_tampered_persisted_file_or_path_is_rejected(self):
        client, pending = self.stage()
        Path(pending.staged).write_bytes(executable_bytes(b'tampered'))
        self.assertIsNone(client.pending_update())
        Path(pending.staged).write_bytes(self.data)
        record = json.loads(client.pending_path.read_text(encoding='utf-8'))
        record['staged'] = str(self.target)
        client.pending_path.write_text(json.dumps(record), encoding='utf-8')
        self.assertIsNone(client.pending_update())

    def test_closed_client_still_installs_verified_pending(self):
        client, pending = self.stage()
        client.close()
        with mock.patch.object(updater.subprocess, 'Popen') as popen:
            self.assertTrue(client.install_pending_on_exit(restart=True))
        args, kwargs = popen.call_args
        self.assertIsInstance(args[0], list)
        self.assertNotIn('shell', kwargs)
        self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW)
        self.assertEqual(kwargs['env']['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        plan = json.loads((client.directory / 'install-plan.json').read_text(encoding='utf-8'))
        self.assertEqual(plan['target'], str(self.target))
        self.assertTrue(plan['restart'])


@unittest.skipUnless(sys.platform == 'win32', 'Windows PowerShell integration')
class InstallerIntegrationTests(_UpdaterFixture, unittest.TestCase):
    def run_helper(self, client, *, tamper=False, wait_pid=2147483647):
        with mock.patch.object(updater.subprocess, 'Popen'):
            client.install_pending_on_exit()
        plan_path = client.directory / 'install-plan.json'
        plan = json.loads(plan_path.read_text(encoding='utf-8'))
        plan['pid'] = wait_pid  # By default no live process; never terminate anything.
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
        if tamper:
            Path(plan['staged']).write_bytes(b'tampered')
        command = [str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
                   '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                   '-File', str(client.directory / 'install-update.ps1'), '-PlanPath', str(plan_path)]
        completed = subprocess.run(command, capture_output=True, timeout=15,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        return completed, plan

    def test_helper_atomically_replaces_unicode_path_and_keeps_backup(self):
        self.target = self.root / "Lyrio canción ' $ [portable].exe"
        old = executable_bytes(b'old version')
        self.target.write_bytes(old)
        client, _ = self.stage()
        completed, plan = self.run_helper(client)
        detail = (client.directory / 'install-result.json').read_text(encoding='utf-8-sig')
        self.assertEqual(completed.returncode, 0, detail + completed.stderr.decode(errors='replace'))
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(Path(str(self.target) + '.lyrio-previous').read_bytes(), old)
        self.assertFalse(client.pending_path.exists())
        result = json.loads((client.directory / 'install-result.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(result['status'], 'installed')

    def test_helper_preserves_current_executable_if_staging_was_tampered(self):
        client, _ = self.stage()
        completed, plan = self.run_helper(client, tamper=True)
        self.assertEqual(completed.returncode, 1)
        self.assertEqual(self.target.read_bytes(), executable_bytes(b'old version'))
        self.assertTrue(client.pending_path.exists())

    def test_helper_waits_for_running_process_without_killing_it(self):
        client, _ = self.stage()
        ready = self.root / 'holder-ready'
        finished = self.root / 'holder-finished'
        code = ('import pathlib,sys,time; '
                'handle=open(sys.argv[1], "rb"); '
                'pathlib.Path(sys.argv[2]).touch(); time.sleep(1.5); '
                'handle.close(); pathlib.Path(sys.argv[3]).touch()')
        holder = subprocess.Popen([sys.executable, '-c', code, str(self.target), str(ready), str(finished)],
                                  creationflags=subprocess.CREATE_NO_WINDOW)
        self.addCleanup(holder.wait, 5)
        deadline = time.monotonic() + 5
        while not ready.exists() and holder.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertTrue(ready.exists())
        completed, _ = self.run_helper(client, wait_pid=holder.pid)
        self.assertEqual(holder.wait(5), 0)
        self.assertTrue(finished.exists(), 'The old process must exit by itself')
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(self.target.read_bytes(), self.data)


if __name__ == '__main__':
    unittest.main()
