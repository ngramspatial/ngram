"""Prepare media only when a user sends a message, never during upload or idle."""

import asyncio
import base64
import json
import shutil
import tempfile
from pathlib import Path

import aiohttp

from ngram.inference.factory import effective_inference_provider_name
from ngram.inference.openai_transport import OpenAIResponsesTransport
from ngram.storage.message_attachments import MessageAttachmentStore, prepare_attachments


def active_transport(entity):
    provider = getattr(entity, 'client', None)
    for _ in range(5):
        if hasattr(provider, 'chat_provider'):
            provider = provider.chat_provider
        elif hasattr(provider, 'provider'):
            provider = provider.provider
        else:
            return getattr(provider, '_t', None)
    return None


async def _command(*args: str) -> bytes:
    process = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(45):
            stdout, _ = await process.communicate()
        if process.returncode:
            raise ValueError('Media decoder could not read this file')
        return stdout
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def video_frames(path: Path) -> tuple[list[dict], str]:
    ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
    if not ffmpeg or not ffprobe:
        return [], 'Video preview unavailable: this worker needs ffmpeg; use execution tools with the original.'
    try:
        formats = 'mov,matroska,webm,avi,mpeg,mpegts,ogg,flv'
        info = json.loads(await _command(ffprobe, '-v', 'error', '-protocol_whitelist', 'file,pipe', '-format_whitelist', formats, '-show_entries', 'format=duration', '-of', 'json', str(path)))
        duration = float(info['format']['duration'])
        if not 0 < duration < 86400:
            raise ValueError('Unsupported duration')
        frames = []
        with tempfile.TemporaryDirectory(prefix='ngram-video-') as directory:
            for index, fraction in enumerate((0.1, 0.35, 0.65, 0.9)):
                output = Path(directory) / f'{index}.jpg'
                await _command(ffmpeg, '-v', 'error', '-nostdin', '-protocol_whitelist', 'file,pipe',
                               '-format_whitelist', formats,
                               '-ss', str(duration * fraction), '-i', str(path), '-frames:v', '1',
                               '-vf', 'scale=768:768:force_original_aspect_ratio=decrease', '-q:v', '4', str(output))
                if output.exists() and output.stat().st_size < 1024 * 1024:
                    frames.append({'base64': base64.b64encode(output.read_bytes()).decode('ascii'), 'mime': 'image/jpeg', 'storage_ref': str(path)})
        return frames, f'Video: {duration:.1f}s. {len(frames)} sampled frames included at 10%, 35%, 65%, 90%; these are still previews, not continuous video.'
    except (ValueError, KeyError, OSError, TimeoutError):
        return [], 'Video preview failed; inspect the original with execution tools.'


async def transcribe(entity, transport, path: Path, mime: str) -> str:
    if path.stat().st_size >= 25 * 1024 * 1024:
        return 'Audio transcription skipped: file exceeds 25 MB. Original is available to execution tools.'

    async def request_transcript():
        # Use the active provider's private credentials, including runtime overrides.
        session = await transport._session_get()
        with path.open('rb') as stream:
            form = aiohttp.FormData()
            form.add_field('model', 'whisper-1')
            form.add_field('file', stream, filename=path.name, content_type=mime)
            async with session.post(transport.api + '/audio/transcriptions', data=form) as response:
                if response.status != 200:
                    return 'Audio transcription unavailable from this provider; original retained for tools.'
                data = await response.json()
                text = str(data.get('text') or '')[:24000]
                return 'Audio transcript (speech only):\n' + text if text else 'No speech was transcribed.'

    control = getattr(entity, 'inference_control', None)
    try:
        return await control.run(request_transcript) if control else await request_transcript()
    except (aiohttp.ClientError, TimeoutError, ValueError):
        return 'Audio transcription failed; original retained for tools.'


async def prepare_message_attachments(entity, ids):
    transport = active_transport(entity)
    provider = effective_inference_provider_name(entity.config.harness)
    native_files = isinstance(transport, OpenAIResponsesTransport) or provider in {'openai', 'openrouter'}
    result = await asyncio.to_thread(prepare_attachments, MessageAttachmentStore.for_entity(entity), ids, native_files=native_files)
    for meta, path in result.pop('media'):
        if meta['mime'].startswith('video/'):
            frames, note = await video_frames(path)
            # Bound all inline image/document bytes across the complete message.
            used = sum(len(image['base64']) * 3 // 4 for image in result['images'])
            used += sum(len(file['file_data']) * 3 // 4 for file in result['files'])
            for frame in frames:
                size = len(frame['base64']) * 3 // 4
                if used + size > 45 * 1024 * 1024 or len(result['images']) >= 20:
                    note += ' Some frames omitted to fit the message; inspect the original for the rest.'
                    break
                result['images'].append(frame)
                used += size
            result['notes'].append(f'{meta["name"]}: {note}')
        if provider == 'openai' and transport and path.suffix.lower() in {'.mp3', '.mp4', '.mpeg', '.mpga', '.m4a', '.wav', '.webm', '.ogg', '.flac'}:
            result['notes'].append(f'{meta["name"]}: ' + await transcribe(entity, transport, path, meta['mime']))
        else:
            result['notes'].append('Audio is not included natively for this provider/format. Use execution tools to inspect or transcribe the original.')
    return result
