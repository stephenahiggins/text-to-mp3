import json
import httpx
from fastapi.testclient import TestClient
from backend import app as core


def test_share_pairing_append_retry_and_revoke(tmp_path, monkeypatch):
    for key, value in [('DATA', tmp_path), ('CACHE', tmp_path/'cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, key, value)
    monkeypatch.setenv('ARTICLE_SHARE_PORT', '0')
    monkeypatch.setenv('ARTICLE_TRANSFER_HOST', '127.0.0.1')
    async def extract(source):
        # Simulate another edit while extraction is in progress.
        with core.db() as conn:
            conn.execute("UPDATE stacks SET name='Changed elsewhere' WHERE id=?", (sid,))
        return dict(title='Article', original='Readable article.', prepared='Readable article.', url=source.url)
    monkeypatch.setattr(core, 'ingestion', extract)
    with TestClient(core.app) as client:
        sid = client.post('/api/stacks', json={'name': 'Reading'}).json()['id']
        assert client.get('/share-setup', follow_redirects=False).headers['location'] == '/#/iphone-sharing'
        assert client.get('/api/share/connect').json() == {'enabled': False}
        assert client.post('/api/share/connect', headers={'Host': '192.168.1.2'}).status_code == 403
        link = client.post('/api/share/connect').json()['connectionLink']
        base, token = link.split('#')
        assert client.get('/api/share/connect').json() == {'enabled': True}
        with httpx.Client(trust_env=False) as phone:
            assert phone.get(base+'/stacks').status_code == 401
            phone.headers['Authorization'] = 'Bearer '+token
            assert phone.get(base+'/stacks').json() == [{'id': sid, 'name': 'Reading'}]
            assert phone.get(base+'/api/jobs').status_code == 404
            body = {'stackId': sid, 'url': 'https://example.com/article'}
            assert phone.post(base+'/items', json={**body, 'url': ''}).status_code == 400
            assert phone.post(base+'/items', json={**body, 'stackId': 'missing'}).status_code == 404
            assert phone.post(base+'/items', json=body).status_code == 200
            assert phone.post(base+'/items', json=body).status_code == 200
            stack = client.get('/api/stacks').json()[0]
            assert stack['name'] == 'Changed elsewhere'
            assert len(stack['items']) == 1
            assert stack['revision'] == 2
            assert stack['items'][0]['url'] == body['url']
            client.delete('/api/share/connect')
            assert client.get('/api/share/connect').json() == {'enabled': False}
            assert phone.get(base+'/stacks').status_code == 401
            link2 = client.post('/api/share/connect').json()['connectionLink']
            assert link2 != link
            assert phone.post(base+'/items', json=body).status_code == 401
    assert core.mobile_sharing.server is None


def test_follow_up_can_create_and_reuse_pending_stack(tmp_path, monkeypatch):
    import time
    import uuid

    for key, value in [('DATA', tmp_path), ('CACHE', tmp_path/'cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, key, value)
    monkeypatch.setenv('ARTICLE_SHARE_PORT', '0')
    monkeypatch.setenv('ARTICLE_TRANSFER_HOST', '127.0.0.1')

    async def extract(source):
        return dict(title='Article', original='Readable article.', prepared='Readable article.', url=source.url)

    monkeypatch.setattr(core, 'ingestion', extract)
    creator = str(uuid.uuid4())
    stack_id = 'shared-' + creator
    draft = {'stackId': '', 'newStackName': 'Weekend reading'}
    with TestClient(core.app) as client:
        base, token = client.post('/api/share/connect').json()['connectionLink'].split('#')
        with httpx.Client(trust_env=False, headers={'Authorization': 'Bearer ' + token}) as phone:
            follow_up = dict(draft, id=str(uuid.uuid4()), newStackId=stack_id, url='https://example.com/next')
            first = dict(draft, id=creator, url='https://example.com/first')
            assert phone.post(base+'/inbox', json=dict(follow_up, newStackId='invalid')).status_code == 400
            for article in (follow_up, first, follow_up):
                assert phone.post(base+'/inbox', json=article).status_code == 202
            deadline = time.monotonic() + 5
            while True:
                receipts = [phone.get(base+'/inbox/'+article['id']).json() for article in (first, follow_up)]
                if all(receipt['status'] == 'complete' for receipt in receipts):
                    break
                assert all(receipt['status'] != 'error' for receipt in receipts), receipts
                assert time.monotonic() < deadline, receipts
                time.sleep(.05)
            assert phone.get(base+'/stacks').json() == [{'id': stack_id, 'name': 'Weekend reading'}]
            stack, = client.get('/api/stacks').json()
            assert {item['url'] for item in stack['items']} == {first['url'], follow_up['url']}


def test_discovery_pairing_requires_local_approval(tmp_path, monkeypatch):
    for key, value in [('DATA', tmp_path), ('CACHE', tmp_path/'cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, key, value)
    with TestClient(core.app) as client:
        base = f'http://127.0.0.1:{core.mobile_sharing.server.server_port}'
        with httpx.Client(trust_env=False) as phone:
            payload = {'secret': 'a'*64, 'name': 'My iPhone'}
            assert phone.get(base+'/stacks').status_code == 401
            assert phone.post(base+'/pair', json=payload, headers={'Origin': 'https://example.com'}).status_code == 403
            assert phone.post(base+'/pair', json={**payload, 'secret': 'short'}).status_code == 400
            pending = phone.post(base+'/pair', json=payload).json()
            assert pending['status'] == 'pending' and 'token' not in pending
            assert phone.post(base+'/pair', json=payload).json() == pending
            request, = client.get('/api/share/requests').json()
            assert request['code'] == pending['code']
            route = '/api/share/requests/'+request['id']+'/approve'
            assert client.post(route, headers={'Host': '192.168.1.2'}).status_code == 403
            assert phone.post(base+route).status_code == 401
            assert client.post(route).status_code == 200
            approved = phone.post(base+'/pair', json=payload).json()
            assert approved['status'] == 'approved'
            token = approved['token']
            assert phone.get(base+'/stacks', headers={'Authorization': 'Bearer '+token}).status_code == 200
            stranger = phone.post(base+'/pair', json={**payload, 'secret': 'b'*64}).json()
            assert stranger['status'] == 'pending' and 'token' not in stranger
            request, = client.get('/api/share/requests').json()
            assert client.post('/api/share/requests/'+request['id']+'/reject').status_code == 200
            assert phone.post(base+'/pair', json={**payload, 'secret': 'b'*64}).json()['status'] == 'rejected'
            client.delete('/api/share/connect')
            assert phone.get(base+'/stacks', headers={'Authorization': 'Bearer '+token}).status_code == 401
            assert phone.post(base+'/pair', json=payload).json()['status'] == 'pending'
    with TestClient(core.app) as client:
        assert core.mobile_sharing.server is not None
        assert client.get('/api/share/requests').json() == []


def test_pairing_expiry_and_capacity(monkeypatch):
    from backend import discovery
    pairing = discovery.Pairing()
    monkeypatch.setattr(discovery.time, 'monotonic', lambda: 10)
    first = pairing.request('a'*64, 'iPhone')
    request, = pairing.pending()
    for number in range(19):
        pairing.request(f'{number:064x}', 'iPhone')
    import pytest
    with pytest.raises(ValueError, match='Too many'):
        pairing.request('b'*64, 'iPhone')
    monkeypatch.setattr(discovery.time, 'monotonic', lambda: 311)
    assert pairing.pending() == []
    with pytest.raises(ValueError, match='expired'):
        pairing.decide(request['id'], 'token')
    assert pairing.request('a'*64, 'iPhone')['status'] == first['status']
