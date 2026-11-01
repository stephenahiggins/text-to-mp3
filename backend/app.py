from contextlib import asynccontextmanager
from pathlib import Path
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
            parts = chunks(article['prepared'])
            update(job_id, total=len(parts), completed=0)
            paths = []
            for index, part in enumerate(parts):
                if stop.is_set() or cancelled.is_set():
                    break
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
                else:
                    update(job_id, status='completed', output=str(output))
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
    yield
    stop.set()
    await asyncio.to_thread(thread.join, 5)


app = FastAPI(title='Article Voice', lifespan=lifespan)


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
    async with httpx.AsyncClient(timeout=30, follow_redirects=False, headers={'User-Agent': 'ArticleVoice/0.1 (personal article reader)'}) as client:
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
                    if len(data) > 10_000_000:
                        raise ValueError('Page exceeds the 10 MB limit')
                return data.decode(response.encoding or 'utf-8', errors='replace')
        raise ValueError('Too many redirects')


def finish(article):
    article['prepared'] = prepare(article['original'])
    article['words'] = len(article['prepared'].split())
    if not article['words']:
        raise ValueError('No readable text found')
    return article


@app.post('/api/extract')
async def ingestion(source: Source):
    try:
        if source.url:
            html = await fetch_url(source.url)
            return await asyncio.to_thread(lambda: finish(extract(html, 'html', source.title, source.url)))
        return finish(extract(source.text, title=source.title))
    except Exception as exc:
        raise HTTPException(400, f'{exc}. You can paste or import authorised article text instead.') from exc


@app.post('/api/import')
async def import_file(file: UploadFile = File(...)):
    extension = Path(file.filename or '').suffix.lower()
    if extension not in ('.html','.htm','.md','.txt'):
        raise HTTPException(400, 'Choose an HTML, Markdown or TXT file')
    content = await file.read(2_000_001)
    if len(content) > 2_000_000:
        raise HTTPException(400, 'File exceeds 2 MB')
    try:
        return await asyncio.to_thread(lambda: finish(extract(content.decode('utf-8-sig'), 'html' if extension in ('.html','.htm') else 'md' if extension=='.md' else 'text', Path(file.filename).stem)))
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get('/api/config')
def config():
    return dict(voices=VOICES, output=str(OUTPUT), model_ready=all((ROOT/'models'/p).exists() for p in ['kokoro-v1.0.onnx','voices-v1.0.bin']))


class Article(BaseModel):
    title: str = Field(max_length=500)
    original: str = Field(max_length=2000000)
    prepared: str = Field(max_length=2000000)
    author: str = ''
    publication: str = ''
    date: str = ''
    url: str = ''


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
def retry(job_id: str):
    if get(job_id)['status'] not in ('failed','cancelled'):
        raise HTTPException(409, 'Only failed or cancelled conversions can be resumed')
    update(job_id, status='queued', error='')
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


if (ROOT/'frontend/dist').exists():
    app.mount('/', StaticFiles(directory=ROOT/'frontend/dist', html=True), name='frontend')
