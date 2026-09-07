export function mergeVoiceDraft(current: string, spoken: string): string {
  const existing = current.replace(/\s+$/, '');
  const dictation = spoken.trim();
  if (!dictation) return current;
  return existing ? `${existing} ${dictation}` : dictation;
}
