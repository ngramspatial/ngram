// @ts-nocheck
type GestureCallback = (gesture: string, hand: 'left' | 'right', position: { x: number; y: number; z: number }) => void;

const COOLDOWN_MS = 6000;

export class HandTracker {
  private lastGestureTime = 0;
  private lastGestureType = '';
  private callback: GestureCallback | null = null;
  private waveHistory: { time: number; x: number }[] = [];
  private pinchHeldFrames = 0;
  private pinchFiredThisHold = false;

  onGesture(cb: GestureCallback): void {
    this.callback = cb;
  }

  processFrame(frame: XRFrame, refSpace: XRReferenceSpace): void {
    if (!this.callback) return;
    const session = frame.session;
    const now = performance.now();

    for (const source of session.inputSources) {
      if (!source.hand) continue;
      const handedness = source.handedness === 'left' ? 'left' : 'right';

      const wrist = source.hand.get('wrist');
      const indexTip = source.hand.get('index-finger-tip');
      const thumbTip = source.hand.get('thumb-tip');
      const middleTip = source.hand.get('middle-finger-tip');
      const ringTip = source.hand.get('ring-finger-tip');
      const pinkyTip = source.hand.get('pinky-finger-tip');

      if (!wrist || !indexTip || !thumbTip) continue;

      const wristPose = frame.getJointPose?.(wrist, refSpace);
      const indexPose = frame.getJointPose?.(indexTip, refSpace);
      const thumbPose = frame.getJointPose?.(thumbTip, refSpace);
      const middlePose = middleTip ? frame.getJointPose?.(middleTip, refSpace) : null;
      const ringPose = ringTip ? frame.getJointPose?.(ringTip, refSpace) : null;
      const pinkyPose = pinkyTip ? frame.getJointPose?.(pinkyTip, refSpace) : null;

      if (!wristPose || !indexPose || !thumbPose) continue;

      const wristPos = wristPose.transform.position;
      const pos = { x: wristPos.x, y: wristPos.y, z: wristPos.z };

      // Pinch: thumb tip touches index tip (held for ~8 frames)
      const pinchDist = this.dist3d(thumbPose.transform.position, indexPose.transform.position);
      if (pinchDist < 0.03) {
        this.pinchHeldFrames++;
        if (this.pinchHeldFrames === 8 && !this.pinchFiredThisHold) {
          this.pinchFiredThisHold = true;
          this.tryEmit('pinch', handedness, pos, now);
        }
      } else {
        this.pinchHeldFrames = 0;
        this.pinchFiredThisHold = false;
      }

      if (now - this.lastGestureTime < COOLDOWN_MS) continue;

      if (this.detectWave(wristPos, now)) {
        this.emit('wave', handedness, pos, now);
        continue;
      }

      if (this.detectThumbsUp(thumbPose, wristPose, indexPose, middlePose)) {
        this.emit('thumbs_up', handedness, pos, now);
        continue;
      }

      if (this.detectOpenPalm(wristPose, indexPose, middlePose, ringPose, pinkyPose)) {
        this.emit('open_palm', handedness, pos, now);
        continue;
      }

      // Point gesture disabled — too noisy in practice
    }
  }

  private tryEmit(gesture: string, hand: 'left' | 'right', pos: { x: number; y: number; z: number }, now: number): void {
    if (now - this.lastGestureTime < COOLDOWN_MS) return;
    this.emit(gesture, hand, pos, now);
  }

  private emit(gesture: string, hand: 'left' | 'right', pos: { x: number; y: number; z: number }, now: number): void {
    this.lastGestureTime = now;
    this.lastGestureType = gesture;
    this.callback?.(gesture, hand, pos);
  }

  private dist3d(a: DOMPointReadOnly, b: DOMPointReadOnly): number {
    const dx = a.x - b.x;
    const dy = a.y - b.y;
    const dz = a.z - b.z;
    return Math.sqrt(dx * dx + dy * dy + dz * dz);
  }

  private detectWave(wristPos: DOMPointReadOnly, now: number): boolean {
    this.waveHistory.push({ time: now, x: wristPos.x });
    this.waveHistory = this.waveHistory.filter(e => now - e.time < 1000);
    if (this.waveHistory.length < 6) return false;

    let dirChanges = 0;
    for (let i = 2; i < this.waveHistory.length; i++) {
      const d1 = this.waveHistory[i - 1].x - this.waveHistory[i - 2].x;
      const d2 = this.waveHistory[i].x - this.waveHistory[i - 1].x;
      if ((d1 > 0.005 && d2 < -0.005) || (d1 < -0.005 && d2 > 0.005)) {
        dirChanges++;
      }
    }
    if (dirChanges >= 3) {
      this.waveHistory = [];
      return true;
    }
    return false;
  }

  private detectThumbsUp(
    thumb: XRJointPose, wrist: XRJointPose,
    index: XRJointPose, middle: XRJointPose | null | undefined,
  ): boolean {
    const thumbY = thumb.transform.position.y;
    const wristY = wrist.transform.position.y;
    const indexY = index.transform.position.y;
    if (thumbY - wristY < 0.06) return false;
    if (indexY > wristY + 0.02) return false;
    if (middle && middle.transform.position.y > wristY + 0.02) return false;
    return true;
  }

  private detectOpenPalm(
    wrist: XRJointPose,
    index: XRJointPose,
    middle: XRJointPose | null | undefined,
    ring: XRJointPose | null | undefined,
    pinky: XRJointPose | null | undefined,
  ): boolean {
    const wy = wrist.transform.position.y;
    const threshold = 0.04;
    if (index.transform.position.y - wy < threshold) return false;
    if (middle && middle.transform.position.y - wy < threshold) return false;
    if (ring && ring.transform.position.y - wy < threshold) return false;
    if (pinky && pinky.transform.position.y - wy < threshold) return false;
    return true;
  }

  private detectPoint(
    index: XRJointPose,
    middle: XRJointPose | null | undefined,
    ring: XRJointPose | null | undefined,
    pinky: XRJointPose | null | undefined,
    wrist: XRJointPose,
  ): boolean {
    const wy = wrist.transform.position.y;
    if (index.transform.position.y - wy < 0.05) return false;
    if (middle && middle.transform.position.y - wy > 0.03) return false;
    if (ring && ring.transform.position.y - wy > 0.03) return false;
    if (pinky && pinky.transform.position.y - wy > 0.03) return false;
    return true;
  }

  private detectFingerGun(
    wrist: XRJointPose,
    index: XRJointPose,
    thumb: XRJointPose,
    middle: XRJointPose | null | undefined,
    ring: XRJointPose | null | undefined,
    pinky: XRJointPose | null | undefined,
  ): boolean {
    const wp = wrist.transform.position;
    // Index extended away from wrist
    if (this.dist3d(index.transform.position, wp) < 0.06) return false;
    // Thumb extended upward (above wrist)
    if (thumb.transform.position.y - wp.y < 0.03) return false;
    // Middle, ring, pinky curled toward wrist
    if (middle && this.dist3d(middle.transform.position, wp) > 0.08) return false;
    if (ring && this.dist3d(ring.transform.position, wp) > 0.08) return false;
    if (pinky && this.dist3d(pinky.transform.position, wp) > 0.08) return false;
    return true;
  }

  private detectFist(
    wrist: XRJointPose,
    index: XRJointPose,
    thumb: XRJointPose,
    middle: XRJointPose | null | undefined,
    ring: XRJointPose | null | undefined,
    pinky: XRJointPose | null | undefined,
  ): boolean {
    const wp = wrist.transform.position;
    if (this.dist3d(index.transform.position, wp) > 0.08) return false;
    if (this.dist3d(thumb.transform.position, wp) > 0.08) return false;
    if (middle && this.dist3d(middle.transform.position, wp) > 0.08) return false;
    if (ring && this.dist3d(ring.transform.position, wp) > 0.08) return false;
    if (pinky && this.dist3d(pinky.transform.position, wp) > 0.08) return false;
    return true;
  }
}
