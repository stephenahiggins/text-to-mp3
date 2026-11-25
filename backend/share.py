"""Paired LAN inbox with durable receipts and restartable ingestion."""
import asyncio
import base64
import hashlib
import json
import secrets
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from fastapi import HTTPException
from fastapi.responses import RedirectResponse
from backend.discovery import Advertisement, Pairing


def register(app, core):
    class Sharing:
        server = None
        token = None
        thread = None

        def __init__(self):
            self.pairing = Pairing()
            self.advertisement = Advertisement()

        def initialize(self):
            with core.db() as conn:
                conn.execute('CREATE TABLE IF NOT EXISTS share_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
                conn.execute('CREATE TABLE IF NOT EXISTS share_inbox (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, error TEXT NOT NULL DEFAULT "", created REAL NOT NULL)')

        def restore(self):
            self.initialize()
            with core.db() as conn:
                row = conn.execute("SELECT value FROM share_settings WHERE key='token'").fetchone()
                conn.execute("UPDATE share_inbox SET status='queued' WHERE status='processing'")
            self.token = row['value'] if row else None
            self.start()

        def persist_token(self, token):
            self.initialize()
            with core.db() as conn:
                if token:
                    conn.execute("INSERT OR REPLACE INTO share_settings VALUES ('token', ?)", (token,))
                else:
                    conn.execute("DELETE FROM share_settings WHERE key='token'")
            core.os.chmod(core.DATA / 'history.sqlite3', 0o600)
            self.token = token

        def close(self):
            self.advertisement.close()
            self.pairing.clear()
            if self.server:
                self.server.shutdown()
                self.server.server_close()
                self.server = None
            if self.thread:
                self.stopping.set()
                self.thread.join()
                self.thread = None
            self.token = None

        def append(self, data, receipt=None):
            sid, url = data['stackId'], data.get('url', '')
            if data.get('newStackName') and receipt:
                sid = data.get('newStackId') or 'shared-' + receipt
            if data.get('pdf'):
                from backend.pdf import pdf_article
                article = core.finish(pdf_article(base64.b64decode(data['pdf']), data.get('title', '')), 'PDF')
                digest = hashlib.sha256(base64.b64decode(data['pdf'])).hexdigest()
            else:
                article = asyncio.run(core.ingestion(core.Source(url=url)))
                digest = ''
            article = core.Article.model_validate(article).model_dump()
            with core.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                if data.get('newStackName') and receipt:
                    conn.execute('INSERT OR IGNORE INTO stacks VALUES (?,?,?,?,?,?)', (sid, data['newStackName'].strip(), '[]', 1, time.time(), time.time()))
                    conn.execute('INSERT OR IGNORE INTO stack_positions VALUES (?,(SELECT coalesce(min(position),0)-1 FROM stack_positions))', (sid,))
                row = conn.execute('SELECT items FROM stacks WHERE id=?', (sid,)).fetchone()
                if not row:
                    raise ValueError('This stack no longer exists. Choose another stack.')
                items = json.loads(row['items'])
                if not any((url and i.get('url') == url) or (digest and i.get('source_hash') == digest) for i in items):
                    if len(items) >= 500:
                        raise ValueError('This stack is full. Choose another stack.')
                    items.append(dict(article, url=url, source_hash=digest, id=uuid.uuid4().hex, added_at=time.time()))
                    conn.execute('UPDATE stacks SET items=?,revision=revision+1,updated=? WHERE id=?', (json.dumps(items), time.time(), sid))
                if receipt:
                    # Receipt and append commit together, including a duplicate source.
                    conn.execute("UPDATE share_inbox SET status='complete',payload='{}',error='' WHERE id=?", (receipt,))

        def work(self):
            while not self.stopping.is_set():
                with core.db() as conn:
                    row = conn.execute("SELECT * FROM share_inbox WHERE status='queued' ORDER BY created LIMIT 1").fetchone()
                    if row:
                        conn.execute("UPDATE share_inbox SET status='processing' WHERE id=?", (row['id'],))
                if not row:
                    self.stopping.wait(.3)
                    continue
                try:
                    self.append(json.loads(row['payload']), row['id'])
                except Exception as exc:
                    message = str(exc.detail) if isinstance(exc, HTTPException) else str(exc)
                    with core.db() as conn:
                        conn.execute("UPDATE share_inbox SET status='error',payload='{}',error=? WHERE id=?", (message[:1000], row['id']))

        def validate(self, data, legacy=False):
            if not isinstance(data, dict):
                raise ValueError('Invalid share request.')
            sid, url = data['stackId'], data.get('url', '')
            if not isinstance(sid, str) or not isinstance(url, str) or len(url) > 8192:
                raise ValueError('Invalid URL or stack.')
            if 'newStackName' in data:
                name = data['newStackName']
                if legacy or sid or not isinstance(name, str) or not 1 <= len(name.strip()) <= 200:
                    raise ValueError('Enter a new stack name (1–200 characters), or select an existing stack.')
            if 'newStackId' in data:
                draft_id = data['newStackId']
                if ('newStackName' not in data or not isinstance(draft_id, str)
                        or not draft_id.startswith('shared-')
                        or draft_id != 'shared-' + str(uuid.UUID(draft_id[7:]))):
                    raise ValueError('Invalid new stack ID.')
            pdf = data.get('pdf')
            if pdf:
                raw = base64.b64decode(pdf, validate=True)
                if len(raw) > 20_000_000 or b'%PDF-' not in raw[:1024]:
                    raise ValueError('Choose a PDF smaller than 20 MB.')
            elif not url.startswith(('http://', 'https://')):
                raise ValueError('Choose a public HTTP or HTTPS URL.')
            if not isinstance(data.get('title', ''), str) or len(data.get('title', '')) > 500:
                raise ValueError('Invalid title.')

        def accept(self, data):
            self.validate(data)
            ident = str(uuid.UUID(data['id']))
            payload = json.dumps({k: data[k] for k in ('stackId', 'url', 'pdf', 'title', 'newStackName', 'newStackId') if k in data}, sort_keys=True)
            fingerprint = hashlib.sha256(payload.encode()).hexdigest()
            with core.db() as conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT * FROM share_inbox WHERE id=?', (ident,)).fetchone()
                if not row or (row['status'] == 'error' and data.get('retry') is True):
                    size = conn.execute("SELECT COALESCE(SUM(length(payload)),0) FROM share_inbox WHERE status!='complete'").fetchone()[0]
                    if size + len(payload) > 300_000_000:
                        raise HTTPException(507, 'Mac inbox is full. Resolve pending imports and retry.')
                if row:
                    if row['fingerprint'] != fingerprint:
                        raise HTTPException(409, 'Request ID already used for a different item.')
                    if row['status'] == 'error' and data.get('retry') is True:
                        conn.execute("UPDATE share_inbox SET status='queued',error='',payload=? WHERE id=?", (payload, ident))
                        result = dict(id=ident, status='queued', error='')
                    else:
                        result = {k: row[k] for k in ('id', 'status', 'error')}
                else:
                    conn.execute("INSERT INTO share_inbox(id,fingerprint,payload,status,created) VALUES (?,?,?,'queued',?)", (ident, fingerprint, payload, time.time()))
                    result = dict(id=ident, status='queued', error='')
            return result

        def start(self):
            if self.server:
                return
            self.initialize()
            owner = self

            class Handler(BaseHTTPRequestHandler):
                def setup(self):
                    self.request.settimeout(40)
                    super().setup()

                def log_message(self, *args):
                    pass

                def reply(self, status, data):
                    payload = json.dumps(data).encode()
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(payload)))
                    self.send_header('Cache-Control', 'no-store')
                    self.end_headers()
                    try:
                        self.wfile.write(payload)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

                def authorized(self):
                    return owner.token and secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + owner.token)

                def do_GET(self):
                    if not self.authorized():
                        return self.reply(401, {'detail': 'Connect again using a new link from your Mac.'})
                    with core.db() as conn:
                        if self.path == '/stacks':
                            rows = conn.execute('SELECT s.id,s.name FROM stacks s JOIN stack_positions p ON p.stack_id=s.id ORDER BY p.position,s.created DESC').fetchall()
                            return self.reply(200, [dict(r) for r in rows])
                        if self.path.startswith('/inbox/'):
                            row = conn.execute('SELECT id,status,error FROM share_inbox WHERE id=?', (self.path[7:],)).fetchone()
                            if row:
                                return self.reply(200, dict(row))
                    self.reply(404, {'detail': 'Item not found on this Mac.'})

                def do_POST(self):
                    if self.path == '/pair':
                        # Only this secret polling request can retrieve its approval.
                        # Browsers cannot pair through cross-origin requests.
                        if self.headers.get('Origin'):
                            return self.reply(403, {'detail': 'Pair from the Reading Stacks app.'})
                        try:
                            length = int(self.headers.get('Content-Length', '0'))
                            if not 0 < length <= 1024:
                                raise ValueError('Invalid pairing request size.')
                            data = json.loads(self.rfile.read(length))
                            return self.reply(200, owner.pairing.request(data['secret'], data['name']))
                        except (ValueError, KeyError, TypeError):
                            return self.reply(400, {'detail': 'Invalid pairing request. Try again shortly.'})
                    if not self.authorized():
                        return self.reply(401, {'detail': 'Connect again using a new link from your Mac.'})
                    if self.path not in ('/items', '/inbox'):
                        return self.reply(404, {})
                    try:
                        length = int(self.headers.get('Content-Length', '0'))
                        if not 0 < length <= (27_000_000 if self.path == '/inbox' else 16384):
                            return self.reply(400, {'detail': 'Invalid request size. PDFs must be under 20 MB.'})
                        data = json.loads(self.rfile.read(length))
                        owner.validate(data, legacy=self.path == '/items')
                        sid = data['stackId']
                        if self.path == '/items':
                            with core.db() as conn:
                                if not conn.execute('SELECT id FROM stacks WHERE id=?', (sid,)).fetchone():
                                    return self.reply(404, {'detail': 'This stack no longer exists.'})
                            owner.append(data)
                            return self.reply(200, {'ok': True})
                        self.reply(202, owner.accept(data))
                    except HTTPException as exc:
                        self.reply(exc.status_code, {'detail': str(exc.detail)})
                    except (ValueError, KeyError, TypeError) as exc:
                        self.reply(400, {'detail': str(exc) or 'Invalid URL or stack.'})
                    except Exception:
                        self.reply(500, {'detail': 'Could not save. Check your Mac and try again.'})

            self.server = ThreadingHTTPServer(('0.0.0.0', int(core.os.environ.get('ARTICLE_SHARE_PORT', '8767'))), Handler)
            self.server.daemon_threads = True
            threading.Thread(target=self.server.serve_forever, daemon=True).start()
            self.stopping = threading.Event()
            self.thread = threading.Thread(target=self.work, daemon=True)
            self.thread.start()
            with core.db() as conn:
                conn.execute("INSERT OR IGNORE INTO share_settings VALUES ('machine_id', ?)", (uuid.uuid4().hex,))
                ident = conn.execute("SELECT value FROM share_settings WHERE key='machine_id'").fetchone()['value']
            if core.os.environ.get('ARTICLE_DISCOVERY', '1') != '0':
                self.advertisement.start(self.server.server_port, ident)

    sharing = Sharing()

    @app.post('/api/share/connect')
    def connect():
        host = core.os.environ.get('ARTICLE_TRANSFER_HOST')
        if not host:
            # Bonjour's stable hostname follows the Mac when DHCP changes its address.
            try:
                host = core.subprocess.check_output(['/usr/sbin/scutil', '--get', 'LocalHostName'], text=True).strip() + '.local'
            except (OSError, core.subprocess.CalledProcessError):
                with core.socket.socket(core.socket.AF_INET, core.socket.SOCK_DGRAM) as sock:
                    sock.connect(('192.0.2.1', 9))
                    host = sock.getsockname()[0]
        sharing.start()
        sharing.pairing.clear()
        sharing.persist_token(secrets.token_urlsafe(32))
        return {'connectionLink': f'http://{host}:{sharing.server.server_port}#{sharing.token}'}

    @app.delete('/api/share/connect')
    def disconnect():
        sharing.pairing.clear()
        sharing.persist_token(None)
        return {'ok': True}

    @app.get('/api/share/connect')
    def connection_status():
        return {'enabled': sharing.token is not None}

    @app.get('/api/share/requests')
    def pairing_requests():
        return sharing.pairing.pending()

    @app.post('/api/share/requests/{ident}/approve')
    def approve_pairing(ident: str):
        token = sharing.token or secrets.token_urlsafe(32)
        try:
            # Persist before releasing the credential to the phone.
            sharing.persist_token(token)
            sharing.pairing.decide(ident, token)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {'ok': True}

    @app.post('/api/share/requests/{ident}/reject')
    def reject_pairing(ident: str):
        try:
            sharing.pairing.decide(ident, None)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {'ok': True}

    @app.get('/share-setup')
    def setup_page():
        return RedirectResponse('/#/iphone-sharing')

    return sharing
