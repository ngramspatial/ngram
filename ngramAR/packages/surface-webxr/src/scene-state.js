/**
 * Durable, versioned persistence for the spatial workspace.
 *
 * This module deliberately has no DOM or Three.js dependency so migrations can
 * be covered by Node tests. Scene managers own their domain-specific state;
 * this module only validates the outer envelope and migrates legacy keys.
 */

export const SCENE_STATE_VERSION = 1;
export const SCENE_STATE_KEY = 'ngram_ar_scene_state_v1';

export const LEGACY_SCENE_STATE_KEYS = Object.freeze([
  'ngram_ar_objects',
  'ngram_ar_env',
  'ngram_ar_bg',
  'ngram_ar_placement',
  'ngram_ar_sticky_notes',
]);

function safeParse(value, fallback) {
  if (!value) return fallback;
  try {
    return JSON.parse(value);
  } catch {
    return fallback;
  }
}

function safeGet(storage, key) {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function finiteNumber(value) {
  return typeof value === 'number' && Number.isFinite(value);
}

function vec3(value) {
  if (Array.isArray(value) && value.length >= 3 && value.slice(0, 3).every(finiteNumber)) {
    return value.slice(0, 3);
  }
  if (value && finiteNumber(value.x) && finiteNumber(value.y) && finiteNumber(value.z)) {
    return [value.x, value.y, value.z];
  }
  return undefined;
}

function quat4(value) {
  if (Array.isArray(value) && value.length >= 4 && value.slice(0, 4).every(finiteNumber)) {
    return value.slice(0, 4);
  }
  if (
    value
    && finiteNumber(value.x)
    && finiteNumber(value.y)
    && finiteNumber(value.z)
    && finiteNumber(value.w)
  ) {
    return [value.x, value.y, value.z, value.w];
  }
  return undefined;
}

export function emptySceneState() {
  return {
    version: SCENE_STATE_VERSION,
    updatedAt: 0,
    camera: undefined,
    avatar: undefined,
    environment: undefined,
    objects: [],
    panels: [],
    drawings: [],
    overlayPanels: {},
    overlayTransforms: {},
  };
}

/** Normalize an untrusted localStorage value while retaining manager payloads. */
export function normalizeSceneState(value) {
  const state = emptySceneState();
  if (!value || typeof value !== 'object' || Array.isArray(value)) return state;

  if (finiteNumber(value.updatedAt)) state.updatedAt = value.updatedAt;

  if (value.camera && typeof value.camera === 'object') {
    const position = vec3(value.camera.position);
    const quaternion = quat4(value.camera.quaternion);
    const target = vec3(value.camera.target);
    if (position && target) {
      state.camera = { position, target, ...(quaternion ? { quaternion } : {}) };
    }
  }

  if (value.avatar && typeof value.avatar === 'object') {
    const position = vec3(value.avatar.position);
    const quaternion = quat4(value.avatar.quaternion);
    const scale = finiteNumber(value.avatar.scale) ? value.avatar.scale : 1;
    if (position) {
      state.avatar = {
        position,
        scale,
        ...(quaternion ? { quaternion } : {}),
      };
    }
  }

  if (value.environment && typeof value.environment === 'object') {
    state.environment = { ...value.environment };
  }
  if (Array.isArray(value.objects)) state.objects = value.objects;
  if (Array.isArray(value.panels)) state.panels = value.panels;
  if (Array.isArray(value.drawings)) state.drawings = value.drawings;
  if (value.overlayPanels && typeof value.overlayPanels === 'object' && !Array.isArray(value.overlayPanels)) {
    state.overlayPanels = value.overlayPanels;
  }
  if (value.overlayTransforms && typeof value.overlayTransforms === 'object' && !Array.isArray(value.overlayTransforms)) {
    state.overlayTransforms = value.overlayTransforms;
  }

  return state;
}

/**
 * Load the unified state. If it does not exist, import all supported legacy
 * keys and immediately write the new format so migration happens only once.
 */
export function loadSceneState(storage) {
  const unified = safeParse(safeGet(storage, SCENE_STATE_KEY), null);
  if (
    unified
    && typeof unified === 'object'
    && !Array.isArray(unified)
    && unified.version === SCENE_STATE_VERSION
  ) {
    return normalizeSceneState(unified);
  }

  const migrated = emptySceneState();
  let foundLegacy = false;

  const objects = safeParse(safeGet(storage, 'ngram_ar_objects'), null);
  if (Array.isArray(objects)) {
    migrated.objects = objects;
    foundLegacy = true;
  }

  const panels = safeParse(safeGet(storage, 'ngram_ar_sticky_notes'), null);
  if (Array.isArray(panels)) {
    migrated.panels = panels;
    foundLegacy = true;
  }

  const placement = safeParse(safeGet(storage, 'ngram_ar_placement'), null);
  const placementPosition = vec3(placement);
  if (placementPosition) {
    migrated.avatar = { position: placementPosition, scale: 1 };
    foundLegacy = true;
  }

  const preset = safeGet(storage, 'ngram_ar_env');
  const background = safeParse(safeGet(storage, 'ngram_ar_bg'), null);
  if (preset || background) {
    migrated.environment = {
      ...(preset ? { preset } : {}),
      ...(background && typeof background === 'object' ? { background } : {}),
    };
    foundLegacy = true;
  }

  if (foundLegacy) {
    try {
      writeSceneState(storage, migrated);
    } catch {
      // Keep the readable legacy keys intact. The coordinator will retry the
      // unified write later, but persistence failure must never block startup.
    }
  }
  return migrated;
}

export function writeSceneState(storage, value) {
  const normalized = normalizeSceneState(value);
  normalized.updatedAt = Date.now();
  storage.setItem(SCENE_STATE_KEY, JSON.stringify(normalized));
  return normalized;
}

export function removeLegacySceneState(storage) {
  for (const key of LEGACY_SCENE_STATE_KEYS) storage.removeItem(key);
}
