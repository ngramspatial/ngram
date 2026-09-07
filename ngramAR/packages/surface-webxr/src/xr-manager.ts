// @ts-nocheck
import * as THREE from 'three';

export interface XRCapabilities {
  ar: boolean;
  handTracking: boolean;
}

type PlacementCallback = (position: THREE.Vector3) => void;
type SessionEndCallback = () => void;
type ControllerCallback = (action: string) => void;
type ThumbstickCallback = (x: number, y: number) => void;

export class XRManager {
  private session: XRSession | null = null;
  private hitTestSource: XRHitTestSource | null = null;
  private referenceSpace: XRReferenceSpace | null = null;

  private reticle: THREE.Group | null = null;
  private lastHitPosition: THREE.Vector3 | null = null;
  private placementCallback: PlacementCallback | null = null;
  private sessionEndCallback: SessionEndCallback | null = null;
  private controllerCallback: ControllerCallback | null = null;
  private thumbstickCallback: ThumbstickCallback | null = null;

  private squeezeDown = false;
  private buttonDown = new Map<string, boolean>();

  /** While > 0, left tracked hand has an active select (pinch) — skip placement reticle / hit test. */
  private leftTrackedHandSelectDepth = 0;

  isARActive = false;
  isPlacementMode = false;
  hasDomOverlay = false;

  async checkSupport(): Promise<XRCapabilities> {
    if (!navigator.xr) return { ar: false, handTracking: false };

    let ar = false;
    let handTracking = false;

    try {
      ar = await navigator.xr.isSessionSupported('immersive-ar');
    } catch { /* unsupported */ }

    if (ar) {
      try {
        // Probe whether the browser actually supports hand-tracking by checking
        // if a session with the feature can be created. Some browsers advertise
        // immersive-ar but don't support hand-tracking at all.
        const probe = await navigator.xr.isSessionSupported('immersive-ar');
        // The WebXR Hand Input spec doesn't expose a separate capability query,
        // so we infer from the user agent: Quest Browser / Oculus Browser ships
        // hand tracking; other AR browsers generally don't yet.
        handTracking = probe && /OculusBrowser|Quest/i.test(navigator.userAgent);
      } catch {
        handTracking = false;
      }
    }

    return { ar, handTracking };
  }

  async startAR(
    renderer: THREE.WebGLRenderer,
    scene: THREE.Scene,
    camera: THREE.PerspectiveCamera,
  ): Promise<void> {
    if (!navigator.xr) throw new Error('WebXR not available');

    const overlayRoot = document.querySelector('.overlay') as HTMLElement | null;

    const optionalFeatures: string[] = [
      'local-floor',
      'hit-test',
      'hand-tracking',
      'anchors',
      'plane-detection',
      'dom-overlay',
    ];

    const sessionInit: any = { optionalFeatures };
    if (overlayRoot) {
      sessionInit.domOverlay = { root: overlayRoot };
    }

    const session = await navigator.xr.requestSession('immersive-ar', sessionInit);

    this.session = session;
    this.isARActive = true;
    this.hasDomOverlay = !!(session as any).domOverlayState?.type;

    renderer.xr.enabled = true;
    await renderer.xr.setSession(session);

    try {
      this.referenceSpace = await session.requestReferenceSpace('local-floor');
    } catch {
      this.referenceSpace = await session.requestReferenceSpace('local');
    }

    const ground = scene.getObjectByName('desktop-ground');
    if (ground) ground.visible = false;
    scene.background = null;
    scene.fog = null;

    try {
      const viewerSpace = await session.requestReferenceSpace('viewer');
      const src = await (session as any).requestHitTestSource({ space: viewerSpace });
      this.hitTestSource = src;
    } catch {
      /* hit-test may not be available */
    }

    this.createReticle(scene);
    this.enterPlacementMode();

    const isLeftTrackedHand = (src: XRInputSource) =>
      src.handedness === 'left' && !!src.hand;

    session.addEventListener('selectstart', (evt: Event) => {
      const e = evt as XRInputSourceEvent;
      if (isLeftTrackedHand(e.inputSource)) {
        this.leftTrackedHandSelectDepth++;
      }
    });

    session.addEventListener('selectend', (evt: Event) => {
      const e = evt as XRInputSourceEvent;
      if (isLeftTrackedHand(e.inputSource)) {
        this.leftTrackedHandSelectDepth = Math.max(0, this.leftTrackedHandSelectDepth - 1);
      }
    });

    session.addEventListener('select', (evt: Event) => {
      const e = evt as XRInputSourceEvent;
      const src = e.inputSource;
      // Left-hand pinch (hand tracking) must not run placement (reticle preview or confirm).
      if (isLeftTrackedHand(src)) return;
      if (this.isPlacementMode && this.lastHitPosition) {
        const pos = this.lastHitPosition.clone();
        this.exitPlacementMode();
        this.placementCallback?.(pos);
      }
    });

    session.addEventListener('squeeze', () => {
      this.controllerCallback?.('squeeze');
    });

    session.addEventListener('end', () => {
      this.leftTrackedHandSelectDepth = 0;
      this.buttonDown.clear();
      this.isARActive = false;
      this.isPlacementMode = false;
      this.hasDomOverlay = false;
      this.hitTestSource = null;
      this.referenceSpace = null;
      this.session = null;
      renderer.xr.enabled = false;
      this.removeReticle(scene);
      if (ground) ground.visible = true;
      this.sessionEndCallback?.();
    });
  }

  private outerRing: THREE.Mesh | null = null;
  private innerRing: THREE.Mesh | null = null;
  private centerDot: THREE.Mesh | null = null;
  private reticlePillar: THREE.Mesh | null = null;
  private outerRingMat: THREE.MeshBasicMaterial | null = null;
  private innerRingMat: THREE.MeshBasicMaterial | null = null;
  private centerDotMat: THREE.MeshBasicMaterial | null = null;
  private pillarMat: THREE.MeshBasicMaterial | null = null;
  private reticleElapsed = 0;

  private createReticle(scene: THREE.Scene): void {
    this.reticle = new THREE.Group();
    this.reticle.name = 'placement-reticle';
    this.reticle.visible = false;

    // Outer ring — slowly rotates
    const outerGeo = new THREE.RingGeometry(0.09, 0.1, 48);
    outerGeo.rotateX(-Math.PI / 2);
    this.outerRingMat = new THREE.MeshBasicMaterial({
      color: 0x6e7dff,
      transparent: true,
      opacity: 0.65,
      side: THREE.DoubleSide,
    });
    this.outerRing = new THREE.Mesh(outerGeo, this.outerRingMat);
    this.reticle.add(this.outerRing);

    // Inner ring — counter-rotates
    const innerGeo = new THREE.RingGeometry(0.055, 0.062, 48);
    innerGeo.rotateX(-Math.PI / 2);
    this.innerRingMat = new THREE.MeshBasicMaterial({
      color: 0xccd2ff,
      transparent: true,
      opacity: 0.4,
      side: THREE.DoubleSide,
    });
    this.innerRing = new THREE.Mesh(innerGeo, this.innerRingMat);
    this.reticle.add(this.innerRing);

    // Cross markers at cardinal points
    const crossMat = new THREE.MeshBasicMaterial({
      color: 0xccd2ff,
      transparent: true,
      opacity: 0.5,
      side: THREE.DoubleSide,
    });
    for (let i = 0; i < 4; i++) {
      const tickGeo = new THREE.PlaneGeometry(0.003, 0.018);
      tickGeo.rotateX(-Math.PI / 2);
      const tick = new THREE.Mesh(tickGeo, crossMat);
      const angle = (i * Math.PI) / 2;
      tick.position.set(Math.cos(angle) * 0.076, 0.001, Math.sin(angle) * 0.076);
      tick.rotation.y = angle;
      this.reticle.add(tick);
    }

    // Center dot — pulses
    const dotGeo = new THREE.CircleGeometry(0.012, 24);
    dotGeo.rotateX(-Math.PI / 2);
    this.centerDotMat = new THREE.MeshBasicMaterial({
      color: 0xffffff,
      transparent: true,
      opacity: 0.9,
    });
    this.centerDot = new THREE.Mesh(dotGeo, this.centerDotMat);
    this.centerDot.position.y = 0.001;
    this.reticle.add(this.centerDot);

    // Pillar — subtler, with gradient-like taper
    const pillarGeo = new THREE.CylinderGeometry(0.001, 0.003, 0.25, 8);
    this.pillarMat = new THREE.MeshBasicMaterial({
      color: 0x6e7dff,
      transparent: true,
      opacity: 0.25,
    });
    this.reticlePillar = new THREE.Mesh(pillarGeo, this.pillarMat);
    this.reticlePillar.position.y = 0.125;
    this.reticle.add(this.reticlePillar);

    scene.add(this.reticle);
  }

  animateReticle(dt: number): void {
    if (!this.reticle?.visible) return;
    this.reticleElapsed += dt;
    const t = this.reticleElapsed;

    if (this.outerRing) {
      this.outerRing.rotation.y = t * 0.4;
    }
    if (this.innerRing) {
      this.innerRing.rotation.y = -t * 0.6;
    }

    // Pulse the center dot
    if (this.centerDotMat) {
      this.centerDotMat.opacity = 0.6 + Math.sin(t * 3) * 0.3;
    }
    if (this.centerDot) {
      const s = 1 + Math.sin(t * 3) * 0.15;
      this.centerDot.scale.setScalar(s);
    }

    // Subtle outer ring opacity breathe
    if (this.outerRingMat) {
      this.outerRingMat.opacity = 0.5 + Math.sin(t * 1.5) * 0.15;
    }

    // Pillar fade
    if (this.pillarMat) {
      this.pillarMat.opacity = 0.15 + Math.sin(t * 2) * 0.1;
    }
  }

  private removeReticle(scene: THREE.Scene): void {
    if (this.reticle) {
      scene.remove(this.reticle);
      this.reticle.traverse((obj) => {
        if (obj instanceof THREE.Mesh) {
          obj.geometry.dispose();
          (obj.material as THREE.Material).dispose();
        }
      });
      this.reticle = null;
    }
  }

  enterPlacementMode(): void {
    this.isPlacementMode = true;
    if (this.reticle) this.reticle.visible = true;
    this.lastHitPosition = null;
  }

  exitPlacementMode(): void {
    this.isPlacementMode = false;
    if (this.reticle) this.reticle.visible = false;
  }

  onPlacement(callback: PlacementCallback): void {
    this.placementCallback = callback;
  }

  onSessionEnd(callback: SessionEndCallback): void {
    this.sessionEndCallback = callback;
  }

  onController(callback: ControllerCallback): void {
    this.controllerCallback = callback;
  }

  onThumbstick(callback: ThumbstickCallback): void {
    this.thumbstickCallback = callback;
  }

  getReferenceSpace(): XRReferenceSpace | null {
    return this.referenceSpace;
  }

  processFrame(frame: XRFrame): void {
    if (!this.referenceSpace) return;

    if (this.hitTestSource && this.isPlacementMode && this.reticle) {
      if (this.leftTrackedHandSelectDepth > 0) {
        this.reticle.visible = false;
        this.lastHitPosition = null;
      } else {
        const results = frame.getHitTestResults(this.hitTestSource);
        if (results.length === 0) {
          this.reticle.visible = false;
          this.lastHitPosition = null;
        } else {
          const hit = results[0];
          const pose = hit.getPose(this.referenceSpace);
          if (pose) {
            const p = pose.transform.position;
            this.lastHitPosition = new THREE.Vector3(p.x, p.y, p.z);
            this.reticle.visible = true;
            this.reticle.position.set(p.x, p.y, p.z);
            const m = new THREE.Matrix4().fromArray(pose.transform.matrix);
            this.reticle.rotation.setFromRotationMatrix(m);
          }
        }
      }
    }

    this.pollGamepads(frame);
  }

  private pollGamepads(_frame: XRFrame): void {
    if (!this.session) return;

    for (const source of this.session.inputSources) {
      if (!source.gamepad) continue;

      const buttons = source.gamepad.buttons;
      const keyPrefix = source.handedness || 'none';

      // Button 3 = thumbstick click, Button 4 = A, Button 5 = B
      if (source.handedness === 'right' && this.edgePress(`${keyPrefix}:3`, !!buttons[3]?.pressed)) {
        this.controllerCallback?.('right_stick_click');
      }
      if (this.edgePress(`${keyPrefix}:4`, !!buttons[4]?.pressed)) {
        this.controllerCallback?.('a_button');
      }
      if (this.edgePress(`${keyPrefix}:5`, !!buttons[5]?.pressed)) {
        this.controllerCallback?.('b_button');
      }

      // Right controller thumbstick (xr-standard: axes[2]=X, axes[3]=Y)
      if (source.handedness === 'right' && this.thumbstickCallback) {
        const axes = source.gamepad.axes;
        const x = axes[2] ?? axes[0] ?? 0;
        const y = axes[3] ?? axes[1] ?? 0;
        const DEADZONE = 0.15;
        if (Math.abs(x) > DEADZONE || Math.abs(y) > DEADZONE) {
          this.thumbstickCallback(
            Math.abs(x) > DEADZONE ? x : 0,
            Math.abs(y) > DEADZONE ? y : 0,
          );
        }
      }
    }
  }

  private edgePress(key: string, isDown: boolean): boolean {
    const wasDown = this.buttonDown.get(key) ?? false;
    this.buttonDown.set(key, isDown);
    return isDown && !wasDown;
  }

  endAR(): void {
    this.session?.end();
  }

  dispose(): void {
    this.endAR();
  }
}
