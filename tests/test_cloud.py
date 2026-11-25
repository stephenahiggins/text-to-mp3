import json
from pathlib import Path
import time
import uuid
import zipfile

from fastapi.testclient import TestClient
from fastapi import HTTPException
import pytest
from backend import app as core
from backend import cloud


@pytest.fixture
def connected(tmp_path, monkeypatch):
    for key, value in [('DATA', tmp_path/'data'), ('CACHE', tmp_path/'data/cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, key, value)
    root = tmp_path/'Reading Stacks'; root.mkdir()
    async def ingest(source):
        return dict(title='Article', original='A readable article.', prepared='A readable article.', url=source.url)
    monkeypatch.setattr(core, 'ingestion', ingest)
    with TestClient(core.app) as client:
        core.cloud_sync.close()  # Tick explicitly; keep the real ingestion worker running.
        core.cloud_sync.stopping.clear()
        response = client.post('/api/cloud', json={'path': str(root), 'autoAudio': False})
        assert response.status_code == 200, response.text
        workspace = json.loads((root/'reading-stacks.json').read_text())['workspaceId']
        yield client, root, workspace


def submit(root, workspace, payload, attempt=0):
    path = root/'requests'/(payload['id']+'.json')
    path.write_text(json.dumps(dict(schemaVersion=1, workspaceId=workspace, attempt=attempt, payload=payload)))
    core.cloud_sync.tick()
    return path


def await_receipt(root, ident, status):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        core.cloud_sync.tick()
        path = root/'receipts'/(ident+'.json')
        if path.exists():
            value = json.loads(path.read_text())
            if value['status'] == status:
                return value
        time.sleep(.05)
    raise AssertionError(f'Receipt did not become {status}: {value}')


def test_cloud_inbox_deduplicates_and_survives_restart(connected):
    client, root, workspace = connected
    ident = str(uuid.uuid4())
    payload = dict(id=ident, stackId='', newStackName='Away reading', title='Article', url='https://example.com/read')
    submit(root, workspace, payload)
    assert await_receipt(root, ident, 'complete')['attempt'] == 0
    for _ in range(3):
        core.cloud_sync.tick()
    stack, = client.get('/api/stacks').json()
    assert len(stack['items']) == 1
    assert stack['id'] == 'shared-' + ident
    catalog = json.loads((root/'catalog.json').read_text())
    assert catalog['stacks'] == [{'id': stack['id'], 'name': 'Away reading'}]
    replacement = cloud.CloudSync(core)
    replacement.tick()
    assert len(client.get('/api/stacks').json()[0]['items']) == 1
    assert replacement.status()['enabled']
    client.delete('/api/cloud')
    assert root.exists() and (root/'requests'/(ident+'.json')).exists()
    assert not client.get('/api/cloud').json()['enabled']


def test_cloud_error_needs_explicit_retry_and_ignores_old_attempt(connected, monkeypatch):
    client, root, workspace = connected
    calls = []
    async def ingest(source):
        calls.append(source.url)
        if len(calls) == 1:
            raise HTTPException(400, 'Website temporarily unavailable')
        return dict(title='Article', original='Readable.', prepared='Readable.', url=source.url)
    monkeypatch.setattr(core, 'ingestion', ingest)
    ident = str(uuid.uuid4())
    payload = dict(id=ident, stackId='', newStackName='Retry', url='https://example.com/read')
    submit(root, workspace, payload)
    await_receipt(root, ident, 'error')
    for _ in range(3): core.cloud_sync.tick()
    assert len(calls) == 1
    path = submit(root, workspace, dict(payload, retry=True), attempt=1)
    assert await_receipt(root, ident, 'complete')['attempt'] == 1
    submit(root, workspace, payload, attempt=0)  # A delayed old iCloud version cannot undo success.
    assert await_receipt(root, ident, 'complete')['attempt'] == 1
    assert len(calls) == 2
    assert len(client.get('/api/stacks').json()[0]['items']) == 1


def test_cloud_rejects_wrong_workspace_changed_payload_and_symlinks(connected, tmp_path):
    client, root, workspace = connected
    ident = str(uuid.uuid4())
    payload = dict(id=ident, stackId='', newStackName='Test', url='https://example.com/a')
    submit(root, 'wrong-workspace', payload)
    assert await_receipt(root, ident, 'error')['error']
    assert client.get('/api/stacks').json() == []
    submit(root, workspace, payload)
    await_receipt(root, ident, 'complete')
    submit(root, workspace, dict(payload, url='https://example.com/b', retry=True), attempt=1)
    assert 'different item' in await_receipt(root, ident, 'error')['error']
    outside = tmp_path/'outside'; outside.write_text('private')
    target = root/'requests'/(str(uuid.uuid4())+'.json'); target.symlink_to(outside)
    core.cloud_sync.tick()
    assert outside.read_text() == 'private'
    assert len(client.get('/api/stacks').json()[0]['items']) == 1
    marker = json.loads((root/'reading-stacks.json').read_text())
    marker['machineId'] = 'another-mac'
    (root/'reading-stacks.json').write_text(json.dumps(marker))
    assert client.post('/api/cloud', json={'path': str(root)}).status_code == 400
    with pytest.raises(ValueError, match='different Mac'):
        core.cloud_sync.tick()


def test_cloud_publishes_verified_portable_audio_once(connected, monkeypatch, tmp_path):
    client, root, workspace = connected
    ident = str(uuid.uuid4()); payload = dict(id=ident, stackId='', newStackName='Audio', url='https://example.com/a')
    submit(root, workspace, payload); await_receipt(root, ident, 'complete')
    audio = tmp_path/'audio.mp3'; audio.write_bytes(b'fake encoded audio')
    jid = uuid.uuid4().hex
    with core.db() as conn:
        conn.execute("INSERT INTO jobs(id,article,status,created,stack_id,stack_revision,output) VALUES (?,?,'completed',?,?,1,?)", (jid, '{}', time.time(), 'shared-'+ident, str(audio)))
    with core.db() as conn:
        conn.execute("INSERT INTO jobs(id,article,status,created,stack_id,stack_revision,output) VALUES (?,?,'completed',?,?,1,?)", (uuid.uuid4().hex, '{}', time.time()+1, 'shared-'+ident, str(tmp_path/'removed.mp3')))
    calls = []
    def portable(core, requested):
        calls.append(requested)
        return dict(id=requested, title='Audio', audio={'file': 'audio.mp3'}), audio
    monkeypatch.setattr(cloud, 'portable_recording', portable)
    core.cloud_sync.tick(); core.cloud_sync.tick()
    assert calls == [jid]
    entry, = json.loads((root/'catalog.json').read_text())['recordings']
    with zipfile.ZipFile(root/'recordings'/entry['file']) as archive:
        assert archive.read('audio.mp3') == audio.read_bytes()
        assert json.loads(archive.read('manifest.json'))['id'] == jid


def test_cloud_auto_audio_only_queues_missing_revision(connected, monkeypatch):
    client, root, workspace = connected
    queued = []
    def generate(sid, revision):
        queued.append((sid, revision))
        with core.db() as conn:
            conn.execute("INSERT INTO jobs(id,article,status,created,stack_id,stack_revision) VALUES (?,?,'failed',?,?,?)", (uuid.uuid4().hex, '{}', time.time(), sid, revision))
    monkeypatch.setattr(core, 'create_stack_audio', generate)
    client.post('/api/cloud', json={'path': str(root), 'autoAudio': True})
    ident = str(uuid.uuid4())
    submit(root, workspace, dict(id=ident, stackId='', newStackName='Audio', url='https://example.com/a'))
    await_receipt(root, ident, 'complete')
    core.cloud_sync.tick()
    assert len(queued) == 1


def test_cloud_setup_rejects_nonempty_folder_and_remote_requests(connected, tmp_path):
    client, _, _ = connected
    other = tmp_path/'personal'; other.mkdir(); (other/'private.txt').write_text('Keep me')
    assert client.post('/api/cloud', json={'path': str(other)}).status_code == 400
    assert not (other/'reading-stacks.json').exists()
    assert client.post('/api/cloud/choose', headers={'Origin': 'https://evil.example'}).status_code == 403
