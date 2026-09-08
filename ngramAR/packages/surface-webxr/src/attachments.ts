export interface ChatAttachment {
  id: string;
  name: string;
  mime: string;
  size: number;
  shell: string;
}

export function attachmentUrl(file: ChatAttachment): string | null {
  if (!/^[a-f0-9]{32}$/.test(file.id) || !/^[a-z0-9-]+$/.test(file.shell)) return null;
  return `/api/shells/${file.shell}/attachments/${file.id}`;
}

export function formatFileSize(size: number): string {
  return size >= 1024 * 1024 ? `${(size / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.ceil(size / 1024))} KB`;
}

export function renderAttachments(parent: HTMLElement, files: ChatAttachment[] = []): void {
  const cards = document.createElement('div');
  cards.className = 'message-attachments';
  for (const file of files.slice(0, 10)) {
    const url = attachmentUrl(file);
    if (!url) continue;
    const card = document.createElement('div');
    card.className = 'message-attachment';
    if (['image/png', 'image/jpeg', 'image/webp', 'image/gif'].includes(file.mime)) {
      const image = document.createElement('img');
      image.src = url; image.alt = file.name; image.loading = 'lazy';
      card.append(image);
    } else if (file.mime.startsWith('audio/') || file.mime.startsWith('video/')) {
      const media = document.createElement(file.mime.startsWith('video/') ? 'video' : 'audio');
      media.src = url; media.controls = true; media.preload = 'none';
      media.setAttribute('aria-label', file.name);
      card.append(media);
    }
    const link = document.createElement('a');
    link.href = url; link.download = file.name;
    link.textContent = file.name; link.title = `Download ${file.name}`;
    const size = document.createElement('small');
    size.textContent = formatFileSize(file.size);
    card.append(link, size); cards.append(card);
  }
  if (cards.childElementCount) parent.append(cards);
}
