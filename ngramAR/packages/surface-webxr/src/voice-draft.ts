/** Own only the dictated range, leaving the rest of an editable draft alone. */
export class VoiceDraft {
  private previousValue: string;
  private start: number;
  private end: number;
  private transcript = '';
  private consumedWords = 0;

  constructor(value: string, start = value.length, end = start) {
    this.previousValue = value;
    this.start = start;
    this.end = end;
  }

  update(value: string, transcript: string, selectionStart = value.length, selectionEnd = selectionStart) {
    if (value !== this.previousValue) {
      // Locate the user's edit relative to our range. Edits inside it take
      // ownership: freeze their correction and dictate new words at the caret.
      let prefix = 0;
      while (prefix < value.length && prefix < this.previousValue.length
        && value[prefix] === this.previousValue[prefix]) prefix++;
      let suffix = 0;
      while (suffix < value.length - prefix && suffix < this.previousValue.length - prefix
        && value[value.length - 1 - suffix] === this.previousValue[this.previousValue.length - 1 - suffix]) suffix++;
      const oldEnd = this.previousValue.length - suffix;
      const delta = value.length - this.previousValue.length;
      if (oldEnd <= this.start) {
        this.start += delta;
        this.end += delta;
      } else if (prefix < this.end) {
        this.start = selectionStart;
        this.end = selectionEnd;
        this.consumedWords = this.transcript.trim().split(/\s+/).filter(Boolean).length;
      }
    }
    const words = transcript.match(/\S+/g) ?? [];
    // Keep line breaks in ordinary dictation; only slice after a manual correction.
    const spoken = this.consumedWords ? words.slice(this.consumedWords).join(' ') : transcript;
    const before = value.slice(0, this.start);
    const after = value.slice(this.end);
    const leftSpace = spoken && before && !/\s$/.test(before) && !/^[,.;:!?\n]/.test(spoken) ? ' ' : '';
    const rightSpace = spoken && after && !/^\s|^[,.;:!?]/.test(after) && !/\s$/.test(spoken) ? ' ' : '';
    const inserted = leftSpace + spoken + rightSpace;
    const next = before + inserted + after;
    const previousEnd = this.end;
    const nextEnd = this.start + inserted.length;
    const moveCaret = (position: number) => position < this.start ? position
      : position <= previousEnd ? nextEnd : position + nextEnd - previousEnd;
    this.previousValue = next;
    this.end = nextEnd;
    this.transcript = transcript;
    return { value: next, selectionStart: moveCaret(selectionStart), selectionEnd: moveCaret(selectionEnd) };
  }
}
