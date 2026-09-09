/** Format speech locally: no extra model call, and no rewriting what was said. */
export function formatDictation(text, complete = false, language = 'en') {
  let result = text.trim();
  if (language.toLowerCase().startsWith('en')) {
    const commands = {
      'new paragraph': '\n\n', 'new line': '\n',
      'question mark': '?', 'exclamation mark': '!', 'exclamation point': '!',
      'full stop': '.', comma: ',', semicolon: ';',
    };
    result = result.replace(/\b(new paragraph|new line|question mark|exclamation mark|exclamation point|full stop|comma|semicolon)\b/gi,
      (match) => commands[match.toLowerCase()]);
    result = result.replace(/\bi\b/g, 'I');
  }
  result = result
    .replace(/[^\S\n]+/g, ' ')
    .replace(/ *\n */g, '\n')
    .replace(/ +([,.;:!?])/g, '$1')
    .replace(/([,;!?])(?=[\p{L}\p{N}])/gu, '$1 ')
    .replace(/(^|[.!?]\s+|\n+)(["'“‘(]*)(\p{L})/gu,
      (_, boundary, quote, letter) => boundary + quote + letter.toLocaleUpperCase(language));
  if (complete && /[\p{L}\p{N}]["'”’)]?$/u.test(result)) {
    // Browsers often supply no punctuation. Existing punctuation always wins.
    const sentence = result.split(/[.!?]\s+|\n/).at(-1) ?? result;
    const question = language.toLowerCase().startsWith('en')
      && /^(?:(?:who|what|where|when|why|how)\b(?!\s+(?:a|an)\b)|(?:can|could|would|will|should|do|does|did|is|are|was|were|have|has)\s+(?:you|we|I|he|she|it|they|there|this|that)\b)/i.test(sentence);
    result = result.replace(/(["'”’)]?)$/, `${question ? '?' : '.'}$1`);
  }
  return result;
}
