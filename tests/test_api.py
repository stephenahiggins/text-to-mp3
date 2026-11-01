import threading
import time
from fastapi.testclient import TestClient
from backend import app as module
from backend.text import chunks


def test_job_recovery_cancellation_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(module,'DATA',tmp_path)
    monkeypatch.setattr(module,'CACHE',tmp_path/'cache')
    monkeypatch.setattr(module,'OUTPUT',tmp_path/'output')
    entered=threading.Event()
    release=threading.Event()
    calls=[]
    class FakeEngine:
        def render(self,part,voice,speed,cache):
            calls.append(part['text'])
            entered.set()
            release.wait(3)
            return cache/'mock.wav'
    monkeypatch.setattr(module,'engine',FakeEngine())
    def encode(paths,output,article):
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b'test')
    monkeypatch.setattr(module,'encode',encode)
    with TestClient(module.app) as client:
        assert client.get('/api/config',headers={'Host':'evil.test'}).status_code==403
        assert client.post('/api/extract',json={'text':'hello'},headers={'Origin':'https://evil.test'}).status_code==403
        article=client.post('/api/extract',json={'text':'A quiet morning.\n\nThe gate opens.','title':'Test'}).json()
        response=client.post('/api/jobs',json={'article':article})
        assert response.status_code==200
        job_id=response.json()['id']
        assert entered.wait(3)
        client.post(f'/api/jobs/{job_id}/cancel')
        assert module.get(job_id)['status']=='cancelled'
        # Resume while the old chunk is still in flight must not let that run continue.
        assert client.post(f'/api/jobs/{job_id}/retry').status_code==200
        release.set()
        for _ in range(100):
            if module.get(job_id)['status']=='completed':break
            time.sleep(.02)
        assert module.get(job_id)['status']=='completed'
        assert len(calls)==3
        assert client.get(f'/api/jobs/{job_id}/audio').status_code==200
        assert client.post(f'/api/jobs/{job_id}/retry').status_code==409
        assert client.post('/api/import',files={'file':('story.md',b'# Story\n\nThe river flows.','text/markdown')}).json()['title']=='Story'
        assert client.post('/api/import',files={'file':('x.exe',b'no','application/octet-stream')}).status_code==400
        module.update(job_id,status='running')
    with TestClient(module.app):
        for _ in range(100):
            if module.get(job_id)['status']=='completed':break
            time.sleep(.02)
        assert module.get(job_id)['status']=='completed'
