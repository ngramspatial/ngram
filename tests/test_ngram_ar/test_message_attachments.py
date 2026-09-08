import base64
import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from ngram.cognition import gemma
from ngram.cognition.history_compression import estimate_message_tokens
from ngram.cognition.senses import input_to_message_content
from ngram.entity import Entity
from ngram.inference.openai_transport import OpenAICompatibleTransport, OpenAIResponsesTransport
from ngram.ngram_ar.attachment_routes import register_attachment_routes
from ngram.ngram_ar.bridge_server import _handle_shell_event
from ngram.ngram_ar.message_media import prepare_message_attachments
from ngram.storage.attachment_ingestion import persist_incoming_attachments
from ngram.storage.message_attachments import MessageAttachmentStore, prepare_attachments


def entity_at(root, name='agent'):
    return SimpleNamespace(config=SimpleNamespace(name=name, execution_workspace_dir=lambda: str(root),
        harness=SimpleNamespace(inference=SimpleNamespace(provider='openai'))), current_platform=None)


def put(store, name, data):
    ident, name, temporary = store.begin(name)
    temporary.write_bytes(data)
    return store.commit(ident, name, len(data), hashlib.sha256(data).hexdigest())


PNG = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aVe8AAAAASUVORK5CYII=')


@pytest.mark.asyncio
async def test_upload_auth_streaming_download_range_and_restart(tmp_path, monkeypatch):
    app = web.Application()
    app['entity'] = entity_at(tmp_path)
    register_attachment_routes(app, lambda req: req.headers.get('Authorization') == 'Bearer test-only')
    async with TestClient(TestServer(app)) as client:
        assert (await client.post('/attachments', data=PNG)).status == 403
        headers = {'Authorization': 'Bearer test-only', 'X-Ngram-Filename': '..%2F..%2Fpicture.png', 'Content-Type': 'text/html'}
        response = await client.post('/attachments', headers=headers, data=PNG)
        assert response.status == 201
        meta = await response.json()
        assert meta['name'] == 'picture.png' and meta['mime'] == 'image/png'
        assert not any(key in meta for key in ('path', 'base64', 'file_data'))
        assert meta['sha256'] == hashlib.sha256(PNG).hexdigest()
        assert (await client.get('/attachments/' + meta['id'])).status == 403
        result = await client.get('/attachments/' + meta['id'], headers={**headers, 'Range': 'bytes=0-7'})
        assert result.status == 206 and await result.read() == PNG[:8]
        assert result.headers['X-Content-Type-Options'] == 'nosniff'
        assert (await client.get('/attachments/' + '0' * 32, headers=headers)).status == 404
        fresh = MessageAttachmentStore.for_entity(entity_at(tmp_path))
        assert fresh.resolve(meta['id'])[1].read_bytes() == PNG
        with pytest.raises(ValueError):
            MessageAttachmentStore.for_entity(entity_at(tmp_path, 'different')).resolve(meta['id'])
        monkeypatch.setattr('ngram.ngram_ar.attachment_routes.MAX_FILE_BYTES', 5)

        async def chunks():
            yield b'123'
            yield b'456'

        assert (await client.post('/attachments', headers=headers, data=chunks())).status == 413
        assert not list(fresh.root.rglob('upload.part'))


def test_safe_names_duplicates_and_html_download(tmp_path):
    store = MessageAttachmentStore(tmp_path)
    for name in ('metadata.json', 'upload.part', 'CON', '../a.txt', 'a\\b.txt', 'a\r\nb.txt'):
        first = put(store, name, b'hello')
        second = put(store, name, b'world')
        assert first['id'] != second['id']
        assert store.resolve(first['id'])[1].read_bytes() == b'hello'
        assert store.resolve(second['id'])[1].read_bytes() == b'world'
    for ident in ('../outside', '/etc/passwd', 'http://test', {'path': 'anything'}):
        with pytest.raises(ValueError):
            store.resolve(ident)


def test_zip_preview_is_bounded_and_never_extracts(tmp_path):
    store = MessageAttachmentStore(tmp_path)
    source = io.BytesIO()
    with zipfile.ZipFile(source, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('readme.md', 'Project says hello')
        archive.writestr('../escape.txt', 'must not read me')
        archive.writestr('compressed.txt', 'x' * 500000)
        for index in range(205):
            archive.writestr(f'file-{index}.bin', b'')
    meta = put(store, 'project.zip', source.getvalue())
    result = prepare_attachments(store, [meta['id']])
    note = result['notes'][0]
    assert 'Project says hello' in note and 'unsafe path; skipped' in note
    assert 'must not read me' not in note and '[Index truncated' in note
    assert len(note) < 18000 and not (tmp_path / 'escape.txt').exists()
    assert not list(tmp_path.rglob('readme.md'))


@pytest.mark.asyncio
async def test_one_message_carries_all_images_native_pdf_text_zip_and_refs(tmp_path):
    entity = entity_at(tmp_path)
    store = MessageAttachmentStore.for_entity(entity)
    ids = [put(store, f'{i}.png', PNG)['id'] for i in range(4)]
    ids += [put(store, 'brief.pdf', b'%PDF-1.7 test document')['id'], put(store, 'notes.txt', b'green triangles')['id']]
    received = []

    async def perceive(inp, **kwargs):
        received.append(inp)
        return '', False

    entity.perceive = perceive
    await _handle_shell_event(entity, 'event:user_speech', {'text': 'Compare these', 'attachments': ids, 'isFinal': True}, 'test', 'agent', 'Agent')
    assert len(received) == 1
    inp = received[0]
    assert inp.text.startswith('Compare these') and 'green triangles' in inp.text
    assert len(inp.images) == 4 and len(inp.files) == 1
    assert all(base64.b64decode(image['base64']) == PNG for image in inp.images)
    assert len(inp.metadata['attachment_storage_refs']) == 6
    persisted = await persist_incoming_attachments(None, inp)  # Originals already stored, no second upload.
    assert persisted.metadata['attachment_storage_refs'] == inp.metadata['attachment_storage_refs']
    content = input_to_message_content(inp)
    assert len([block for block in content if block['type'] == 'image_url']) == 4
    responses = OpenAIResponsesTransport()._message_content(content)
    assert responses[-1]['type'] == 'input_file' and responses[-1]['file_data'].startswith('data:application/pdf;base64,')
    payload = OpenAICompatibleTransport(gemma_shaping=False)._build_chat_completions_payload('test', [{'role': 'user', 'content': content}])
    assert payload['messages'][0]['content'][-1]['file']['filename'] == 'brief.pdf'
    assert 'base64' not in gemma.stringify_content_blocks(content)
    assert estimate_message_tokens({'content': content}) < 20000
    entity.config.cognition = SimpleNamespace(history_message_char_limit=4000)
    receipt = Entity._history_user_turn_text(entity, inp)
    assert 'brief.pdf' in receipt and 'Compare these' in receipt and 'base64' not in receipt


@pytest.mark.asyncio
async def test_attachment_only_invalid_id_and_provider_fallback(tmp_path):
    entity = entity_at(tmp_path)
    store = MessageAttachmentStore.for_entity(entity)
    ident = put(store, 'brief.pdf', b'%PDF-1.7')['id']
    received = []

    async def perceive(inp, **kwargs):
        received.append(inp)
        return '', False

    entity.perceive = perceive
    await _handle_shell_event(entity, 'event:user_speech', {'attachments': [ident]}, 'test', 'agent', 'Agent')
    assert len(received) == 1 and received[0].files
    actions = await _handle_shell_event(entity, 'event:user_speech', {'text': 'read', 'attachments': ['0' * 32]}, 'test', 'agent', 'Agent')
    assert len(received) == 1 and actions[0]['type'] == 'action:error'
    entity.config.harness.inference.provider = 'local'
    result = await prepare_message_attachments(entity, [ident])
    assert not result['files'] and 'not included' in result['notes'][0]
    with pytest.raises(ValueError):
        prepare_attachments(store, [ident] * 2)
    with pytest.raises(ValueError):
        OpenAIResponsesTransport()._message_content([{'type': 'input_audio', 'input_audio': {'data': 'secret-base64'}}])


@pytest.mark.asyncio
async def test_audio_transcribed_once_at_send_with_active_provider_credentials(tmp_path):
    calls = []

    async def transcribe(request):
        data = await request.post()
        calls.append((request.headers.get('Authorization'), data['model'], data['file'].file.read()))
        return web.json_response({'text': 'Meet by the blue cube'})

    app = web.Application()
    app.router.add_post('/v1/audio/transcriptions', transcribe)
    async with TestClient(TestServer(app)) as client:
        entity = entity_at(tmp_path)
        transport = OpenAIResponsesTransport(str(client.make_url('')).rstrip('/'), extra_headers={'Authorization': 'Bearer runtime-test'})
        entity.client = SimpleNamespace(_t=transport)
        meta = put(MessageAttachmentStore.for_entity(entity), 'memo.wav', b'RIFF-test-audio')
        assert calls == []
        result = await prepare_message_attachments(entity, [meta['id']])
        await transport.close()
        assert calls == [('Bearer runtime-test', 'whisper-1', b'RIFF-test-audio')]
        assert 'Meet by the blue cube' in '\n'.join(result['notes'])
        assert not result['files'] and not result['images']
        assert 'base64' not in json.dumps(result)
