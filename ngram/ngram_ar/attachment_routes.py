"""Authenticated streaming uploads to the worker's persistent workspace."""

import asyncio
import hashlib
from urllib.parse import quote, unquote

from aiohttp import web

from ngram.storage.message_attachments import MAX_FILE_BYTES, MessageAttachmentStore


def register_attachment_routes(app, check_token):
    def store(request):
        return MessageAttachmentStore.for_entity(request.app['entity'])

    async def upload(request):
        if not check_token(request):
            raise web.HTTPForbidden()
        if request.headers.get('Content-Encoding', 'identity') != 'identity':
            raise web.HTTPUnsupportedMediaType(text='Compressed HTTP uploads are not supported')
        if request.content_length is not None and request.content_length > MAX_FILE_BYTES:
            raise web.HTTPRequestEntityTooLarge(max_size=MAX_FILE_BYTES, actual_size=request.content_length)
        name = unquote(request.headers.get('X-Ngram-Filename', 'attachment'))
        attachment_store = store(request)
        ident, name, temporary = attachment_store.begin(name)
        size, digest = 0, hashlib.sha256()
        committed = False
        try:
            with temporary.open('wb') as output:
                async with asyncio.timeout(300):
                    async for chunk in request.content.iter_chunked(256 * 1024):
                        size += len(chunk)
                        if size > MAX_FILE_BYTES:
                            raise web.HTTPRequestEntityTooLarge(max_size=MAX_FILE_BYTES, actual_size=size)
                        digest.update(chunk)
                        await asyncio.to_thread(output.write, chunk)
            metadata = attachment_store.commit(ident, name, size, digest.hexdigest())
            committed = True
            return web.json_response(metadata, status=201, headers={'Cache-Control': 'no-store'})
        finally:
            if not committed:
                # Only this upload's generated directory, never a caller-supplied path.
                for child in temporary.parent.iterdir():
                    if child.is_file() and not child.is_symlink():
                        child.unlink(missing_ok=True)
                temporary.parent.rmdir()

    async def download(request):
        if not check_token(request):
            raise web.HTTPForbidden()
        try:
            meta, path = store(request).resolve(request.match_info['attachment'])
        except ValueError:
            raise web.HTTPNotFound(text='Attachment not found') from None
        inline = meta['mime'] in {'image/png', 'image/jpeg', 'image/webp', 'image/gif'} or meta['mime'].startswith(('audio/', 'video/'))
        return web.FileResponse(path, headers={
            'Content-Type': meta['mime'],
            'Content-Disposition': f'{"inline" if inline else "attachment"}; filename="attachment"; filename*=UTF-8\'\'{quote(meta["name"], safe="")}',
            'X-Content-Type-Options': 'nosniff', 'Content-Security-Policy': "default-src 'none'; sandbox",
            'Cache-Control': 'private, max-age=86400',
        })

    app.router.add_post('/attachments', upload)
    app.router.add_get('/attachments/{attachment:[a-f0-9]{32}}', download)
