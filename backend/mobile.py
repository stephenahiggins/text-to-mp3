"""Portable stack exports and a separate, capability-scoped LAN receiver service.

The editor remains loopback-only. The LAN listener exposes only explicitly sent
recordings, for one hour, and never exposes local paths or editing endpoints.
"""
import hashlib
import json
import os
import secrets
import socket
import subprocess
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask


MAX_FILE_BYTES = 150_000_000
_portable_lock = threading.Lock()


def recording(core, jid):
    job = core.get(jid)
    path = Path(job['output'])
    if job['status'] != 'completed' or not path.is_file():
        raise HTTPException(404, 'Completed audio unavailable')
    return job, path


def manifest(core, jid, audio_path=None):
    job, path = recording(core, jid)
    article = json.loads(job['article'])
    chapters_path = path.with_suffix('.chapters.json')
    chapters = json.loads(chapters_path.read_text()) if chapters_path.exists() else []
    path = audio_path or path
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return dict(schemaVersion=1, id=job['id'], stackId=job['stack_id'] or job['id'],
                revision=job['stack_revision'] or 1, title=article['title'],
                generatedAt=job['generated_at'] or job['created'],
                voice=job['voice'], speed=job['speed'], chapters=chapters,
                documents=[{k: ('' if k == 'briefing_summary' and not article.get('include_briefings') else d.get(k, '')) for k in ('id', 'title', 'date', 'author', 'url', 'prepared', 'publication', 'content_type', 'added_at', 'briefing_summary')}
                           for d in article.get('documents', [article])],
                audio=dict(file='audio.mp3', bytes=path.stat().st_size, sha256=digest.hexdigest()))


def portable_recording(core, jid):
    """Return a bounded phone/export copy without changing the master recording."""
    _, original = recording(core, jid)
    data = manifest(core, jid)
    # Budget for the entire .stack file, including documents and ZIP headers.
    budget = MAX_FILE_BYTES - len(json.dumps(data, ensure_ascii=False).encode()) - 4096
    if budget <= 0:
        raise HTTPException(413, 'The stack documents exceed 150 MB. Split the documents into smaller stacks.')
    if original.stat().st_size <= budget:
        return data, original
    stat = original.stat()
    key = hashlib.sha256(f'{original.resolve()}:{stat.st_size}:{stat.st_mtime_ns}:{budget}:v1'.encode()).hexdigest()
    target = original.parent / 'mobile-copies' / (key + '.mp3')
    with _portable_lock:
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                duration = float(subprocess.check_output([
                    'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                    '-of', 'csv=p=0', str(original)], timeout=60))
                rates = [r for r in (128, 112, 96, 80, 64, 56, 48, 40, 32, 24, 16, 8)
                         if duration > 0 and duration * r * 1000 / 8 <= budget * .97]
                if not rates:
                    raise HTTPException(413, 'This recording is too long to fit within 150 MB. Split it into smaller stacks.')
                with NamedTemporaryFile(suffix='.mp3', dir=target.parent, delete=False) as temp:
                    partial = Path(temp.name)
                try:
                    for rate in rates:
                        subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                            '-y', '-i', str(original), '-map', '0:a:0', '-map_metadata', '-1',
                            '-ac', '1', '-ar', '24000', '-codec:a', 'libmp3lame',
                            '-b:a', f'{rate}k', str(partial)], check=True, capture_output=True, timeout=3600)
                        if 0 < partial.stat().st_size <= budget:
                            partial.replace(target)
                            break
                    else:
                        raise HTTPException(413, 'This recording cannot fit within 150 MB. Split it into smaller stacks.')
                finally:
                    partial.unlink(missing_ok=True)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                raise HTTPException(503, 'Could not prepare a smaller phone copy. Check that FFmpeg is installed and retry.') from exc
    return manifest(core, jid, target), target


class Transfers:
    def __init__(self, core):
        self.core = core
        self.server = None
        self.lock = threading.Lock()
        self.sent = {}

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.server = None
        self.sent.clear()

    def start(self):
        if self.server:
            return
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                self.request.settimeout(30)
                super().setup()

            def log_message(self, *args):
                pass  # Capability URLs must not appear in access logs.

            def do_GET(self):
                self.serve(False)

            def do_POST(self):
                self.serve(True)

            def serve(self, ack):
                pieces = self.path.split('/')
                token = pieces[2] if len(pieces) == 4 and pieces[1] == 't' else ''
                action = pieces[3] if len(pieces) == 4 else ''
                with owner.lock:
                    transfer = owner.sent.get(token)
                if (not transfer or transfer['expiresAt'] <= time.time()
                        or action not in (('ack',) if ack else ('manifest', 'audio'))):
                    self.send_error(404)
                    return
                try:
                    recording(owner.core, transfer['jobId'])
                    audio = transfer['audioPath']
                    if ack:
                        with owner.lock:
                            transfer['downloadedAt'] = time.time()
                        data = b'{"ok":true}'
                    elif action == 'manifest':
                        data = json.dumps(transfer['manifest']).encode()
                    else:
                        data = None
                    self.send_response(200)
                    self.send_header('Content-Type', 'audio/mpeg' if data is None else 'application/json')
                    self.send_header('Content-Length', str(audio.stat().st_size if data is None else len(data)))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    if data is not None:
                        self.wfile.write(data)
                    else:
                        with audio.open('rb') as stream:
                            for block in iter(lambda: stream.read(256 * 1024), b''):
                                self.wfile.write(block)
                except (HTTPException, OSError):
                    # A broken download is retried by the phone; never leak paths.
                    self.close_connection = True

        self.server = ThreadingHTTPServer(('0.0.0.0', int(os.environ.get('ARTICLE_TRANSFER_PORT', '8766'))), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def send(self, jid):
        data, audio = portable_recording(self.core, jid)
        host = os.environ.get('ARTICLE_TRANSFER_HOST')
        if not host:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.connect(('192.0.2.1', 9))  # Select route; no packets sent.
                    host = sock.getsockname()[0]
            except OSError:
                raise HTTPException(503, 'Connect to Wi-Fi or set ARTICLE_TRANSFER_HOST')
        with self.lock:
            try:
                self.start()
            except OSError as exc:
                raise HTTPException(503, 'Phone transfer port unavailable') from exc
            self.sent = {k: v for k, v in self.sent.items() if v['expiresAt'] > time.time()}
            token = secrets.token_urlsafe(32)
            transfer = dict(jobId=jid, expiresAt=time.time() + 3600, downloadedAt=None, manifest=data, audioPath=audio)
            self.sent[token] = transfer
            return dict(token=token, url=f'http://{host}:{self.server.server_port}/t/{token}/manifest',
                        expiresAt=transfer['expiresAt'])


def register(app, core):
    transfers = Transfers(core)

    @app.post('/api/jobs/{jid}/send')
    def send(jid: str):
        return transfers.send(jid)

    @app.get('/api/transfers/{token}')
    def status(token: str):
        with transfers.lock:
            item = transfers.sent.get(token)
            if not item:
                raise HTTPException(404, 'Transfer unavailable')
            return {k: item[k] for k in ('jobId', 'expiresAt', 'downloadedAt')}

    @app.delete('/api/transfers/{token}')
    def revoke(token: str):
        with transfers.lock:
            transfers.sent.pop(token, None)
        return {'ok': True}

    @app.get('/api/jobs/{jid}/stack')
    def export(jid: str):
        data, audio = portable_recording(core, jid)
        with NamedTemporaryFile(suffix='.stack', delete=False) as temp:
            target = Path(temp.name)
        try:
            with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED) as archive:
                archive.writestr('manifest.json', json.dumps(data, ensure_ascii=False))
                archive.write(audio, 'audio.mp3')
            if target.stat().st_size > MAX_FILE_BYTES:
                raise HTTPException(413, 'Stack export exceeds 150 MB. Split it into smaller stacks.')
        except Exception:
            target.unlink(missing_ok=True)
            raise
        return FileResponse(target, media_type='application/zip', filename=f'{jid[:8]}.stack',
                            background=BackgroundTask(target.unlink, missing_ok=True))

    return transfers
