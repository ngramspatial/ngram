// @ts-nocheck
export const VISEME_MAP: Record<number, string> = {
  0: "sil",
  1: "PP",
  2: "FF",
  3: "TH",
  4: "DD",
  5: "kk",
  6: "CH",
  7: "SS",
  8: "nn",
  9: "RR",
  10: "aa",
  11: "E",
  12: "ih",
  13: "oh",
  14: "ou",
};

export const VISEME_NAMES: Record<string, number> = Object.fromEntries(
  Object.entries(VISEME_MAP).map(([idx, name]) => [name, Number(idx)]),
);

const CHAR_TO_VISEME: Record<string, number> = {
  a: 10,
  e: 11,
  i: 12,
  o: 13,
  u: 14,
  p: 1,
  b: 1,
  m: 1,
  f: 2,
  v: 2,
  t: 4,
  d: 4,
  k: 5,
  g: 5,
  c: 5,
  s: 7,
  z: 7,
  n: 8,
  l: 8,
  r: 9,
  h: 0,
  j: 6,
  w: 14,
  y: 12,
  q: 5,
  x: 7,
};

/**
 * Rough heuristic: maps text characters to viseme keyframes spread
 * across the given duration. Returns [visemeIndex, weight, timeMs][].
 */
export function generateBasicVisemes(
  text: string,
  durationMs: number,
): [number, number, number][] {
  const chars = text.toLowerCase().replace(/[^a-z]/g, "");
  if (chars.length === 0) return [[0, 1, 0]];

  const keyframes: [number, number, number][] = [];
  const interval = durationMs / (chars.length + 1);

  for (let i = 0; i < chars.length; i++) {
    const viseme = CHAR_TO_VISEME[chars[i]] ?? 0;
    const timeMs = Math.round(interval * (i + 1));
    keyframes.push([viseme, 1, timeMs]);
  }

  if (keyframes.length === 0 || keyframes[0][2] > 0) {
    keyframes.unshift([0, 1, 0]);
  }
  keyframes.push([0, 1, durationMs]);

  return keyframes;
}
