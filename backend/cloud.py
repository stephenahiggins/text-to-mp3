"""User-selected iCloud Drive inbox. Immutable IDs share LAN receipt semantics."""
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile

from fastapi import HTTPException
from pydantic import BaseModel
from backend.mobile import portable_recording, MAX_FILE_BYTES


class CloudFiles:
    def __init__(self, core):
        self.core = core
        self.written = {}

    def command(self, *args):
        executable = self.core.DATA / 'tools' / 'cloud-files'
        source = self.core.ROOT / 'scripts/cloud-files.swift'
        if not executable.exists() or executable.stat().st_mtime < source.stat().st_mtime:
            executable.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['xcrun', 'swiftc', '-module-cache-path', str(executable.parent / 'module-cache'), str(source), '-o', str(executable)], check=True, capture_output=True, timeout=120)
        return subprocess.check_output([str(executable), *map(str, args)], stderr=subprocess.PIPE, timeout=30)

    @property
    def coordinated(self):
        return sys.platform == 'darwin' and os.environ.get('ARTICLE_CLOUD_COORDINATION', '1') != '0'

    def read(self, path, limit=27_000_000):
        if path.is_symlink():
            raise ValueError('Sync files cannot be symbolic links.')
        if self.coordinated:
            return self.command('read', path, limit)
        with path.open('rb') as stream:
            content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError('File exceeds sync limit.')
        return content

    def names(self, path):
        if path.is_symlink():
            raise ValueError('Sync folders cannot be symbolic links.')
        return json.loads(self.command('list', path)) if self.coordinated else [p.name for p in path.iterdir()]

    def write_file(self, path, source):
        if path.is_symlink():
            raise ValueError('Sync files cannot be symbolic links.')
        if self.coordinated:
            self.command('write', path, source)
        else:
            temp = path.with_name('.' + path.name + '.tmp')
            temp.write_bytes(source.read_bytes())
            temp.replace(path)

    def write_json(self, path, data):
        content = json.dumps(data, ensure_ascii=False, sort_keys=True).encode()
        if self.written.get(str(path)) == content and path.is_file() and not path.is_symlink():
            return
        try:
            if path.exists() and self.read(path) == content:
                self.written[str(path)] = content
                return
        except (FileNotFoundError, subprocess.CalledProcessError):
            pass
        with tempfile.NamedTemporaryFile(dir=self.core.DATA) as temp:
            temp.write(content); temp.flush()
            self.write_file(path, Path(temp.name))
            self.written[str(path)] = content


class CloudSync:
    def __init__(self, core):
        self.core = core
        self.files = CloudFiles(core)
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.thread = None
        self.error = ''
        self.last_sync = None

    @property
    def settings_path(self):
        return self.core.DATA / 'cloud-sync.json'

    def settings(self):
        return json.loads(self.settings_path.read_text()) if self.settings_path.exists() else {}

    def machine(self):
        with self.core.db() as conn:
            return conn.execute("SELECT value FROM share_settings WHERE key='machine_id'").fetchone()['value']

    def configure(self, path, auto_audio):
        with self.lock:
            root = Path(path).expanduser()
            if not root.is_absolute() or root.is_symlink() or not root.is_dir():
                raise ValueError('Select an existing folder in iCloud Drive.')
            root = root.resolve()
            marker_path = root / 'reading-stacks.json'
            # Download an existing iCloud placeholder before considering a new workspace.
            names = self.files.names(root)
            if 'reading-stacks.json' in names:
                marker = json.loads(self.files.read(marker_path))
                if marker.get('schemaVersion') != 1 or marker.get('machineId') != self.machine():
                    raise ValueError('This folder belongs to another Reading Stacks Mac. Select a new folder.')
            else:
                if names:
                    raise ValueError('Choose a new, empty Reading Stacks folder to keep other iCloud files separate.')
                marker = dict(schemaVersion=1, workspaceId=uuid.uuid4().hex, machineId=self.machine())
                self.files.write_json(marker_path, marker)
            for name in ('requests', 'receipts', 'recordings'):
                directory = root / name
                if directory.is_symlink():
                    raise ValueError('Sync folders cannot be symbolic links.')
                directory.mkdir(exist_ok=True)
            settings = dict(path=str(root), workspaceId=marker['workspaceId'], autoAudio=auto_audio)
            temporary = self.settings_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(settings)); temporary.replace(self.settings_path)
            self.error = ''
            return self.status()

    def status(self):
        settings = self.settings()
        return dict(enabled=bool(settings), folder=settings.get('path', ''), autoAudio=settings.get('autoAudio', True),
                    lastSync=self.last_sync, error=self.error)

    def start(self):
        with self.core.db() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS cloud_requests (workspace TEXT, id TEXT, attempt INTEGER, stack_id TEXT, PRIMARY KEY(workspace,id))')
        self.stopping.clear()
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()

    def close(self):
        self.stopping.set()
        if self.thread:
            self.thread.join()
            self.thread = None

    def work(self):
        while not self.stopping.is_set():
            try:
                self.tick()
            except Exception as exc:
                self.error = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                logging.warning('iCloud sync: %s', self.error)
            self.stopping.wait(5)

    def tick(self):
        with self.lock:
            settings = self.settings()
            if not settings:
                return
            root = Path(settings['path'])
            if root.is_symlink():
                raise ValueError('The sync folder changed. Select it again.')
            marker = json.loads(self.files.read(root / 'reading-stacks.json'))
            workspace = settings['workspaceId']
            if marker.get('workspaceId') != workspace or marker.get('machineId') != self.machine():
                raise ValueError('The iCloud folder belongs to a different Mac. Select the original folder again.')
            for directory in ('requests', 'receipts', 'recordings'):
                if (root / directory).is_symlink():
                    raise ValueError('Sync folders cannot be symbolic links.')
            rejected = set()
            for name in sorted(self.files.names(root / 'requests'))[:500]:
                if self.stopping.is_set():
                    return
                if not re.fullmatch(r'[0-9a-f-]{36}\.json', name):
                    continue
                ident = name[:-5]
                envelope = {}
                try:
                    if str(uuid.UUID(ident)) != ident:
                        continue
                    envelope = json.loads(self.files.read(root / 'requests' / name))
                    if not isinstance(envelope, dict):
                        raise ValueError('Invalid share envelope.')
                    attempt = envelope.get('attempt')
                    if envelope.get('schemaVersion') != 1 or envelope.get('workspaceId') != workspace:
                        raise ValueError('This share belongs to another sync folder.')
                    if type(attempt) is not int or not 0 <= attempt <= 1_000_000:
                        raise ValueError('Invalid retry attempt.')
                    data = envelope['payload']
                    if not isinstance(data, dict) or data.get('id') != ident:
                        raise ValueError('Share ID does not match its filename.')
                    with self.core.db() as conn:
                        previous = conn.execute('SELECT attempt FROM cloud_requests WHERE workspace=? AND id=?', (workspace, ident)).fetchone()
                    if not previous or attempt > previous['attempt']:
                        # A retry only happens on a new explicit attempt; polling an error never resubmits it.
                        data['retry'] = data.get('retry') is True and (not previous or attempt > previous['attempt'])
                        self.core.mobile_sharing.accept(data)
                        sid = data.get('newStackId') or 'shared-' + ident if data.get('newStackName') else data['stackId']
                        with self.core.db() as conn:
                            conn.execute('INSERT OR REPLACE INTO cloud_requests VALUES (?,?,?,?)', (workspace, ident, attempt, sid))
                except (ValueError, KeyError, TypeError, HTTPException) as exc:
                    rejected.add(ident)
                    message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                    self.files.write_json(root / 'receipts' / name, dict(id=ident, attempt=envelope.get('attempt', 0) if isinstance(envelope, dict) else 0, status='error', error=message))
            with self.core.db() as conn:
                tracked = conn.execute('SELECT c.*,s.status,s.error FROM cloud_requests c JOIN share_inbox s ON s.id=c.id WHERE workspace=?', (workspace,)).fetchall()
            for row in tracked:
                if row['id'] in rejected:
                    continue
                self.files.write_json(root / 'receipts' / (row['id'] + '.json'), {k: row[k] for k in ('id', 'attempt', 'status', 'error')})
            if settings['autoAudio']:
                self.generate({row['stack_id'] for row in tracked if row['status'] == 'complete'})
            recordings = self.export_recordings(root)
            with self.core.db() as conn:
                stacks = [dict(row) for row in conn.execute('SELECT s.id,s.name FROM stacks s JOIN stack_positions p ON p.stack_id=s.id ORDER BY p.position')]
            self.files.write_json(root / 'catalog.json', dict(schemaVersion=1, workspaceId=workspace, stacks=stacks, recordings=recordings))
            self.last_sync = time.time()
            self.error = ''

    def generate(self, stack_ids):
        for sid in stack_ids:
            with self.core.db() as conn:
                stack = conn.execute('SELECT revision FROM stacks WHERE id=?', (sid,)).fetchone()
                if not stack:
                    continue
                # Failed/cancelled attempts stay visible for manual retry; never spin on paid briefings or errors.
                existing = conn.execute("SELECT 1 FROM jobs WHERE stack_id=? AND (stack_revision=? OR status IN ('queued','running','encoding'))", (sid, stack['revision'])).fetchone()
            if not existing:
                try:
                    self.core.create_stack_audio(sid, stack['revision'])
                except HTTPException as exc:
                    if exc.status_code != 409:
                        raise

    def export_recordings(self, root):
        with self.core.db() as conn:
            jobs = conn.execute("SELECT id,stack_id,output FROM jobs WHERE status='completed' ORDER BY created DESC").fetchall()
        result, seen = [], set()
        published = set(self.files.names(root / 'recordings'))
        for job in jobs:
            if not Path(job['output'] or '').is_file():
                continue
            if job['stack_id'] in seen:
                continue
            seen.add(job['stack_id'])
            target = root / 'recordings' / (job['id'] + '.stack')
            if target.name not in published:
                data, audio = portable_recording(self.core, job['id'])
                with tempfile.NamedTemporaryFile(suffix='.stack', dir=self.core.DATA) as temp:
                    with zipfile.ZipFile(temp.name, 'w', compression=zipfile.ZIP_STORED) as archive:
                        archive.writestr('manifest.json', json.dumps(data, ensure_ascii=False))
                        archive.write(audio, 'audio.mp3')
                    if Path(temp.name).stat().st_size > MAX_FILE_BYTES:
                        raise ValueError('Recording exceeds the 150 MB sync limit.')
                    self.files.write_file(target, Path(temp.name))
            result.append(dict(id=job['id'], file=target.name))
        return result


def register(app, core):
    sync = CloudSync(core)

    class Configuration(BaseModel):
        path: str
        autoAudio: bool = True

    @app.get('/api/cloud')
    def status():
        return sync.status()

    @app.post('/api/cloud')
    def configure(data: Configuration):
        try:
            return sync.configure(data.path, data.autoAudio)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.post('/api/cloud/choose')
    def choose():
        try:
            result = subprocess.run(['osascript', '-e', 'POSIX path of (choose folder with prompt "Choose an empty Reading Stacks folder in iCloud Drive (use New Folder if needed)")'], capture_output=True, text=True, timeout=180)
            if result.returncode:
                return {'cancelled': True}
            return sync.configure(result.stdout.strip(), True)
        except (ValueError, OSError, subprocess.SubprocessError) as exc:
            raise HTTPException(400, str(exc)) from exc

    @app.delete('/api/cloud')
    def disconnect():
        with sync.lock:
            sync.settings_path.unlink(missing_ok=True)
            sync.error = ''
        return sync.status()

    return sync
