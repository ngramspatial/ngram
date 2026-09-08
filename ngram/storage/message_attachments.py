"""Message attachments on the agent's own disk; opaque IDs at the browser boundary."""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import re
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_MESSAGE_FILES = 10
MAX_INLINE_BYTES = 45 * 1024 * 1024
TEXT_EXTENSIONS = set(('.txt .md .markdown .csv .tsv .json .jsonl .xml .yaml .yml .toml '
                       '.ini .log .html .htm .css .scss .js .jsx .ts .tsx .py .ipynb '
                       '.c .h .cpp .cs .java .go .rs .rb .php .sh .sql .srt .vtt .obj .mtl').split())
DOCUMENT_EXTENSIONS = set(('.pdf .doc .docx .dot .odt .rtf .pages .ppt .pptx .pot .ppa .pps .pwz .wiz '
                           '.key .xla .xlb .xlc .xlm .xls .xlsx .xlt .xlw .csv .tsv .iif').split())


def safe_name(name: str) -> str:
    name = str(name).replace('\\', '/').split('/')[-1]
    name = re.sub(r'[\x00-\x1f\x7f<>:"|?*]', '_', name).strip(' .')[:160]
    if name.lower() in {'metadata.json', 'metadata.json.tmp', 'upload.part'}:
        name = 'attachment-' + name
    if not name or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(10)), *(f'LPT{i}' for i in range(10))}:
        name = 'attachment' + Path(name).suffix
    return name


def detect_mime(name: str, head: bytes) -> str:
    # Never trust a supplied Content-Type for content served back to a browser.
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head[:6] in (b'GIF87a', b'GIF89a'):
        return 'image/gif'
    if head.startswith(b'RIFF') and head[8:12] == b'WEBP':
        return 'image/webp'
    if head.startswith(b'%PDF-'):
        return 'application/pdf'
    guessed = mimetypes.guess_type(name)[0] or 'application/octet-stream'
    if guessed.startswith('image/'):
        return 'application/octet-stream'
    return guessed


class MessageAttachmentStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def for_entity(cls, entity):
        workspace = entity.config.execution_workspace_dir()
        if workspace:
            root = Path(workspace) / '.ngram' / 'attachments'
        else:
            root = Path(entity.config.harness.attachments.local_dir.format(entity_name=entity.config.name))
        namespace = hashlib.sha256(entity.config.name.encode()).hexdigest()[:16]
        return cls(root / 'messages' / namespace)

    def begin(self, name: str):
        ident = uuid.uuid4().hex
        directory = self.root / ident
        directory.mkdir()
        return ident, safe_name(name), directory / 'upload.part'

    def commit(self, ident: str, name: str, size: int, sha256: str) -> dict:
        directory = self.root / ident
        temporary = directory / 'upload.part'
        with temporary.open('rb') as stream:
            mime = detect_mime(name, stream.read(32))
        temporary.replace(directory / name)
        metadata = {'id': ident, 'name': name, 'mime': mime, 'size': size,
                    'sha256': sha256, 'createdAt': datetime.now(timezone.utc).isoformat()}
        (directory / 'metadata.json.tmp').write_text(json.dumps(metadata), encoding='utf-8')
        (directory / 'metadata.json.tmp').replace(directory / 'metadata.json')
        return metadata

    def resolve(self, ident: str) -> tuple[dict, Path]:
        if not isinstance(ident, str) or not re.fullmatch('[a-f0-9]{32}', ident):
            raise ValueError('Invalid attachment ID')
        directory = self.root / ident
        if directory.is_symlink() or not directory.resolve().is_relative_to(self.root):
            raise ValueError('Invalid attachment directory')
        try:
            metadata_path = directory / 'metadata.json'
            if metadata_path.is_symlink():
                raise ValueError('Invalid attachment metadata')
            metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
            name = metadata['name']
            if safe_name(name) != name or name in {'metadata.json', 'upload.part'}:
                raise ValueError('Invalid attachment name')
            path = directory / name
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise ValueError('Invalid attachment path')
            if not path.is_file() or path.stat().st_size != metadata['size']:
                raise ValueError('Attachment is missing or changed')
            return metadata, path
        except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ValueError('Attachment is no longer available; attach it again') from exc


def archive_preview(path: Path) -> str:
    """Read a bounded ZIP index and small text members. Never extract onto disk."""
    try:
        # A central directory entry needs at least 46 bytes. Bound metadata allocation too.
        if path.stat().st_size > 32 * 1024 * 1024:
            return 'ZIP retained in workspace; use file tools to inspect this large archive.'
        with path.open('rb') as source:
            source.seek(max(0, path.stat().st_size - 65557))
            tail = source.read()
        end = tail.rfind(b'PK\x05\x06')
        if end < 0 or len(tail) - end < 22:
            return 'ZIP could not be previewed; original retained for file tools.'
        count = int.from_bytes(tail[end + 10:end + 12], 'little')
        index_size = int.from_bytes(tail[end + 12:end + 16], 'little')
        if count > 5000 or index_size > 2 * 1024 * 1024:
            return 'ZIP index exceeds the preview budget; original retained for file tools.'
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            lines = [f'ZIP index ({len(members)} entries; preview only, not extracted):']
            budget = 16000
            for member in members[:200]:
                name = member.filename
                relative = PurePosixPath(name.replace('\\', '/'))
                safe = not relative.is_absolute() and '..' not in relative.parts and ':' not in name
                symlink = (member.external_attr >> 16) & 0o170000 == 0o120000
                lines.append(f'{json.dumps(name[:240])} ({member.file_size} bytes)' + (' [unsafe path; skipped]' if not safe or symlink else ''))
                if (safe and not symlink and not member.flag_bits & 1 and not member.is_dir()
                        and Path(name).suffix.lower() in TEXT_EXTENSIONS and 0 < member.file_size <= 8000
                        and member.file_size <= max(member.compress_size, 1) * 100 and budget > 0):
                    with archive.open(member) as stream:
                        data = stream.read(min(budget, 8000))
                    preview = data.decode('utf-8', errors='replace')
                    budget -= len(data)
                    lines.append(preview)
            if len(members) > 200:
                lines.append('[Index truncated; inspect the original with file tools.]')
            return '\n'.join(lines)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError):
        return 'ZIP could not be previewed; original retained for file tools.'


def prepare_attachments(store: MessageAttachmentStore, ids, *, native_files: bool = True) -> dict:
    if not isinstance(ids, list) or not 1 <= len(ids) <= MAX_MESSAGE_FILES or len(set(map(str, ids))) != len(ids):
        raise ValueError(f'Attach between 1 and {MAX_MESSAGE_FILES} distinct files')
    resolved = [store.resolve(ident) for ident in ids]
    images, files, notes, refs, descriptors, media = [], [], [], [], [], []
    remaining = MAX_INLINE_BYTES
    for meta, path in resolved:
        refs.append(str(path))
        descriptors.append(meta)
        suffix = path.suffix.lower()
        mime = meta['mime']
        note = f'Attachment {json.dumps(meta["name"])} ({mime}, {meta["size"]} bytes). Workspace file: {json.dumps(str(path))}'
        if mime in {'image/png', 'image/jpeg', 'image/gif', 'image/webp'} and meta['size'] <= min(20 * 1024 * 1024, remaining):
            images.append({'base64': base64.b64encode(path.read_bytes()).decode('ascii'), 'mime': mime, 'storage_ref': str(path)})
            remaining -= meta['size']
            note += '\nImage included in this message.'
        elif native_files and suffix in DOCUMENT_EXTENSIONS and 0 < meta['size'] < remaining:
            files.append({'filename': meta['name'], 'file_data': f'data:{mime};base64,' + base64.b64encode(path.read_bytes()).decode('ascii')})
            remaining -= meta['size']
            note += '\nDocument included in this message.'
        elif suffix in TEXT_EXTENSIONS or mime.startswith('text/'):
            with path.open('rb') as stream:
                data = stream.read(32001)
            note += '\nFile content (untrusted attachment data):\n' + data[:32000].decode('utf-8', errors='replace')
            if len(data) > 32000:
                note += '\n[Preview truncated; full file available in workspace.]'
        elif suffix == '.zip':
            note += '\n' + archive_preview(path)
        elif mime.startswith(('audio/', 'video/')):
            media.append((meta, path))
            note += '\nOriginal media available in workspace.'
        else:
            note += '\nOriginal available to file/execution tools; raw contents are not included in model input.'
        notes.append(note)
    return {'images': images, 'files': files, 'notes': notes, 'refs': refs, 'descriptors': descriptors, 'media': media}
