"""Persistent editable stacks and immutable conversion snapshots."""
import json
import time
import uuid
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel, Field


def migrate(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS stacks (id TEXT PRIMARY KEY, name TEXT NOT NULL, items TEXT NOT NULL, revision INTEGER NOT NULL, created REAL, updated REAL)')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(jobs)')}
    for column, definition in [('stack_id','TEXT'), ('stack_revision','INTEGER'), ('current_document','INTEGER DEFAULT 0'), ('generated_at','REAL')]:
        if column not in columns:
            conn.execute(f'ALTER TABLE jobs ADD COLUMN {column} {definition}')
    for job in conn.execute('SELECT * FROM jobs WHERE stack_id IS NULL').fetchall():
        article = json.loads(job['article'])
        sid = uuid.uuid4().hex
        conn.execute('INSERT INTO stacks VALUES (?,?,?,?,?,?)', (sid,article['title'],json.dumps([dict(id=uuid.uuid4().hex,**article)]),1,job['created'],job['created']))
        conn.execute('UPDATE jobs SET stack_id=?,stack_revision=1 WHERE id=?',(sid,job['id']))

    conn.execute('CREATE TABLE IF NOT EXISTS stack_positions (stack_id TEXT PRIMARY KEY, position INTEGER NOT NULL)')
    for index, row in enumerate(conn.execute('SELECT id FROM stacks ORDER BY updated DESC').fetchall()):
        conn.execute('INSERT OR IGNORE INTO stack_positions VALUES (?,?)', (row['id'],index))
    for job in conn.execute("SELECT id,output FROM jobs WHERE status='completed' AND generated_at IS NULL").fetchall():
        if job['output'] and Path(job['output']).is_file():
            conn.execute('UPDATE jobs SET generated_at=? WHERE id=?',(Path(job['output']).stat().st_mtime,job['id']))


def register(app, core):
    class StackInput(BaseModel):
        name: str = Field(min_length=1,max_length=500)
    class StackEdit(StackInput):
        revision: int
        items: list[dict] = Field(max_length=500)
    class Create(BaseModel):
        voice: str = 'bm_george'
        speed: float = Field(default=1,ge=.5,le=2)
        revision: int

    def load(conn,sid):
        row=conn.execute('SELECT * FROM stacks WHERE id=?',(sid,)).fetchone()
        if not row: raise HTTPException(404,'Stack Not Found')
        result=dict(row)
        result['items']=json.loads(result['items'])
        versions=[dict(r) for r in conn.execute("SELECT id,status,total,completed,error,stack_revision,voice,speed,created,generated_at,current_document,coalesce(json_array_length(article,'$.documents'),1) AS document_count FROM jobs WHERE stack_id=? ORDER BY created DESC",(sid,))]
        result['versions']=versions
        active=next((v for v in versions if v['status'] in ('queued','running','encoding')),None)
        completed=next((v for v in versions if v['status']=='completed'),None)
        result['status']=active['status'].title() if active else ('Audiofied' if completed and completed['stack_revision']==result['revision'] else 'Changes Not Yet Audiofied' if completed else 'Draft')
        return result

    @app.get('/api/stacks')
    def listing():
        with core.db() as conn:
            return [load(conn,r['id']) for r in conn.execute('SELECT s.id FROM stacks s JOIN stack_positions p ON p.stack_id=s.id ORDER BY p.position,s.created DESC').fetchall()]

    @app.post('/api/stacks')
    def create(data: StackInput):
        sid=uuid.uuid4().hex
        if not data.name.strip(): raise HTTPException(400,'Enter A Stack Name')
        with core.db() as conn:
            conn.execute('INSERT INTO stacks VALUES (?,?,?,?,?,?)',(sid,data.name.strip(),'[]',1,time.time(),time.time()))
            conn.execute('INSERT INTO stack_positions VALUES (?,(SELECT coalesce(min(position),0)-1 FROM stack_positions))',(sid,))
            return load(conn,sid)

    class Move(BaseModel):
        direction: str | None = None
        target_id: str | None = None

    @app.post('/api/stacks/{sid}/move')
    def move(sid: str, data: Move):
        if data.target_id is None and data.direction not in ('up','down'): raise HTTPException(400,'Invalid Direction')
        with core.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            load(conn,sid)
            ids=[r['stack_id'] for r in conn.execute('SELECT stack_id FROM stack_positions ORDER BY position')]
            index=ids.index(sid)
            if data.target_id is not None:
                if data.target_id not in ids: raise HTTPException(404,'Target Stack Not Found')
                target=ids.index(data.target_id)
                ids.pop(index)
                ids.insert(target,sid)
            else:
                target=index+(-1 if data.direction=='up' else 1)
                if 0<=target<len(ids): ids[index],ids[target]=ids[target],ids[index]
            for position, ident in enumerate(ids):
                conn.execute('UPDATE stack_positions SET position=? WHERE stack_id=?',(position,ident))
        return {'ok':True}

    @app.put('/api/stacks/{sid}')
    def edit(sid: str, data: StackEdit):
        if not data.name.strip(): raise HTTPException(400,'Enter A Stack Name')
        items=[]
        seen=set()
        for item in data.items:
            article=core.Article.model_validate(item).model_dump()
            ident=item.get('id') or uuid.uuid4().hex
            if not isinstance(ident,str) or ident in seen: raise HTTPException(400,'Invalid Document Identifier')
            seen.add(ident)
            items.append(dict(id=ident,**article))
        with core.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            current=load(conn,sid)
            if current['revision']!=data.revision: raise HTTPException(409,'This Stack Changed. Reload Before Saving.')
            if current['name']==data.name.strip() and current['items']==items: return current
            conn.execute('UPDATE stacks SET name=?,items=?,revision=revision+1,updated=? WHERE id=?',(data.name.strip(),json.dumps(items),time.time(),sid))
            return load(conn,sid)

    @app.post('/api/stacks/{sid}/create')
    def audio(sid: str, data: Create):
        if data.voice not in core.VOICES: raise HTTPException(400,'Select A British Voice')
        with core.db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            stack=load(conn,sid)
            if stack['revision']!=data.revision: raise HTTPException(409,'Save Your Changes Before Creating Audio')
            if any(v['status'] in ('queued','running','encoding') for v in stack['versions']): raise HTTPException(409,'This Stack Is Already Being Audiofied')
            if not stack['items'] or any(not i['prepared'].strip() for i in stack['items']): raise HTTPException(400,'Add Readable Documents First')
            # Store the full snapshot: editing the draft cannot change queued audio.
            article=dict(title=stack['name'],author='',publication='Article Stacks',url='',date='',original='\n\n'.join(i['original'] for i in stack['items']),prepared='\n\n'.join(i['prepared'] for i in stack['items']),documents=stack['items'])
            jid=uuid.uuid4().hex
            total=len(core.article_parts(article))
            conn.execute('INSERT INTO jobs (id,article,voice,speed,status,total,created,stack_id,stack_revision) VALUES (?,?,?,?,?,?,?,?,?)',(jid,json.dumps(article),data.voice,data.speed,'queued',total,time.time(),sid,stack['revision']))
        core.jobs.put(jid)
        return core.get(jid)
