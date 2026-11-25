import asyncio
import base64
import json
import time
import uuid
from io import BytesIO

import httpx
import pytest
from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
from backend import app as core
from backend.pdf import pdf_article


@pytest.fixture(autouse=True)
def isolated_backend(tmp_path, monkeypatch):
    for key, value in [('DATA', tmp_path), ('CACHE', tmp_path/'cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, key, value)
    monkeypatch.setenv('ARTICLE_SHARE_PORT', '0')
    monkeypatch.setenv('ARTICLE_TRANSFER_HOST', '127.0.0.1')


def pdf_bytes(text='A quiet river flows.'):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(f'BT /F1 12 Tf 72 720 Td ({text}) Tj ET'.encode())
    page[NameObject('/Contents')] = writer._add_object(stream)
    writer.add_metadata({'/Title': 'River paper', '/Author': 'A Writer'})
    output = BytesIO(); writer.write(output)
    return output.getvalue()


def test_pdf_metadata_upload_and_extensionless_url(monkeypatch):
    pdf = pdf_bytes()
    article = pdf_article(pdf, url='https://example.com/pdf/123')
    assert article['title'] == 'River paper'
    assert article['author'] == 'A Writer'
    assert 'quiet river' in article['original']
    async def fetch(url):
        return pdf, 'application/octet-stream', 'utf-8'
    monkeypatch.setattr(core, 'fetch_url', fetch)
    result = asyncio.run(core.ingestion(core.Source(url='https://example.com/pdf/123')))
    assert result['url'] == 'https://example.com/pdf/123'
    assert result['content_type'] == 'PDF'
    with TestClient(core.app) as client:
        response = client.post('/api/import', files={'file': ('paper.pdf', pdf, 'application/pdf')})
        assert response.status_code == 200
        assert response.json()['title'] == 'River paper'


def test_scanned_encrypted_and_malformed_pdf():
    writer = PdfWriter(); writer.add_blank_page(width=612, height=792)
    stream = BytesIO(); writer.write(stream)
    with pytest.raises(ValueError, match='selectable text'):
        pdf_article(stream.getvalue())
    writer.encrypt('password'); stream = BytesIO(); writer.write(stream)
    with pytest.raises(ValueError, match='Encrypted'):
        pdf_article(stream.getvalue())
    with pytest.raises(ValueError, match='not a PDF'):
        pdf_article(b'not a document')


def wait_receipt(phone, base, ident):
    for _ in range(200):
        result = phone.get(base + '/inbox/' + ident).json()
        if result['status'] in ('complete', 'error'):
            return result
        time.sleep(.02)
    pytest.fail('Inbox worker did not finish')


def test_queue_persistence_pdf_retry_and_pairing(tmp_path, monkeypatch):
    for key, value in [('DATA', tmp_path), ('CACHE', tmp_path/'cache'), ('OUTPUT', tmp_path/'output')]:
        monkeypatch.setattr(core, key, value)
    monkeypatch.setenv('ARTICLE_SHARE_PORT', '0')
    monkeypatch.setenv('ARTICLE_TRANSFER_HOST', '127.0.0.1')
    with TestClient(core.app) as client:
        sid = client.post('/api/stacks', json={'name': 'Papers'}).json()['id']
        base, token = client.post('/api/share/connect').json()['connectionLink'].split('#')
        with httpx.Client(headers={'Authorization': 'Bearer '+token}, trust_env=False) as phone:
            ident = str(uuid.uuid4())
            body = dict(id=ident, stackId=sid, url='', title='River', pdf=base64.b64encode(pdf_bytes()).decode())
            assert phone.post(base+'/inbox', json=body).status_code == 202
            assert wait_receipt(phone, base, ident)['status'] == 'complete'
            assert phone.post(base+'/inbox', json=body).json()['status'] == 'complete'
            assert phone.post(base+'/inbox', json={**body, 'title':'Changed'}).status_code == 409
            stack = client.get('/api/stacks').json()[0]
            assert len(stack['items']) == 1
            assert stack['items'][0]['source_hash']
            assert client.put('/api/stacks/'+sid, json={'name':'Edited', 'revision':stack['revision'], 'items':stack['items']}).status_code == 200
            duplicate = dict(body, id=str(uuid.uuid4()))
            assert phone.post(base+'/inbox', json=duplicate).status_code == 202
            assert wait_receipt(phone, base, duplicate['id'])['status'] == 'complete'
            assert len(client.get('/api/stacks').json()[0]['items']) == 1
            bad = dict(body, id=str(uuid.uuid4()), stackId='missing')
            assert phone.post(base+'/inbox', json=bad).status_code == 202
            assert wait_receipt(phone, base, bad['id'])['status'] == 'error'
            assert phone.post(base+'/inbox', json={**bad, 'retry': True}).json()['status'] == 'queued'
            assert wait_receipt(phone, base, bad['id'])['status'] == 'error'
            # Simulate a crash after acceptance, before extraction.
            with core.db() as conn:
                pending = str(uuid.uuid4())
                conn.execute("INSERT INTO share_inbox VALUES (?,?,?,'processing','',?)", (pending, 'test', json.dumps(body), time.time()))
    with TestClient(core.app) as client:
        assert client.get('/api/share/connect').json()['enabled'] is True
        base = f'http://127.0.0.1:{core.mobile_sharing.server.server_port}'
        with httpx.Client(headers={'Authorization':'Bearer '+token}, trust_env=False) as phone:
            assert phone.get(base+'/stacks').status_code == 200
            assert wait_receipt(phone, base, pending)['status'] == 'complete'
            assert len(client.get('/api/stacks').json()[0]['items']) == 1
            client.delete('/api/share/connect')
            assert phone.get(base+'/stacks').status_code == 401
    with TestClient(core.app) as client:
        assert not client.get('/api/share/connect').json()['enabled']


def test_new_stack_is_created_once_with_article(monkeypatch):
    async def extract(source):
        return dict(title='Article', original='Readable text.', prepared='Readable text.', url=source.url)
    monkeypatch.setattr(core, 'ingestion', extract)
    with TestClient(core.app) as client:
        base, token = client.post('/api/share/connect').json()['connectionLink'].split('#')
        with httpx.Client(headers={'Authorization': 'Bearer '+token}, trust_env=False) as phone:
            body = dict(id=str(uuid.uuid4()), stackId='', newStackName='Morning reading', url='https://example.com/read')
            assert phone.post(base+'/inbox', json={**body, 'newStackName':' '}).status_code == 400
            assert phone.post(base+'/inbox', json=body).status_code == 202
            assert wait_receipt(phone, base, body['id'])['status'] == 'complete'
            assert phone.post(base+'/inbox', json=body).json()['status'] == 'complete'
            stacks = client.get('/api/stacks').json()
            assert len(stacks) == 1
            assert stacks[0]['name'] == 'Morning reading'
            assert len(stacks[0]['items']) == 1
