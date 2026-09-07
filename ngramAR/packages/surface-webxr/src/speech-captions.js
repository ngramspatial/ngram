/** Small, word-aligned pages; offsets remain in the original spoken transcript. */
export function captionPages(text, limit = 80) {
  const pages = [];
  let start = 0;
  while (start < text.length) {
    while (/\s/.test(text[start] ?? '') && start < text.length) start++;
    if (start >= text.length) break;
    let end = start, width = 0;
    for (const char of text.slice(start)) {
      // Wide glyphs need more room than Latin text in both DOM and XR captions.
      const units = char.codePointAt(0) > 255 ? 2 : 1;
      if (width + units > limit) break;
      width += units; end += char.length;
    }
    if (end < text.length) {
      const space = text.lastIndexOf(' ', end);
      if (space > start + (end - start) / 2) end = space;
      // Don't split a UTF-16 surrogate pair in an unbroken token.
      if (/[\uD800-\uDBFF]/.test(text[end - 1])) end--;
    }
    pages.push({ text: text.slice(start, end).replace(/\s+/g, ' ').trim(), start, end });
    start = end;
  }
  return pages;
}

export function captionAt(pages, charIndex) {
  return (pages.find(page => charIndex < page.end) ?? pages.at(-1))?.text ?? '';
}
