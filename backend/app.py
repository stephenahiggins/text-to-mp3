from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
import asyncio
import ipaddress
import json
import logging
import os
import queue
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from backend.text import extract, prepare, chunks, filename
from backend.audio import Engine, encode, ROOT
from backend.briefings import BriefingCancelled, configured as briefings_configured, model as briefing_model

from dotenv import load_dotenv
load_dotenv(ROOT / ".env", override=False)

DATA = Path(os.environ.get('ARTICLE_DATA_DIR', ROOT / '.data'))
OUTPUT = Path(os.environ.get('ARTICLE_OUTPUT_DIR', Path.home() / 'Music/Articles'))
CACHE = DATA / 'cache'
VOICES = ['bm_george', 'bm_lewis', 'bm_daniel', 'bm_fable', 'bf_emma', 'bf_isabella', 'bf_alice', 'bf_lily']
jobs = queue.Queue()
engine = Engine()
stop = threading.Event()
cancellations = {}
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')


def db():
    connection = sqlite3.connect(DATA / 'history.sqlite3', timeout=30)
    connection.row_factory = sqlite3.Row
    return connection


def update(job_id, **values):
    with db() as conn:
        conn.execute('UPDATE jobs SET ' + ','.join(f'{k}=?' for k in values) + ' WHERE id=?', [*values.values(), job_id])


def get(job_id):
    with db() as conn:
        row = conn.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
    if not row:
        raise HTTPException(404, 'Conversion not found')
    return dict(row)


def article_parts(article):
    if 'documents' not in article:
        return chunks(article['prepared'])
    from backend.narration import stack_parts
    return stack_parts(article)


def worker():
    while not stop.is_set():
        try:
            job_id = jobs.get(timeout=0.5)
        except queue.Empty:
            continue
        try:
            job = get(job_id)
            if job['status'] != 'queued':
                continue
            cancelled = threading.Event()
            cancellations[job_id] = cancelled
            update(job_id, status='running', error='')
            article = json.loads(job['article'])
            from backend.briefings import prepare_job
            def check_briefing():
                if stop.is_set() or cancelled.is_set(): raise BriefingCancelled()
            def briefing_progress(index, snapshot=None):
                values = dict(stage='briefing', current_document=index)
                if snapshot is not None: values['article'] = json.dumps(snapshot)
                update(job_id, **values)
            prepare_job(article, db, briefing_progress, check_briefing)
            check_briefing()
            update(job_id, stage='narrating', article=json.dumps(article))
            parts = article_parts(article)
            update(job_id, total=len(parts), completed=0)
            paths = []
            for index, part in enumerate(parts):
                if stop.is_set() or cancelled.is_set():
                    break
                update(job_id, current_document=part.get('document', 1))
                paths.append(engine.render(part, job['voice'], job['speed'], CACHE))
                update(job_id, completed=index+1)
            else:
                if cancelled.is_set() or stop.is_set():
                    continue
                update(job_id, status='encoding')
                output = OUTPUT / (filename(article['title']) + '-' + job_id[:8] + '.mp3')
                encode(paths, output, article)
                output.with_suffix('.json').write_text(json.dumps(article, ensure_ascii=False, indent=2), encoding='utf-8')
                if cancelled.is_set():
                    output.unlink(missing_ok=True)
                    output.with_suffix('.json').unlink(missing_ok=True)
                    output.with_suffix('.chapters.json').unlink(missing_ok=True)
                else:
                    with db() as conn:
                        conn.execute("UPDATE jobs SET status='completed',output=?,generated_at=? WHERE id=?", (str(output), time.time(), job_id))
                        if job.get('stack_id'):
                            from backend.stacks import prune_audio
                            prune_audio(conn, job['stack_id'])
        except BriefingCancelled:
            pass
        except Exception as exc:
            logging.exception('Conversion %s failed', job_id)
            if get(job_id)['status'] not in ('cancelled', 'queued'):
                update(job_id, status='failed', error=str(exc))
        finally:
            cancellations.pop(job_id, None)
            jobs.task_done()


@asynccontextmanager
async def lifespan(app):
    CACHE.mkdir(parents=True, exist_ok=True)
    logging.getLogger().addHandler(logging.FileHandler(DATA / 'application.log'))
    with db() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, article TEXT, voice TEXT, speed REAL, status TEXT, total INTEGER DEFAULT 0, completed INTEGER DEFAULT 0, output TEXT DEFAULT "", error TEXT DEFAULT "", created REAL)')
        from backend.stacks import migrate
        migrate(conn)
        conn.execute("UPDATE jobs SET status='queued' WHERE status IN ('running','encoding')")
        pending = conn.execute("SELECT id FROM jobs WHERE status='queued' ORDER BY created").fetchall()
    # Cache is reusable for 30 days; partial writes are never reused.
    for path in CACHE.iterdir():
        if '.part.' in path.name or time.time()-path.stat().st_mtime > 30*86400:
            path.unlink(missing_ok=True)
    for path in OUTPUT.glob('*.part.mp3') if OUTPUT.exists() else []:
        path.unlink(missing_ok=True)
    for row in pending:
        jobs.put(row['id'])
    stop.clear()
    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    mobile_sharing.restore()
    yield
    stop.set()
    await asyncio.to_thread(thread.join, 5)
    await asyncio.to_thread(mobile_transfers.close)
    await asyncio.to_thread(mobile_sharing.close)


app = FastAPI(title='Reading Stacks', lifespan=lifespan)


@app.middleware('http')
async def local_only(request: Request, call_next):
    host = request.headers.get('host', '').split(':')[0]
    origin = request.headers.get('origin')
    if host not in ('localhost', '127.0.0.1', 'testserver') or (origin and origin not in ('http://localhost:8765', 'http://127.0.0.1:8765', 'http://localhost:5173', 'http://127.0.0.1:5173')):
        from fastapi.responses import JSONResponse
        return JSONResponse({'detail': 'Only local application requests are accepted'}, status_code=403)
    return await call_next(request)


class Source(BaseModel):
    text: str = Field(default='', max_length=2000000)
    url: str = ''
    title: str = Field(default='Untitled article', max_length=500)


async def fetch_url(url):
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, headers={'User-Agent': 'ReadingStacks/1.0 (https://github.com/stephenahiggins/text-to-mp3) httpx/' + httpx.__version__}) as client:
        for _ in range(6):
            parsed = urlparse(url)
            if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username:
                raise ValueError('Enter a public HTTP or HTTPS article URL')
            addresses = await asyncio.to_thread(socket.getaddrinfo, parsed.hostname, parsed.port or (443 if parsed.scheme=='https' else 80))
            if any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError('Only publicly accessible article addresses are supported')
            async with client.stream('GET', url) as response:
                if response.is_redirect:
                    from urllib.parse import urljoin
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                data = bytearray()
                async for block in response.aiter_bytes():
                    data.extend(block)
                    if len(data) > 20_000_000:
                        raise ValueError('Document exceeds the 20 MB limit')
                return bytes(data), response.headers.get('content-type', ''), response.encoding or 'utf-8'
        raise ValueError('Too many redirects')


def finish(article, content_type='document'):
    article['content_type'] = content_type
    article['prepared'] = prepare(article['original'])
    article['words'] = len(article['prepared'].split())
    if not article['words']:
        raise ValueError('No readable text found')
    return article


@app.post('/api/extract')
async def ingestion(source: Source):
    try:
        if source.url:
            content, media_type, encoding = await fetch_url(source.url)
            if 'application/pdf' in media_type.lower() or b'%PDF-' in content[:1024]:
                from backend.pdf import pdf_article
                return await asyncio.to_thread(lambda: finish(pdf_article(content, source.title, source.url), 'PDF'))
            html = content.decode(encoding, errors='replace')
            return await asyncio.to_thread(lambda: finish(extract(html, 'html', source.title, source.url), 'article'))
        return finish(extract(source.text, title=source.title), 'text')
    except Exception as exc:
        raise HTTPException(400, f'{exc}. You can paste or import authorised article text instead.') from exc


@app.post('/api/import')
async def import_file(file: UploadFile = File(...)):
    extension = Path(file.filename or '').suffix.lower()
    if extension not in ('.html','.htm','.md','.txt','.pdf','.docx'):
        raise HTTPException(400, 'Choose An HTML, Markdown, TXT, PDF Or DOCX File')
    content = await file.read(20_000_001)
    if len(content) > 20_000_000:
        raise HTTPException(400, 'File Exceeds 20 MB')
    try:
        if extension == '.pdf':
            from backend.pdf import pdf_article
            return await asyncio.to_thread(lambda: finish(pdf_article(content, Path(file.filename or 'PDF document').stem), 'PDF'))
        if extension == '.docx':
            from backend.imports import document_text
            text = await asyncio.to_thread(document_text, content, extension)
            return finish(extract(text, title=Path(file.filename).stem), 'PDF' if extension=='.pdf' else 'Word document')
        return await asyncio.to_thread(lambda: finish(extract(content.decode('utf-8-sig'), 'html' if extension in ('.html','.htm') else 'md' if extension=='.md' else 'text', Path(file.filename).stem)))
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/config')
def config():
    return dict(briefings_ready=briefings_configured(), briefing_model=briefing_model(), voices=VOICES, output=str(OUTPUT), model_ready=all((ROOT/'models'/p).exists() for p in ['kokoro-v1.0.onnx','voices-v1.0.bin']))


class Article(BaseModel):
    title: str = Field(max_length=500)
    original: str = Field(max_length=2000000)
    prepared: str = Field(max_length=2000000)
    author: str = ''
    publication: str = ''
    date: str = ''
    url: str = ''
    content_type: str = Field(default='document', max_length=50)
    source_hash: str = Field(default='', max_length=64)
    briefing_override: str | None = Field(default=None, max_length=1000)
    narration_mode: Literal['whole', 'briefing'] = 'whole'
    briefing_nonce: int = Field(default=0, ge=0)


class Conversion(BaseModel):
    article: Article
    voice: str = 'bm_george'
    speed: float = Field(default=1, ge=0.5, le=2)


@app.post('/api/jobs')
def create_job(data: Conversion):
    if data.voice not in VOICES or not data.article.prepared.strip():
        raise HTTPException(400, 'Select a British voice and provide narration text')
    job_id = uuid.uuid4().hex
    article = data.article.model_dump()
    with db() as conn:
        conn.execute('INSERT INTO jobs (id,article,voice,speed,status,total,created) VALUES (?,?,?,?,?,?,?)', (job_id,json.dumps(article),data.voice,data.speed,'queued',len(chunks(article['prepared'])),time.time()))
    jobs.put(job_id)
    return get(job_id)


@app.get('/api/jobs')
def history():
    with db() as conn:
        rows = conn.execute('SELECT id,voice,speed,status,total,completed,output,error,created,json_extract(article,"$.title") AS title FROM jobs ORDER BY created DESC LIMIT 100').fetchall()
    return [dict(row) for row in rows]


@app.post('/api/jobs/{job_id}/cancel')
def cancel(job_id: str):
    job = get(job_id)
    if job['status'] in ('queued','running','encoding'):
        if job_id in cancellations:
            cancellations[job_id].set()
        update(job_id, status='cancelled')
    return get(job_id)


@app.post('/api/jobs/{job_id}/retry')
def retry(job_id: str, skip_briefings: bool = False):
    job = get(job_id)
    if job.get('stack_id'):
        with db() as conn:
            active = conn.execute("SELECT id FROM jobs WHERE stack_id=? AND status IN ('queued','running','encoding')", (job['stack_id'],)).fetchone()
            if active: raise HTTPException(409, 'This Stack Is Already Being Audiofied')
    if job['status'] not in ('failed','cancelled'):
        raise HTTPException(409, 'Only failed or cancelled conversions can be resumed')
    if skip_briefings:
        article = json.loads(job['article'])
        if any(d.get('narration_mode') == 'briefing' for d in article.get('documents', [])):
            raise HTTPException(409, 'Briefing Only Items Need A Briefing. Retry Or Change Them To Whole Article.')
        article['include_briefings'] = False
        update(job_id, article=json.dumps(article))
    update(job_id, status='queued', stage='', error='')
    jobs.put(job_id)
    return get(job_id)


@app.get('/api/jobs/{job_id}/audio')
def audio(job_id: str):
    job = get(job_id)
    if job['status'] != 'completed' or not Path(job['output']).is_file():
        raise HTTPException(404, 'Audio file unavailable')
    return FileResponse(job['output'], media_type='audio/mpeg', filename=Path(job['output']).name)


@app.post('/api/jobs/{job_id}/reveal')
def reveal(job_id: str):
    job = get(job_id)
    if job['status'] != 'completed' or not Path(job['output']).is_file():
        raise HTTPException(404, 'Audio file unavailable')
    subprocess.run(['open', '-R', job['output']], check=True)
    return {'ok': True}


import sys
from backend.stacks import register
register(app, sys.modules[__name__])
from backend.mobile import register as register_mobile
mobile_transfers = register_mobile(app, sys.modules[__name__])
from backend.share import register as register_share
mobile_sharing = register_share(app, sys.modules[__name__])

if (ROOT/'frontend/dist').exists():
    app.mount('/', StaticFiles(directory=ROOT/'frontend/dist', html=True), name='frontend')
