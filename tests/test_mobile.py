import hashlib
import json
import time
import uuid
import subprocess

import pytest
from io import BytesIO
from zipfile import ZipFile

import httpx
import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

from backend import app as core
from backend.audio import encode
from backend.mobile import manifest
from backend import mobile
from backend.narration import stack_parts


def seed_recording(client, tmp_path):
    article = client.post('/api/extract', json={'text': 'A quiet river.', 'title': 'River'}).json()
    jid = uuid.uuid4().hex
    audio = tmp_path / 'river.mp3'
    audio.write_bytes(b'ID3-fake-test-audio')
    with core.db() as conn:
        conn.execute('INSERT INTO jobs (id,article,voice,speed,status,created,output,generated_at) VALUES (?,?,?,?,?,?,?,?)',
                     (jid, json.dumps(article), 'bm_george', 1, 'completed', time.time(), str(audio), time.time()))
    return jid, audio


@pytest.mark.parametrize("oversized", [False, True])
def test_export_and_scoped_transfer(tmp_path, monkeypatch, oversized):
    for key, value in [('DATA', tmp_path), ('CACHE', tmp_path/'cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, key, value)
    class FakeEngine:
        def render(self, *args):
            raise RuntimeError('No synthesis in transfer test')
    monkeypatch.setattr(core, 'engine', FakeEngine())
    monkeypatch.setenv('ARTICLE_TRANSFER_PORT', '0')
    monkeypatch.setenv('ARTICLE_TRANSFER_HOST', '127.0.0.1')
    with TestClient(core.app) as client:
        jid, audio = seed_recording(client, tmp_path)
        if oversized:
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                'sine=frequency=440:duration=30', '-ar', '24000', '-ac', '1',
                '-b:a', '128k', str(audio)], check=True)
            monkeypatch.setattr(mobile, 'MAX_FILE_BYTES', 250_000)
        chapters = [dict(document=0, title='River', start=0, end=30)] if oversized else []
        if chapters:
            audio.with_suffix('.chapters.json').write_text(json.dumps(chapters))
        original = audio.read_bytes()
        exported = client.get(f'/api/jobs/{jid}/stack')
        assert exported.status_code == 200
        with ZipFile(BytesIO(exported.content)) as archive:
            assert set(archive.namelist()) == {'manifest.json', 'audio.mp3'}
            data = json.loads(archive.read('manifest.json'))
            assert data['audio']['sha256'] == hashlib.sha256(archive.read('audio.mp3')).hexdigest()
            exported_audio = archive.read('audio.mp3')
            assert data['audio']['bytes'] == len(exported_audio)
            assert len(exported.content) <= mobile.MAX_FILE_BYTES
            assert data['chapters'] == chapters  # Preserve timings; old files may have none.
            assert str(tmp_path) not in json.dumps(data)
        assert audio.read_bytes() == original
        if oversized:
            assert exported_audio != original
            prepared, copy = mobile.portable_recording(core, jid)
            stamp = copy.stat().st_mtime_ns
            assert mobile.portable_recording(core, jid)[1].stat().st_mtime_ns == stamp
            duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                'format=duration', '-of', 'csv=p=0', str(copy)]))
            assert abs(duration - 30) < .2
        response = client.post(f'/api/jobs/{jid}/send')
        assert response.status_code == 200, response.text
        sent = response.json()
        base = sent['url'].removesuffix('manifest')
        with httpx.Client(trust_env=False) as phone:
            assert phone.get(sent['url']).json() == data
            assert phone.get(base+'audio').content == exported_audio
            assert phone.get(base+'../../api/stacks').status_code == 404
            assert phone.get(base.replace(sent['token'], 'invalid')+'audio').status_code == 404
            assert client.get('/api/transfers/'+sent['token']).json()['downloadedAt'] is None
            assert phone.post(base+'ack').status_code == 200
            assert client.get('/api/transfers/'+sent['token']).json()['downloadedAt'] > 0
            with core.mobile_transfers.lock:
                core.mobile_transfers.sent[sent['token']]['expiresAt'] = 0
            assert phone.get(base+'audio').status_code == 404
            sent2 = client.post(f'/api/jobs/{jid}/send').json()
            client.delete('/api/transfers/'+sent2['token'])
            assert phone.get(sent2['url']).status_code == 404
        monkeypatch.setattr(mobile, 'MAX_FILE_BYTES', 100)
        assert client.get(f'/api/jobs/{jid}/stack').status_code == 413
        assert client.post(f'/api/jobs/{jid}/send').status_code == 413
        assert client.get('/api/config', headers={'Host': '192.168.1.4:8765'}).status_code == 403
    assert core.mobile_transfers.server is None


def test_chapters_use_actual_audio_durations(tmp_path):
    article = dict(title='Weekend', documents=[dict(title='River', date='', prepared='A quiet river.'), dict(title='Garden', date='', prepared='The garden sleeps.')])
    parts = stack_parts(article)
    paths = []
    for index, _ in enumerate(parts):
        path = tmp_path / f'{index}.wav'
        sf.write(path, np.zeros(24000), 24000)
        paths.append(path)
    output = tmp_path/'stack.mp3'
    encode(paths, output, article)
    chapters = json.loads(output.with_suffix('.chapters.json').read_text())
    assert [c['title'] for c in chapters] == ['Weekend', 'River', 'Garden']
    assert [c['start'] for c in chapters] == [0, 1, 3]
    assert chapters[-1]['end'] == len(parts)
    import subprocess
    duration = float(subprocess.check_output(['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(output)]))
    assert abs(duration - chapters[-1]['end']) < .15
