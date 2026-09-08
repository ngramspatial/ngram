import { formatFileSize, type ChatAttachment } from './attachments.js';

const MAX_FILE_BYTES = 100 * 1024 * 1024;
const MAX_FILES = 10;
interface DraftFile { file: File; preview?: string; uploaded?: ChatAttachment; progress: number; }

export function setupAttachments(input: HTMLTextAreaElement, getShell: () => string,
  notify: (message: string) => void) {
  const bar = input.closest('.command-bar') as HTMLElement;
  const actions = bar.querySelector('.command-actions')!;
  const button = document.createElement('button');
  button.type = 'button'; button.id = 'attach-btn'; button.title = 'Attach files';
  button.setAttribute('aria-label', 'Attach files');
  button.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="m8 13 7-7a3 3 0 0 1 4.2 4.2l-9.4 9.4a5 5 0 0 1-7.1-7.1l10-10a2 2 0 0 1 2.8 2.8L5.7 15a1 1 0 0 0 1.4 1.4L15 8.5"/></svg>';
  const picker = document.createElement('input');
  picker.type = 'file'; picker.multiple = true; picker.hidden = true; picker.id = 'attachment-input';
  picker.setAttribute('aria-label', 'Choose attachments');
  actions.prepend(button); bar.append(picker);
  const tray = document.createElement('div');
  tray.className = 'attachment-tray'; tray.hidden = true;
  tray.setAttribute('aria-label', 'Message attachments');
  bar.prepend(tray);
  const status = document.createElement('span');
  status.className = 'attachment-status'; status.setAttribute('role', 'status');
  status.setAttribute('aria-live', 'polite'); bar.append(status);
  let draft: DraftFile[] = [];
  let uploading = false;
  let generation = 0;
  let request: XMLHttpRequest | undefined;

  function render() {
    tray.replaceChildren(); tray.hidden = draft.length === 0;
    button.disabled = uploading;
    button.classList.toggle('has-attachments', draft.length > 0);
    for (const item of draft) {
      const chip = document.createElement('div'); chip.className = 'attachment-chip';
      if (item.preview) {
        const image = document.createElement('img'); image.src = item.preview; image.alt = '';
        chip.append(image);
      } else {
        const icon = document.createElement('span'); icon.className = 'attachment-file-icon';
        icon.textContent = item.file.name.split('.').pop()?.slice(0, 5).toUpperCase() || 'FILE';
        chip.append(icon);
      }
      const label = document.createElement('span'); label.className = 'attachment-label';
      const name = document.createElement('span'); name.textContent = item.file.name; name.title = item.file.name;
      const detail = document.createElement('small');
      detail.textContent = uploading ? (item.uploaded ? 'Ready' : `Uploading ${item.progress}%`) : formatFileSize(item.file.size);
      label.append(name, detail); chip.append(label);
      const remove = document.createElement('button'); remove.type = 'button';
      remove.textContent = '×'; remove.setAttribute('aria-label', `Remove ${item.file.name}`);
      remove.disabled = uploading;
      remove.onclick = () => {
        if (item.preview) URL.revokeObjectURL(item.preview);
        draft = draft.filter(entry => entry !== item); render(); input.focus();
      };
      chip.append(remove); tray.append(chip);
    }
    if (uploading) {
      const cancel = document.createElement('button'); cancel.type = 'button'; cancel.className = 'attachment-cancel';
      cancel.textContent = 'Cancel upload'; cancel.onclick = () => cancelUpload(); tray.append(cancel);
    }
  }

  function add(files: File[]) {
    if (uploading) { notify('Finish or cancel this upload before adding files.'); return; }
    const errors: string[] = [];
    for (const file of files) {
      if (file.size > MAX_FILE_BYTES) { errors.push(`${file.name} exceeds 100 MB.`); continue; }
      if (draft.length >= MAX_FILES) { errors.push('You can attach up to 10 files per message.'); break; }
      draft.push({ file, progress: 0, preview: /^image\/(png|jpeg|webp|gif)$/.test(file.type) ? URL.createObjectURL(file) : undefined });
    }
    render(); input.focus();
    status.textContent = `${draft.length} attachment${draft.length === 1 ? '' : 's'} ready to send`;
    if (errors.length) notify(errors.join(' '));
  }

  button.onclick = () => picker.click();
  picker.onchange = () => { add(Array.from(picker.files ?? [])); picker.value = ''; };
  input.addEventListener('paste', event => {
    const files = Array.from(event.clipboardData?.files ?? []);
    if (!files.length) return;
    event.preventDefault(); add(files);
    const text = event.clipboardData?.getData('text/plain');
    if (text) { input.setRangeText(text, input.selectionStart, input.selectionEnd, 'end'); input.dispatchEvent(new Event('input')); }
  });
  let dragDepth = 0;
  const isFileDrag = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files');
  document.addEventListener('dragenter', event => {
    if (!isFileDrag(event)) return;
    event.preventDefault(); dragDepth++; bar.classList.add('attachment-drop-target');
  });
  document.addEventListener('dragover', event => { if (isFileDrag(event)) event.preventDefault(); });
  document.addEventListener('dragleave', event => {
    if (!isFileDrag(event)) return;
    if (--dragDepth <= 0) { dragDepth = 0; bar.classList.remove('attachment-drop-target'); }
  });
  document.addEventListener('drop', event => {
    if (!isFileDrag(event)) return;
    dragDepth = 0; bar.classList.remove('attachment-drop-target');
    // Let a dedicated scene importer keep ownership of a drop it already handled.
    if (event.defaultPrevented || (event.target instanceof Element && event.target.closest('input[type=file], [data-file-drop]'))) return;
    event.preventDefault(); add(Array.from(event.dataTransfer?.files ?? []));
  });

  function cancelUpload() {
    generation++; request?.abort(); request = undefined; uploading = false;
    status.textContent = 'Upload cancelled. Your draft is still here.'; render();
  }
  function clear() {
    generation++; request?.abort(); request = undefined; uploading = false;
    draft.forEach(item => { if (item.preview) URL.revokeObjectURL(item.preview); });
    draft = []; status.textContent = ''; render();
  }
  async function prepare(): Promise<ChatAttachment[]> {
    if (uploading) throw Error('An upload is already running.');
    const shell = getShell();
    if (!/^[a-z0-9-]+$/.test(shell)) throw Error('Connect an agent before attaching files.');
    const version = generation;
    uploading = true; render();
    try {
      for (const item of draft) {
        if (item.uploaded?.shell === shell) continue;
        status.textContent = `Uploading ${item.file.name}`;
        item.uploaded = await new Promise<ChatAttachment>((resolve, reject) => {
          const xhr = new XMLHttpRequest(); request = xhr;
          xhr.open('POST', `/api/shells/${shell}/attachments`); xhr.timeout = 300_000;
          xhr.setRequestHeader('Content-Type', item.file.type || 'application/octet-stream');
          xhr.setRequestHeader('X-Ngram-Filename', encodeURIComponent(item.file.name));
          xhr.upload.onprogress = event => { item.progress = event.lengthComputable ? Math.round(event.loaded * 100 / event.total) : 0; render(); };
          xhr.onload = () => {
            let result: any;
            try { result = JSON.parse(xhr.responseText); } catch { reject(Error('Upload failed. Retry when the worker is available.')); return; }
            if (xhr.status !== 201 || !/^[a-f0-9]{32}$/.test(result.id)) { reject(Error(result.error || `Upload failed (${xhr.status}).`)); return; }
            resolve({ id: result.id, name: result.name, mime: result.mime, size: result.size, shell });
          };
          xhr.onerror = () => reject(Error('Upload failed. Check the connection and retry.'));
          xhr.ontimeout = () => reject(Error('Upload timed out. Your draft is still here.'));
          xhr.onabort = () => reject(new DOMException('Upload cancelled', 'AbortError'));
          xhr.send(item.file);
        });
        if (generation !== version || getShell() !== shell) throw new DOMException('Draft changed', 'AbortError');
        render();
      }
      return draft.map(item => item.uploaded!);
    } finally {
      if (generation === version) { request = undefined; uploading = false; status.textContent = ''; render(); }
    }
  }
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && uploading) { event.preventDefault(); cancelUpload(); }
  });
  return { hasFiles: () => draft.length > 0, isUploading: () => uploading, prepare, clear, cancelUpload };
}
