export const UI_THEMES = Object.freeze(['light', 'dark', 'periwinkle']);

export function isThemeName(value) {
  return UI_THEMES.includes(value);
}

export function normalizeTheme(value) {
  return isThemeName(value) ? value : 'light';
}

export function nextTheme(value) {
  const current = normalizeTheme(value);
  const index = UI_THEMES.indexOf(current);
  return UI_THEMES[(index + 1) % UI_THEMES.length];
}

export function themeUsesDarkPanels(value) {
  return normalizeTheme(value) === 'dark';
}

export function themeOverridesEnvironment(value) {
  // UI color never suppresses an explicitly selected scene preset/background.
  return false;
}
