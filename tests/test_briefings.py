import json
import sqlite3
import pytest
import httpx
from backend import briefings as b
from backend.narration import stack_parts


@pytest.fixture
def database(tmp_path):
    path = tmp_path/'test.db'
    def db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn
    with db() as conn:
        conn.execute('CREATE TABLE briefings (cache_key TEXT PRIMARY KEY, summary TEXT, model TEXT, created REAL)')
    return db


def test_request_privacy_validation_and_errors(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'secret-test-key')
    monkeypatch.setenv('OPENAI_SUMMARY_MODEL', 'gpt-6-luna')
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json={'status':'completed', 'output':[{'type':'message','content':[{'type':'output_text','text':'The author explores gardens. They explain how plants grow.'}]}]})
    monkeypatch.setattr(b.httpx, 'post', post)
    assert b.request_summary('Source text').startswith('The author')
    assert calls[0]['json']['model'] == 'gpt-6-luna'
    assert calls[0]['json']['reasoning'] == {'effort':'none'}
    assert calls[0]['json']['store'] is False
    assert calls[0]['json']['input'] == 'Source text'
    monkeypatch.setattr(b.httpx, 'post', lambda *a, **k: httpx.Response(401, text='secret-test-key'))
    with pytest.raises(b.BriefingError) as exc:
        b.request_summary('Source')
    assert 'secret-test-key' not in str(exc.value)
    monkeypatch.delenv('OPENAI_API_KEY')
    with pytest.raises(b.BriefingError, match='OPENAI_API_KEY'):
        b.request_summary('Source')


def test_full_source_cache_and_invalidation(database, monkeypatch):
    calls=[]
    def summary(source, check=lambda:None):
        calls.append(source)
        return 'A concise summary.'
    monkeypatch.setattr(b,'request_summary',summary)
    doc={'prepared':'x'*(b.SECTION_CHARS+100)}
    b.summarise(doc,database)
    assert ''.join(calls[:2])==doc['prepared']
    assert len(calls)==3
    b.summarise(doc,database)
    assert len(calls)==3
    b.summarise(dict(doc,briefing_nonce=1),database)
    assert len(calls)==6
    assert b.summarise(dict(doc,briefing_override='My summary.'),database)=='My summary.'
    assert len(calls)==6


def test_snapshot_progress_and_spoken_order(database, monkeypatch):
    monkeypatch.setattr(b,'request_summary',lambda *args:'An overview of rivers. It describes their wildlife.')
    article={'title':'Weekend','include_briefings':True,'documents':[{'title':'River','prepared':'The full article.', 'date':'2026-11-01','content_type':'article','author':'Jane','added_at':1793491200}]}
    saved=[]
    b.prepare_job(article,database,lambda i,a=None: saved.append(json.dumps(a)) if a else None,lambda:None)
    assert json.loads(saved[-1])['documents'][0]['briefing_summary']
    parts=stack_parts(article)
    text=' '.join(p['text'] for p in parts)
    assert text.index('Weekend') < text.index('River') < text.index('An overview') < text.index('The full article')
    assert 'an article by Jane' in text
    assert 'Published on the first of November' in text
    assert 'You added it to this stack' in text
    with pytest.raises(b.BriefingCancelled):
        b.prepare_job(article,database,lambda *a:None,lambda:(_ for _ in ()).throw(b.BriefingCancelled()))


def test_retry_is_bounded(monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY','fake')
    calls=[]
    monkeypatch.setattr(b.httpx,'post',lambda *a,**k: calls.append(1) or httpx.Response(429))
    monkeypatch.setattr(b.time,'sleep',lambda _:None)
    with pytest.raises(b.BriefingError,match='Rate Limited'):
        b.request_summary('Source')
    assert len(calls)==3


def test_added_date_and_failed_briefing_fallback(tmp_path, monkeypatch):
    import time
    from fastapi.testclient import TestClient
    from backend import app as core
    for key, value in [('DATA',tmp_path),('CACHE',tmp_path/'cache'),('OUTPUT',tmp_path/'output')]:
        monkeypatch.setattr(core,key,value)
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    class Engine:
        def render(self,*args):
            return tmp_path/'dummy.wav'
    monkeypatch.setattr(core,'engine',Engine())
    monkeypatch.setattr(core,'encode',lambda paths,output,article: (output.parent.mkdir(exist_ok=True),output.write_bytes(b'mp3')))
    with TestClient(core.app) as c:
        s=c.post('/api/stacks',json={'name':'Reading'}).json()
        doc=c.post('/api/extract',json={'title':'River','text':'A river flows.'}).json()
        def save(s,items):
            return c.put('/api/stacks/'+s['id'],json={'name':s['name'],'revision':s['revision'],'items':items}).json()
        s=save(s,[dict(doc,id='river',added_at=1)])
        added=s['items'][0]['added_at']
        assert added>1
        s=save(s,[dict(s['items'][0],added_at=1,briefing_override='Manual summary.')])
        assert s['items'][0]['added_at']==added
        s=save(s,[dict(s['items'][0],prepared='Changed content.')])
        assert s['items'][0]['briefing_override'] is None
        job=c.post('/api/stacks/'+s['id']+'/create',json={'revision':s['revision'],'include_briefings':True}).json()
        def wait(status):
            for _ in range(150):
                result=core.get(job['id'])
                if result['status']==status:return result
                time.sleep(.02)
            pytest.fail(str(result))
        failed=wait('failed')
        assert failed['stage']=='briefing'
        assert c.post('/api/jobs/'+job['id']+'/retry?skip_briefings=true').status_code==200
        finished=wait('completed')
        assert json.loads(finished['article'])['include_briefings'] is False


def test_briefing_only_prepared_even_when_global_briefings_off(database, monkeypatch):
    calls = []
    monkeypatch.setattr(b, 'request_summary', lambda source, *args: calls.append(source) or 'Short summary.')
    article = {'include_briefings':False, 'documents':[
        {'prepared':'Read this in full.', 'narration_mode':'whole'},
        {'prepared':'Summarise this.', 'narration_mode':'briefing'},
    ]}
    b.prepare_job(article, database, lambda *args: None, lambda: None)
    assert calls == ['Summarise this.']
    assert article['documents'][1]['briefing_summary'] == 'Short summary.'
