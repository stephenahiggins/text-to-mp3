import json
import threading
import time
from io import BytesIO
from fastapi.testclient import TestClient
from backend import app as core
from backend.imports import document_text


def test_stacks_snapshot_edits_and_migration(tmp_path,monkeypatch):
    for name,value in [('DATA',tmp_path),('CACHE',tmp_path/'cache'),('OUTPUT',tmp_path/'output')]:monkeypatch.setattr(core,name,value)
    entered=threading.Event();release=threading.Event()
    class Engine:
        def render(self,*args):
            entered.set();release.wait(3)
            return tmp_path/'dummy.wav'
    monkeypatch.setattr(core,'engine',Engine())
    def encode(paths,output,article):
        output.parent.mkdir(exist_ok=True);output.write_bytes(b'mp3')
    monkeypatch.setattr(core,'encode',encode)
    with TestClient(core.app) as c:
        s=c.post('/api/stacks',json={'name':'Weekend Reading'}).json()
        assert s['status']=='Draft'
        assert c.post(f"/api/stacks/{s['id']}/create",json={'revision':1}).status_code==400
        article=c.post('/api/extract',json={'text':'The river is still.','title':'River'}).json()
        s=c.put('/api/stacks/'+s['id'],json={'name':s['name'],'revision':1,'items':[dict(id='river',**article)]}).json()
        assert s['revision']==2
        job=c.post(f"/api/stacks/{s['id']}/create",json={'revision':2}).json()
        assert entered.wait(3)
        assert c.post(f"/api/stacks/{s['id']}/create",json={'revision':2}).status_code==409
        # A new document cannot leak into the frozen narration snapshot.
        second=dict(article,id='second',title='Evening',prepared='The stars are bright.')
        edited=c.put('/api/stacks/'+s['id'],json={'name':s['name'],'revision':2,'items':[second,*s['items']]}).json()
        assert edited['revision']==3
        assert len(json.loads(core.get(job['id'])['article'])['documents'])==1
        assert c.put('/api/stacks/'+s['id'],json={'name':'Stale','revision':2,'items':[]}).status_code==409
        release.set()
        for _ in range(100):
            if core.get(job['id'])['status']=='completed':break
            time.sleep(.02)
        current=c.get('/api/stacks').json()[0]
        assert current['status']=='Changes Not Yet Audiofied'
        assert current['items'][0]['id']=='second'
        newer=c.post(f"/api/stacks/{s['id']}/create",json={'revision':3}).json()
        for _ in range(100):
            if core.get(newer['id'])['status']=='completed':break
            time.sleep(.02)
        assert c.get('/api/stacks').json()[0]['status']=='Audiofied'
        # Legacy job is imported into a single-document stack on restart.
        legacy=c.post('/api/jobs',json={'article':article}).json()
    with TestClient(core.app) as c:
        stacks=c.get('/api/stacks').json()
        assert len(stacks)==2
        assert any(s['name']=='River' for s in stacks)
        assert len(next(s for s in stacks if s['name']=='Weekend Reading')['versions'])==1


def test_word_import_preserves_order():
    from docx import Document
    doc=Document();doc.add_heading('Garden',0);doc.add_paragraph('First light.');doc.add_paragraph('Evening falls.')
    stream=BytesIO();doc.save(stream)
    assert document_text(stream.getvalue(),'.docx')=='Garden\n\nFirst light.\n\nEvening falls.'


def test_stack_order_persists_without_changing_revision(tmp_path, monkeypatch):
    for name, value in [('DATA', tmp_path), ('CACHE', tmp_path/'cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, name, value)
    with TestClient(core.app) as c:
        first=c.post('/api/stacks',json={'name':'First'}).json()
        second=c.post('/api/stacks',json={'name':'Second'}).json()
        assert first['created'] > 0
        assert c.post(f"/api/stacks/{first['id']}/move",json={'direction':'up'}).status_code==200
        assert [s['id'] for s in c.get('/api/stacks').json()]==[first['id'],second['id']]
        c.put('/api/stacks/'+second['id'],json={'name':'Renamed','items':[],'revision':1})
    with TestClient(core.app) as c:
        stacks=c.get('/api/stacks').json()
        assert [s['id'] for s in stacks]==[first['id'],second['id']]
        assert stacks[0]['revision']==1
        assert stacks[0]['created']==first['created']
        assert c.post(f"/api/stacks/{first['id']}/move",json={'direction':'down'}).status_code==200
        assert c.get('/api/stacks').json()[0]['id']==second['id']

        third=c.post('/api/stacks',json={'name':'Third'}).json()
        assert c.post(f"/api/stacks/{first['id']}/move",json={'target_id':third['id']}).status_code==200
        assert [s['id'] for s in c.get('/api/stacks').json()]==[first['id'],third['id'],second['id']]
        assert c.post(f"/api/stacks/{first['id']}/move",json={'target_id':'missing'}).status_code==404
    with TestClient(core.app) as c:
        assert [s['id'] for s in c.get('/api/stacks').json()]==[first['id'],third['id'],second['id']]


def test_single_audio_cleanup_preserves_recording_during_failed_replacement(tmp_path):
    import sqlite3
    from backend.stacks import prune_audio
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    conn.execute('CREATE TABLE jobs (id TEXT, stack_id TEXT, status TEXT, output TEXT, created REAL)')
    def add(ident, status, created):
        path = tmp_path / (ident + '.mp3')
        for asset in (path, path.with_suffix('.json'), path.with_suffix('.chapters.json')):
            asset.write_text(ident)
        conn.execute('INSERT INTO jobs VALUES (?,?,?,?,?)', (ident, 'stack', status, str(path), created))
        return path
    old = add('old', 'completed', 1)
    current = add('current', 'completed', 2)
    failed = add('failed', 'failed', 3)
    prune_audio(conn, 'stack')
    assert not old.exists() and not old.with_suffix('.json').exists()
    assert not old.with_suffix('.chapters.json').exists()
    assert current.exists() and failed.exists()
    replacement = add('replacement', 'completed', 4)
    prune_audio(conn, 'stack')
    assert replacement.exists() and not current.exists() and not failed.exists()
    assert [r['id'] for r in conn.execute('SELECT id FROM jobs')] == ['replacement']


def test_item_mode_persists_and_estimate_uses_it(tmp_path, monkeypatch):
    for name, value in [('DATA', tmp_path), ('CACHE', tmp_path/'cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, name, value)
    with TestClient(core.app) as c:
        stack = c.post('/api/stacks', json={'name':'Reading'}).json()
        url = '/api/stacks/' + stack['id']
        doc = dict(id='one', title='Article', prepared='Words here. ' * 500, original='Source', narration_mode='whole')
        stack = c.put(url, json=dict(name='Reading', revision=1, items=[doc])).json()
        full = c.get(url+'/estimate').json()['seconds']
        doc.update(narration_mode='briefing', briefing_override='A short overview.')
        stack = c.put(url, json=dict(name='Reading', revision=stack['revision'], items=[doc])).json()
        estimate = c.get(url+'/estimate').json()
        assert estimate['seconds'] < full / 10
        assert estimate['pending_briefings'] == 0
        assert c.get(url+'/estimate?speed=0').status_code == 400
    with TestClient(core.app) as c:
        assert c.get('/api/stacks').json()[0]['items'][0]['narration_mode'] == 'briefing'


def test_delete_stack_removes_its_recordings_and_survives_restart(tmp_path, monkeypatch):
    for name, value in [('DATA', tmp_path), ('CACHE', tmp_path/'cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, name, value)
    with TestClient(core.app) as c:
        stack = c.post('/api/stacks', json={'name':'Delete me'}).json()
        other = c.post('/api/stacks', json={'name':'Keep me'}).json()
        output = tmp_path/'recording.mp3'
        assets = [output, output.with_suffix('.json'), output.with_suffix('.chapters.json')]
        for path in assets: path.write_text('test')
        with core.db() as conn:
            conn.execute('INSERT INTO jobs (id,stack_id,status,output,article,created) VALUES (?,?,?,?,?,?)',
                         ('recording',stack['id'],'completed',str(output),'{}',time.time()))
        core.cancellations['recording'] = threading.Event()
        try:
            assert c.delete('/api/stacks/'+stack['id']).status_code == 409
            assert output.exists()
        finally:
            core.cancellations.pop('recording')
        with core.db() as conn:
            conn.execute("UPDATE jobs SET status='running' WHERE id='recording'")
        assert c.delete('/api/stacks/'+stack['id']).status_code == 409
        with core.db() as conn:
            conn.execute("UPDATE jobs SET status='completed' WHERE id='recording'")
        assert c.delete('/api/stacks/'+stack['id']).status_code == 200
        assert all(not path.exists() for path in assets)
        assert c.get('/api/jobs/recording/audio').status_code == 404
        assert c.delete('/api/stacks/'+stack['id']).status_code == 404
        with core.db() as conn:
            assert not conn.execute('SELECT * FROM stack_positions WHERE stack_id=?',(stack['id'],)).fetchall()
    with TestClient(core.app) as c:
        assert [s['id'] for s in c.get('/api/stacks').json()] == [other['id']]
