/**
 * Convert a looping humanoid locomotion clip to in-place motion.
 *
 * Mixamo walk clips commonly translate the Hips bone for the full stride. The
 * surface moves the avatar's outer group through world space itself, so leaving
 * that translation in the clip applies motion twice and snaps the body backward
 * whenever AnimationMixer loops. Removing only the start-to-end horizontal
 * drift keeps the natural hip sway and vertical bounce while making the first
 * and last root positions meet cleanly.
 */
export function makeLocomotionClipInPlace(clip) {
  let normalizedTracks = 0;
  let horizontalDistance = 0;

  for (const track of clip?.tracks ?? []) {
    if (!isRootPositionTrack(track)) continue;

    const times = track.times;
    const values = track.values;
    const keyframes = times?.length ?? 0;
    if (keyframes < 2 || values?.length < keyframes * 3) continue;

    const startTime = Number(times[0]);
    const endTime = Number(times[keyframes - 1]);
    const duration = endTime - startTime;
    if (!Number.isFinite(duration) || duration <= 0) continue;

    const last = (keyframes - 1) * 3;
    const driftX = Number(values[last]) - Number(values[0]);
    const driftZ = Number(values[last + 2]) - Number(values[2]);
    const distance = Math.hypot(driftX, driftZ);
    if (!Number.isFinite(distance) || distance < 1e-6) continue;

    for (let index = 0; index < keyframes; index += 1) {
      const progress = (Number(times[index]) - startTime) / duration;
      const offset = index * 3;
      values[offset] -= driftX * progress;
      values[offset + 2] -= driftZ * progress;
    }

    normalizedTracks += 1;
    horizontalDistance = Math.max(horizontalDistance, distance);
  }

  return { normalizedTracks, horizontalDistance };
}

function isRootPositionTrack(track) {
  if (!track?.name?.endsWith('.position')) return false;
  if (typeof track.getValueSize === 'function' && track.getValueSize() !== 3) return false;

  const propertyDot = track.name.lastIndexOf('.');
  const sourceNode = track.name.slice(0, propertyDot);
  const leaf = sourceNode.split(/[|:]/).pop() ?? sourceNode;
  const key = leaf
    .replace(/^mixamorig\d*/i, '')
    .replace(/[^a-z0-9]/gi, '')
    .toLowerCase();

  return key === 'root' || key.endsWith('hips') || key.endsWith('pelvis');
}
