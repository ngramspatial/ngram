// @ts-nocheck
import * as THREE from 'three';
import { loadSpatialAssets } from './spatial-design.js';
import { createScene } from './scene-setup.js';
import { ConnectionManager } from './connection.js';
import { dispatchWithReceipt } from './action-receipts.js';
import { AvatarController } from './avatar.js';
import { SpeechHandler } from './speech.js';
import { attachSlashCommands, parseSlashCommand, SLASH_COMMANDS, showCommandNotice } from './slash-commands.js';
import { captionPages } from './speech-captions.js';
import { setupVoiceSettings } from './voice-settings.js';
import { mergeVoiceDraft } from './voice-draft.js';
import { updateContextDisplay } from './context-status.js';
import { setupContextWheel } from './context-wheel.js';
import { setupResponseControl } from './response-control.js';
import type { ContextDisplayState } from './context-status.js';
import { XRManager } from './xr-manager.js';
import { setupUI } from './ui.js';
import { SpeechBubbleManager } from './speech-bubble.js';
import { PanelManager } from './panel-manager.js';
import { initPhysics } from './physics-world.js';
import { HighlightManager } from './highlight.js';
import { HandTracker } from './hand-tracker.js';
import { AmbientBehavior } from './ambient.js';
import { SpatialUI } from './spatial-ui.js';
import { RadialMenu } from './radial-menu.js';
import { WristMenu } from './wrist-menu.js';
import { AnimationPanel } from './animation-panel.js';
import { BehaviorPanel } from './behavior-panel.js';
import { AgentStateDisplay } from './agent-state.js';
import type { VisualAgentState } from './agent-state.js';
import { MusicPlayer } from './music-player.js';
import { YouTubePlayer } from './youtube-player.js';
import { TerminalViewer } from './terminal-viewer.js';
import { AppPanelManager } from './app-panel.js';
import { SpatialOverlayBridge } from './spatial-overlay-bridge.js';
import { BrowserViewer } from './browser-viewer.js';
import { BehaviorSensorManager, type SensorContext } from './behavior-sensors.js';
import { captureFrame, containsVisionTrigger } from './vision-capture.js';
import { SceneObjectManager } from './scene-object-manager.js';
import { DrawingManager } from './drawing-manager.js';
import { EnvironmentManager } from './environment-manager.js';
import { XRPointerManager } from './xr-pointer.js';
import {
  BehaviorEngine,
  LookAtUserBehavior,
  IdleBreatheBehavior,
  AnchorToSurfaceBehavior,
  ProximityGreetBehavior,
  GestureRespondBehavior,
} from '@ngram-ar/embodiment';
import type { BehaviorContext, BehaviorOutput, Behavior } from '@ngram-ar/embodiment';
import { EventBus } from './event-bus.js';
import { MessageRouter } from './message-router.js';
import { DesktopInteraction } from './desktop-interaction.js';
import { refocusOrbitOnObject } from './camera-refocus.js';
import {
  loadSceneState,
  removeLegacySceneState,
  writeSceneState,
} from './scene-state.js';
import type { SpatialContextSnapshot, SurfaceCapabilities } from '@ngram-ar/core';
import { themeOverridesEnvironment, themeUsesDarkPanels } from './theme.js';

interface SpatialAction {
  type: string;
  [key: string]: any;
}

async function main() {
  await loadSpatialAssets();
  const {
    scene,
    camera,
    renderer,
    clock,
    controls,
    desktopNavigation,
    shadowPlane,
    updateThemeBackground,
    envMap,
    lights,
  } = createScene();
  const ui = setupUI();
  const responseControl = setupResponseControl(ui.sendBtn, sendTextMessage, stopResponse);
  let contextDisplay: ContextDisplayState = {};
  function handleContextStatus(status: any): void {
    const display = updateContextDisplay(contextDisplay, status);
    contextDisplay = display.state;
    contextWheel.update(contextDisplay);
    responseControl.update({ compacting: !!contextDisplay.compacting });
    if (status.phase !== 'usage') notifyCommand(display.text);
  }
  const connection = new ConnectionManager(false);
  const contextWheel = setupContextWheel(() => connection.send({ type: 'event:context_status' }));
  const avatar = new AvatarController();
  const speech = new SpeechHandler();
  setupVoiceSettings({
    play: (options, done) => { clearResponsePlayback(); speech.playResponse(options, done); },
    stop: () => clearResponsePlayback(),
  });
  const xr = new XRManager();
  const bubbles = new SpeechBubbleManager();
  const panels = new PanelManager();
  const highlights = new HighlightManager();
  const handTracker = new HandTracker();
  const ambient = new AmbientBehavior();
  const spatialUI = new SpatialUI();
  const animPanel = new AnimationPanel();
  const behaviorPanel = new BehaviorPanel();
  const agentState = new AgentStateDisplay();
  const musicPlayer = new MusicPlayer();
  const youtubePlayer = new YouTubePlayer();
  const terminalViewer = new TerminalViewer();
  const sceneObjects = new SceneObjectManager();
  const drawings = new DrawingManager();
  const envManager = new EnvironmentManager();
  const appPanels = new AppPanelManager();
  const browserViewer = new BrowserViewer();
  const overlayBridge = new SpatialOverlayBridge();
  const xrPointers = new XRPointerManager();
  const bus = new EventBus();
  const router = new MessageRouter(bus);
  let musicLastUrl = '';
  let musicLastTitle = '';
  let musicPanelMode = false;
  const DANCE_CLIP_HINTS = [
    'dancing',
    'dance',
    'breakdanc',
    'twerk',
    'macarena',
    'hiphop',
    'twist',
    'cheering',
    'clapping',
  ];
  const isDanceLikeClip = (clipName: string): boolean => {
    const name = clipName.toLowerCase();
    return DANCE_CLIP_HINTS.some((hint) => name.includes(hint));
  };

  musicPlayer.onStateChange((state) => {
    if (state.playing && !state.paused) {
      const label = state.title || 'Audio';
      ui.addTranscript('agent', `🎵 Now playing: ${label}`);
    }
    if (musicPanelMode && overlayBridge.isActive()) {
      const title = musicLastTitle || state.title || 'Audio stream';
      const isAudible = state.playing && !state.paused;
      if (state.playing || state.paused) {
        if (overlayBridge.hasProxy('__music')) {
          overlayBridge.updateMusic(title, musicLastUrl, isAudible, state.volume);
        } else {
          overlayBridge.addMusic(title, musicLastUrl, isAudible, state.volume, avatar.getPosition());
        }
      } else if (overlayBridge.hasProxy('__music')) {
        overlayBridge.removeProxy('__music');
      }
    }

    if (musicPanelMode) {
      const activeClip = avatar.getActiveClipName();
      const danceLike = isDanceLikeClip(activeClip);
      if (state.playing && !state.paused) {
        if (!danceLike) avatar.setAnimation('dancing');
      } else if (danceLike) {
        avatar.setAnimation('idle');
      }
    }
  });

  let youtubeWasPlaying = false;
  let suppressNextYouTubeAnnouncement = false;
  youtubePlayer.onStateChange((state) => {
    const nowPlaying = state.open && state.playing;
    if (nowPlaying && !youtubeWasPlaying && !suppressNextYouTubeAnnouncement) {
      const label = state.title || state.videoId;
      ui.addTranscript('agent', `▶ YouTube: ${label}`);
    }
    if (state.open && (state.playing || state.paused)) suppressNextYouTubeAnnouncement = false;
    youtubeWasPlaying = nowPlaying;
    scheduleSceneSave();
  });

  let ytAudioFallbackTimer: ReturnType<typeof setTimeout> | null = null;
  let ytAudioFallbackRunning = false;
  let ytFallbackMode = false;
  let ytArLinkMode = false;
  let ytLastUrl = '';

  function clearYtAudioFallbackTimer(): void {
    if (ytAudioFallbackTimer) {
      clearTimeout(ytAudioFallbackTimer);
      ytAudioFallbackTimer = null;
    }
  }

  function tryYouTubeAudioFallback(videoId: string, title?: string): void {
    if (!videoId || ytAudioFallbackRunning) return;
    ytAudioFallbackRunning = true;

    // Public Invidious instances: try direct AAC stream URL (itag=140) as a pragmatic fallback.
    const candidates = [
      `https://inv.nadeko.net/latest_version?id=${encodeURIComponent(videoId)}&itag=140`,
      `https://inv.us.projectsegfau.lt/latest_version?id=${encodeURIComponent(videoId)}&itag=140`,
      `https://yewtu.be/latest_version?id=${encodeURIComponent(videoId)}&itag=140`,
    ];
    let idx = 0;

    const tryNext = () => {
      if (idx >= candidates.length) {
        ytAudioFallbackRunning = false;
        ytFallbackMode = false;
        ui.addTranscript('agent', '[YouTube audio fallback failed on all mirrors.]');
        return;
      }

      const url = candidates[idx++];
      musicPlayer.play({
        url,
        title: title ? `${title} (audio fallback)` : 'YouTube audio fallback',
        volume: 0.6,
        spatial: true,
      });

      clearYtAudioFallbackTimer();
      ytAudioFallbackTimer = setTimeout(() => {
        if (musicPlayer.isPlaying) {
          ytAudioFallbackRunning = false;
          ytFallbackMode = true;
          if (xr.isARActive) {
            spatialUI.showStatus('Playing audio fallback');
            setTimeout(() => spatialUI.hideStatus(), 3000);
          }
          return;
        }
        tryNext();
      }, 2500);
    };

    tryNext();
  }

  youtubePlayer.onError((code) => {
    const s = youtubePlayer.getState();
    let hint = 'YouTube playback failed.';
    if (code === 101 || code === 150) {
      hint = 'This video blocks embedding. Trying audio fallback mirrors…';
      tryYouTubeAudioFallback(s.videoId, s.title);
    } else if (code === 2) {
      hint = 'Invalid YouTube video ID.';
    } else if (code === 5) {
      hint = 'HTML5 playback error in this browser. Trying audio fallback…';
      tryYouTubeAudioFallback(s.videoId, s.title);
    } else if (code === 100) {
      hint = 'Video not found or private.';
    }
    ui.addTranscript('agent', `[YouTube error ${code}: ${hint}]`);
    if (xr.isARActive) {
      spatialUI.showStatus(hint);
      setTimeout(() => spatialUI.hideStatus(), 8000);
    }
  });

  const BEHAVIOR_REGISTRY: Record<string, () => Behavior> = {
    'look-at-user': () => new LookAtUserBehavior(),
    'idle-breathe': () => new IdleBreatheBehavior(),
    'anchor-to-surface': () => new AnchorToSurfaceBehavior(),
    'proximity-greet': () => new ProximityGreetBehavior(),
    'gesture-respond': () => new GestureRespondBehavior(),
  };

  let behaviorEngine: BehaviorEngine | null = null;
  let behaviorPackNames: string[] = [];
  let lastBehaviorOutput: BehaviorOutput = {};

  ui.onThemeChange((theme) => {
    envManager.setThemeEnvironmentOverride(themeOverridesEnvironment(theme));
    updateThemeBackground();
    envManager.reapplyState();
    spatialUI.setTheme(theme);
    const darkPanels = themeUsesDarkPanels(theme);
    radialMenu.setTheme(darkPanels);
    panels.setTheme(darkPanels);
    overlayBridge.setTheme(darkPanels);
  });
  envManager.setThemeEnvironmentOverride(themeOverridesEnvironment(ui.getTheme()));
  updateThemeBackground();

  panels.attach(scene);
  panels.attachDesktop(renderer, camera);
  panels.onDragStateChange = (dragging) => {
    if (controls) controls.enabled = !dragging;
  };
  await initPhysics();
  highlights.attach(scene);
  spatialUI.attach(scene);
  agentState.attach(scene);
  animPanel.attach(avatar);
  behaviorPanel.attach();
  sceneObjects.attach(scene, envMap);
  sceneObjects.attachDesktop(renderer, camera);
  sceneObjects.onDragStateChange = (dragging) => {
    if (controls) controls.enabled = !dragging;
  };
  const desktopInteraction = new DesktopInteraction(renderer, camera, sceneObjects);
  drawings.attach(scene);
  envManager.attach(scene, renderer, lights);
  overlayBridge.attach(scene);
  xrPointers.attach(scene);
  panels.setPointerManager(xrPointers);
  overlayBridge.setPointerManager(xrPointers);
  sceneObjects.setPointerManager(xrPointers);

  let agentSpawned = false;
  let agentSpawning = false;
  let agentVisible = false;
  let lastProximityBucket = -1;
  let lastDistance: number | null = null;
  let lastApproaching = false;
  let lastProximityObservedAt = 0;
  let wasLookingAtAgent = false;
  let lastGazeSendTime = 0;
  let lastUserGesture: SpatialContextSnapshot['user']['lastGesture'];
  let followTarget: string | null = null;
  let followDistance = 1.5;
  let totalElapsed = 0;
  let micActive = false;
  let pendingSpawn = false;
  let helpShownThisSession = false;
  let desktopBackground: THREE.Color | THREE.Texture | null = null;

  // ─── Durable spatial workspace ───────────────────────────────────────────
  // One atomic snapshot replaces the old collection of partial localStorage
  // keys. Loading migrates those keys so existing scenes are not lost.
  let durableSceneState = loadSceneState(localStorage);
  let sceneHydrating = true;
  let sceneDirty = false;
  let sceneSaveTimer: ReturnType<typeof setTimeout> | null = null;
  let lastForcedSceneFlushAt = 0;

  const captureCameraState = () => ({
    position: camera.position.toArray(),
    quaternion: camera.quaternion.toArray(),
    target: controls ? controls.target.toArray() : [0, 0.9, 0],
  });

  let desktopCameraState = durableSceneState.camera ?? captureCameraState();

  function captureAvatarState() {
    const group = avatar.getGroup();
    if (!agentSpawned || !group) return durableSceneState.avatar;
    return {
      position: group.position.toArray(),
      quaternion: group.quaternion.toArray(),
      scale: avatar.getScale(),
    };
  }

  function captureSceneState() {
    if (!xr.isARActive) desktopCameraState = captureCameraState();
    return {
      version: 1,
      camera: desktopCameraState,
      avatar: captureAvatarState(),
      environment: envManager.getSavedState(),
      objects: sceneObjects.getSavedState(),
      panels: panels.getSavedState(),
      drawings: drawings.getSavedState(),
      overlayPanels: {
        terminal: terminalViewer.getSavedState(),
        youtube: youtubePlayer.getSavedState(),
        browser: browserViewer.getSavedState(),
        apps: appPanels.getSavedState(),
      },
      overlayTransforms: overlayBridge.getSavedTransforms(),
    };
  }

  function flushSceneState(force = false): void {
    if (sceneHydrating) return;
    const now = Date.now();
    if (!force && !sceneDirty) return;
    // A normal debounce flush must never suppress the first lifecycle flush:
    // the camera or a live physics body may have moved since that write. Only
    // coalesce the visibility/pagehide/beforeunload burst after one forced
    // capture has succeeded, and never while another durable change is dirty.
    if (force && !sceneDirty && now - lastForcedSceneFlushAt < 250) return;
    if (sceneSaveTimer) {
      clearTimeout(sceneSaveTimer);
      sceneSaveTimer = null;
    }
    try {
      durableSceneState = writeSceneState(localStorage, captureSceneState());
      // Remove legacy records only after the complete snapshot is committed.
      try { removeLegacySceneState(localStorage); } catch {
        // Cleanup is best-effort; the unified snapshot is already durable.
      }
      sceneDirty = false;
      if (force) lastForcedSceneFlushAt = now;
    } catch (err) {
      console.warn('[scene-state] Unable to persist spatial workspace:', err);
    }
  }

  function scheduleSceneSave(): void {
    if (sceneHydrating) return;
    sceneDirty = true;
    if (sceneSaveTimer) clearTimeout(sceneSaveTimer);
    sceneSaveTimer = setTimeout(flushSceneState, 200);
  }

  sceneObjects.onPersistChange = () => {
    ui.updateSettingsObjectCount(sceneObjects.getObjectCount());
    scheduleSceneSave();
  };
  drawings.onPersistChange = scheduleSceneSave;
  panels.onPersistChange = scheduleSceneSave;
  envManager.onPersistChange = scheduleSceneSave;
  overlayBridge.onPersistChange = scheduleSceneSave;
  terminalViewer.onStateChange(scheduleSceneSave);
  browserViewer.onStateChange(scheduleSceneSave);
  appPanels.onStateChange(scheduleSceneSave);

  // Restore the desktop view before the first rendered frame. WebXR owns its
  // headset camera, so this state is never applied while an XR session runs.
  if (durableSceneState.camera) {
    camera.position.fromArray(durableSceneState.camera.position);
    if (controls) {
      controls.target.fromArray(durableSceneState.camera.target);
      controls.update();
    } else if (durableSceneState.camera.quaternion) {
      camera.quaternion.fromArray(durableSceneState.camera.quaternion);
    }
    desktopCameraState = captureCameraState();
  }

  if (durableSceneState.environment) {
    try { envManager.loadSavedState(durableSceneState.environment); } catch (err) {
      console.warn('[scene-state] Environment restore failed:', err);
    }
  }
  try { overlayBridge.loadSavedTransforms(durableSceneState.overlayTransforms); } catch (err) {
    console.warn('[scene-state] Overlay layout restore failed:', err);
  }
  const savedOverlayPanels = durableSceneState.overlayPanels ?? {};
  try { terminalViewer.loadSavedState(savedOverlayPanels.terminal); } catch (err) {
    console.warn('[scene-state] Terminal restore failed:', err);
  }
  try { appPanels.loadSavedState(savedOverlayPanels.apps); } catch (err) {
    console.warn('[scene-state] App panel restore failed:', err);
  }
  try { browserViewer.loadSavedState(savedOverlayPanels.browser); } catch (err) {
    console.warn('[scene-state] Browser panel restore failed:', err);
  }
  if (savedOverlayPanels.youtube?.open) {
    suppressNextYouTubeAnnouncement = true;
    ytLastUrl = `https://www.youtube.com/watch?v=${encodeURIComponent(savedOverlayPanels.youtube.videoId ?? '')}`;
    void youtubePlayer.loadSavedState(savedOverlayPanels.youtube).catch((err) => {
      suppressNextYouTubeAnnouncement = false;
      console.warn('[scene-state] YouTube panel restore failed:', err);
    });
  }

  const savedAvatarPos = durableSceneState.avatar?.position
    ? new THREE.Vector3().fromArray(durableSceneState.avatar.position)
    : new THREE.Vector3();
  try { sceneObjects.loadSavedState(durableSceneState.objects, savedAvatarPos); } catch (err) {
    console.warn('[scene-state] Object restore failed:', err);
  }
  try { panels.loadSavedState(durableSceneState.panels, savedAvatarPos); } catch (err) {
    console.warn('[scene-state] Panel restore failed:', err);
  }
  try { drawings.loadSavedState(durableSceneState.drawings); } catch (err) {
    console.warn('[scene-state] Drawing restore failed:', err);
  }

  ui.updateSettingsObjectCount(sceneObjects.getObjectCount());
  ui.updateSettingsEnvPreset(envManager.getCurrentPreset());
  sceneHydrating = false;

  controls?.addEventListener('end', () => {
    desktopCameraState = captureCameraState();
    scheduleSceneSave();
  });
  ui.refocusBtn.hidden = !controls;
  ui.refocusBtn.addEventListener('click', () => {
    const group = avatar.getGroup();
    if (!controls || !agentSpawned || !group) return;
    const focused = refocusOrbitOnObject(
      controls,
      group,
      avatar.getPosition(),
      avatar.getVisualHeight(),
    );
    if (!focused) return;
    desktopCameraState = captureCameraState();
    scheduleSceneSave();
  });
  window.addEventListener('pagehide', () => flushSceneState(true));
  window.addEventListener('beforeunload', () => flushSceneState(true));
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flushSceneState(true);
  });

  // Debounce for gamepad buttons (they fire every frame while held)
  let lastAButtonTime = 0;
  let lastBButtonTime = 0;
  let lastStickClickTime = 0;
  const BUTTON_DEBOUNCE = 500;
  let resizeMode = false;
  const radialMenu = new RadialMenu();
  radialMenu.attach(scene);
  const initialTheme = ui.getTheme();
  const initialDarkPanels = themeUsesDarkPanels(initialTheme);
  spatialUI.setTheme(initialTheme);
  radialMenu.setTheme(initialDarkPanels);
  panels.setTheme(initialDarkPanels);
  overlayBridge.setTheme(initialDarkPanels);

  const wristMenu = new WristMenu();
  wristMenu.attach(scene);

  let visionEnabled = localStorage.getItem('ngram_ar:vision') === 'true';

  const behaviorSensors = new BehaviorSensorManager(connection);
  let lastSpeechEndTime = 0;
  let prevSceneAnchorCount = 0;

  const repositionBtn = document.getElementById('reposition-btn') as HTMLButtonElement;
  const exitArBtn = document.getElementById('exit-ar-btn') as HTMLButtonElement;

  // Resolve XR capabilities before registering any handlers that depend on them
  const xrCaps = await xr.checkSupport();
  const isQuestBrowser = /OculusBrowser|Quest/i.test(navigator.userAgent);
  ui.showARButton(xrCaps.ar || isQuestBrowser);

  const surfaceCapabilities: SurfaceCapabilities = {
    ar: xrCaps.ar,
    handTracking: xrCaps.handTracking,
    eyeTracking: false,
    spatialAudio: true,
    hitTest: xrCaps.ar,
    planeDetection: false,
    meshDetection: false,
    anchors: false,
  };

  const rounded = (value: number): number => Number(value.toFixed(3));
  const vec = (value: THREE.Vector3) => ({
    x: rounded(value.x),
    y: rounded(value.y),
    z: rounded(value.z),
  });

  function buildSpatialContext(): SpatialContextSnapshot {
    const now = Date.now();
    const gaze = new THREE.Vector3();
    camera.getWorldDirection(gaze);
    const agentPosition = avatar.getPosition();
    const measuredDistance = agentSpawned
      ? camera.position.distanceTo(agentPosition)
      : lastDistance;
    return {
      version: '1.0',
      observedAt: now,
      surface: {
        mode: xr.isARActive ? 'ar' : 'desktop',
        capabilities: surfaceCapabilities,
      },
      user: {
        position: vec(camera.position),
        rotation: {
          x: rounded(camera.quaternion.x),
          y: rounded(camera.quaternion.y),
          z: rounded(camera.quaternion.z),
          w: rounded(camera.quaternion.w),
        },
        gazeDirection: vec(gaze),
        lookingAtAgent: wasLookingAtAgent,
        ...(lastUserGesture && now - lastUserGesture.observedAt < 30_000
          ? { lastGesture: lastUserGesture }
          : {}),
      },
      agent: {
        spawned: agentSpawned,
        visible: agentVisible,
        position: vec(agentPosition),
        distanceMeters: measuredDistance == null ? null : rounded(measuredDistance),
        animation: avatar.getActiveClipName() || 'idle',
        speaking: avatar.isSpeaking(),
        scale: rounded(avatar.getScale()),
      },
      proximity: {
        distanceMeters: measuredDistance == null ? null : rounded(measuredDistance),
        approaching: now - lastProximityObservedAt < 2_000 && lastApproaching,
      },
      scene: {
        anchors: [],
        anchorCount: 0,
        objectCount: sceneObjects.getObjectCount(),
      },
    };
  }

  function sendShellReady() {
    connection.send({
      type: 'event:shell_ready',
      shellName: 'webxr',
      capabilities: surfaceCapabilities,
      spatialContext: buildSpatialContext(),
    });
    connection.send({
      type: 'event:scene_ready',
      anchors: [],
      capabilities: surfaceCapabilities,
      spatialContext: buildSpatialContext(),
    });
  }

  let shellName = 'ngram';
  let activeShellSlug: string | null = null;

  function rebuildBehaviorEngine(packNames: string[]): void {
    behaviorPackNames = packNames;
    const enabled = behaviorPanel.getEnabledBehaviors();
    const behaviors: Behavior[] = [];
    for (const name of enabled) {
      const factory = BEHAVIOR_REGISTRY[name];
      if (factory) behaviors.push(factory());
    }
    behaviorEngine = new BehaviorEngine(behaviors);
    behaviorPanel.setBehaviors(packNames);
  }

  behaviorPanel.onChange((state) => {
    const behaviors: Behavior[] = [];
    for (const name of state.activeBehaviors) {
      if (state.disabledBehaviors.has(name)) continue;
      const factory = BEHAVIOR_REGISTRY[name];
      if (factory) behaviors.push(factory());
    }
    behaviorEngine = new BehaviorEngine(behaviors);
  });

  // Inference status is model-free and belongs to the shared Entity.
  let inferencePoll: ReturnType<typeof setInterval> | undefined;
  function queryInferenceStatus(): void {
    connection.send({ type: 'event:inference_control', command: 'status' });
    connection.send({ type: 'event:context_status' });
  }

  // --- Connection status ---
  connection.onStatusChange((status) => {
    contextWheel.setConnected(status === 'connected');
    if (status !== 'connected') {
      contextDisplay = {};
      clearResponsePlayback();
      responseControl.reset();
    }
    switch (status) {
      case 'connected':
        ui.setStatus('connected', true);
        ui.setShellInfo(shellName, 'connected', true);
        ui.addNotification(`Connected to ${shellName}`);
        sendShellReady();
        queryInferenceStatus();
        clearInterval(inferencePoll);
        inferencePoll = setInterval(queryInferenceStatus, 10000);
        break;
      case 'connecting':
        ui.setStatus('connecting…', false);
        ui.setShellInfo(shellName, 'connecting…', false);
        break;
      default:
        clearInterval(inferencePoll);
        ui.setStatus('disconnected', false);
        ui.setShellInfo(shellName, 'disconnected', false);
        ui.addNotification(`Disconnected from ${shellName}`);
        handleGoIdle();
    }
  });

  // --- Terminal sidebar toggle ---
  ui.onTerminalToggle(() => terminalViewer.toggle());

  // --- App panel interactions ---
  appPanels.onInteraction((panelId, action, data) => {
    connection.send({
      type: 'event:panel_interaction',
      panelId,
      action,
      data,
    });
  });

  browserViewer.onNavigate((url) => {
    connection.send({
      type: 'event:panel_interaction',
      panelId: '__browser',
      action: 'navigate',
      data: { url },
    });
  });

  appPanels.onClose((panelId) => {
    overlayBridge.removeProxy(panelId);
  });

  // --- Settings callbacks ---
  ui.onSettingsEnvChange((preset) => {
    envManager.setEnvironment(preset as any);
    scheduleSceneSave();
  });

  ui.onSettingsClearObjects(() => {
    sceneObjects.clearAll();
    scheduleSceneSave();
  });

  ui.onSettingsClearDrawings(() => {
    drawings.clearAll();
    scheduleSceneSave();
  });

  ui.onSettingsClearEnv(() => {
    envManager.clearEnvironment();
    ui.updateSettingsEnvPreset('default');
    scheduleSceneSave();
  });

  ui.onSettingsClearAll(() => {
    sceneObjects.clearAll();
    drawings.clearAll();
    envManager.clearEnvironment();
    ui.updateSettingsEnvPreset('default');
    scheduleSceneSave();
  });

  // --- DevTools protocol logging ---
  connection.onMessage((msg: SpatialAction) => {
    ui.devtools.logMessage('inbound', msg as unknown as Record<string, unknown>);
  });

  const originalSend = connection.send.bind(connection);
  connection.send = (msg: any) => {
    ui.devtools.logMessage('outbound', msg as Record<string, unknown>);
    originalSend(msg);
  };

  // --- Action dispatch ---
  function dispatchSpatialAction(msg: SpatialAction): void {
    const { type } = msg;
    switch (type) {
      case 'shell:config':
        if (msg.shell) {
          if (msg.shell.slug) {
            const slugChanged = activeShellSlug !== msg.shell.slug;
            activeShellSlug = msg.shell.slug;
            ui.setActiveShellSlug(msg.shell.slug);
            document.querySelectorAll('.agent-card').forEach(c => {
              c.classList.remove('loading', 'selected');
              if (c.getAttribute('data-agent') === msg.shell.slug) c.classList.add('selected');
            });
            refreshChatList();
            if (slugChanged && currentChatId) {
              loadChat(currentChatId);
            }
          }
          if (msg.shell.name) {
            shellName = msg.shell.name;
            ui.setShellInfo(shellName, 'connected', true);
          }
          avatar.configure({
            model: msg.shell.model ?? 'default',
            scale: msg.shell.scale,
            animationPack: msg.shell.animationPack ?? 'standard',
            shellSlug: msg.shell.slug,
          });
          if (!xr.isARActive) {
            const currentGroup = avatar.getGroup();
            const restoredAvatar = agentSpawned && currentGroup
              ? {
                  position: currentGroup.position.toArray(),
                  quaternion: currentGroup.quaternion.toArray(),
                  scale: avatar.getScale(),
                }
              : durableSceneState.avatar;
            const pos = restoredAvatar?.position
              ? new THREE.Vector3().fromArray(restoredAvatar.position)
              : new THREE.Vector3(0, 0, 0);
            agentSpawning = true;
            avatar.spawn(scene, pos).then(() => {
              const group = avatar.getGroup();
              if (restoredAvatar?.quaternion && group) {
                group.quaternion.fromArray(restoredAvatar.quaternion);
              } else {
                avatar.faceToward(camera.position);
              }
              if (Number.isFinite(restoredAvatar?.scale)) {
                avatar.setScale(restoredAvatar.scale);
              }
              agentSpawned = true;
              agentSpawning = false;
              agentVisible = true;
              scheduleSceneSave();
            }).catch((err) => {
              agentSpawning = false;
              console.warn('[main] Spawn on shell:config failed:', err);
            });
          }
          if (msg.shell.behaviorPack && Array.isArray(msg.shell.behaviorPack)) {
            rebuildBehaviorEngine(msg.shell.behaviorPack);
          }
          if (msg.shell.behaviors && Array.isArray(msg.shell.behaviors)) {
            behaviorSensors.configure(msg.shell.behaviors);
          }

          ui.updateSettingsObjectCount(sceneObjects.getObjectCount());
        }
        return;
      case 'action:spawn':
        handleSpawn(msg);
        break;
      case 'action:speak':
        handleSpeak(msg);
        break;
      case 'action:emote':
        avatar.setEmote(msg.emotion, msg.intensity ?? 0.7);
        break;
      case 'action:move_to':
        handleMoveTo(msg);
        break;
      case 'action:look_at':
        handleLookAt(msg);
        break;
      case 'action:face_user':
        avatar.faceToward(camera.position);
        scheduleSceneSave();
        break;
      case 'action:gesture':
        handleGesture(msg);
        break;
      case 'action:play_motion_clip':
        handleMotionClip(msg);
        break;
      case 'action:show_panel':
        handleShowPanel(msg);
        break;
      case 'action:hide_panel':
        handleHidePanel(msg);
        break;
      case 'action:set_mode':
        handleSetMode(msg);
        break;
      case 'action:follow':
        handleFollow(msg);
        break;
      case 'action:highlight':
        handleHighlight(msg);
        break;
      case 'action:go_idle':
        handleGoIdle();
        break;

      // ─── Autonomous Agent State ───────────────────────────────────────
      case 'action:set_agent_state':
        handleAgentState(msg);
        break;
      case 'action:turn_cancelled':
        if (contextDisplay.compacting) handleContextStatus({ phase: 'stopped' });
        clearResponsePlayback();
        clearMessagingIdleTimer();
        applyAgentState('idle');
        responseControl.reset();
        ui.hideBusy();
        ui.showSubtitle(shellName, (msg as any).reason === 'timeout'
          ? 'Turn stopped at the time limit.' : 'Stopped.', 2500);
        break;
      case 'action:inference_status':
        if (msg.paused) { clearResponsePlayback(); responseControl.reset(); }
        break;
      case 'action:context_status':
        handleContextStatus(msg);
        break;
      case 'action:speak_stream_start':
        handleSpeakStreamStart(msg);
        break;
      case 'action:speak_stream_delta':
        handleSpeakStreamDelta(msg);
        break;
      case 'action:speak_stream_end':
        handleSpeakStreamEnd(msg);
        break;

      // ─── Music / Audio ──────────────────────────────────────────────────
      case 'action:play_audio':
        handlePlayAudio(msg);
        break;
      case 'action:stop_audio':
        handleStopAudio();
        break;
      case 'action:pause_audio':
        handlePauseAudio(msg);
        break;
      case 'action:set_audio_volume':
        handleSetAudioVolume(msg);
        break;

      // ─── YouTube ──────────────────────────────────────────────────────────
      case 'action:play_youtube':
        handlePlayYouTube(msg);
        break;
      case 'action:control_youtube':
        handleControlYouTube(msg);
        break;

      // ─── Terminal ─────────────────────────────────────────────────────────
      case 'action:terminal_output':
        terminalViewer.write(msg);
        if (overlayBridge.isActive() && !overlayBridge.hasProxy('__terminal')) {
          overlayBridge.addTerminal(
            () => terminalViewer.getContentHTML(),
            avatar.getPosition(),
          );
        }
        break;

      // ─── Scene Objects ────────────────────────────────────────────────────
      case 'action:spawn_object':
        sceneObjects.spawnObject(msg.objectId, msg.shape, avatar.getPosition(), {
          color: msg.color, size: msg.size, position: msg.position,
          label: msg.label, physics: msg.physics,
        });
        break;
      case 'action:spawn_text':
        sceneObjects.spawnText(msg.objectId, msg.text, avatar.getPosition(), {
          position: msg.position, size: msg.size, color: msg.color,
        });
        break;
      case 'action:spawn_image':
        sceneObjects.spawnImage(msg.objectId, msg.url, avatar.getPosition(), {
          position: msg.position, width: msg.width,
        });
        break;
      case 'action:spawn_toy':
        sceneObjects.spawnToy(msg.objectId, msg.toyType, avatar.getPosition(), {
          color: msg.color, position: msg.position, impulse: msg.impulse,
        });
        break;
      case 'action:spawn_model':
        sceneObjects.spawnModel(msg.objectId, msg.url, avatar.getPosition(), {
          position: msg.position, scale: msg.scale, rotation: msg.rotation,
          physics: msg.physics, label: msg.label,
        });
        break;
      case 'action:remove_object':
        sceneObjects.remove(msg.objectId);
        break;
      case 'action:clear_objects':
        sceneObjects.clearAll();
        break;

      // ─── Drawing ──────────────────────────────────────────────────────────
      case 'action:draw_line':
        drawings.drawLine(msg.drawingId, msg.points, { color: msg.color, width: msg.width });
        break;
      case 'action:draw_arrow': {
        const fromPos = msg.from === 'agent' ? avatar.getPosition()
          : msg.from === 'user' ? camera.position.clone()
          : new THREE.Vector3(msg.from.x, msg.from.y, msg.from.z);
        const toPos = msg.to === 'agent' ? avatar.getPosition()
          : msg.to === 'user' ? camera.position.clone()
          : new THREE.Vector3(msg.to.x, msg.to.y, msg.to.z);
        drawings.drawArrow(msg.drawingId, fromPos, toPos, { color: msg.color, label: msg.label });
        break;
      }
      case 'action:draw_annotation':
        drawings.drawAnnotation(msg.drawingId, msg.position, msg.text, {
          color: msg.color, style: msg.style,
        });
        break;
      case 'action:clear_drawings':
        drawings.clearAll();
        break;

      // ─── Environment ──────────────────────────────────────────────────────
      case 'action:set_environment':
        envManager.setEnvironment(msg.preset);
        ui.updateSettingsEnvPreset(msg.preset);
        break;
      case 'action:set_lighting':
        envManager.setLighting({ color: msg.color, intensity: msg.intensity, mood: msg.mood });
        break;
      case 'action:spawn_particles':
        envManager.spawnParticles(msg.effectId, msg.particleType, {
          position: msg.position, duration: msg.duration, intensity: msg.intensity,
        });
        break;
      case 'action:clear_environment':
        envManager.clearEnvironment();
        ui.updateSettingsEnvPreset('default');
        break;

      // ─── App Panels ──────────────────────────────────────────────────────
      case 'action:update_app_panel':
        appPanels.sendData(msg.panelId, msg.data ?? {});
        if (overlayBridge.isActive() && overlayBridge.hasProxy(msg.panelId)) {
          overlayBridge.updateAppPanel(msg.panelId, '', `<pre style="font-size:11px;color:rgba(255,255,255,0.7);">${JSON.stringify(msg.data ?? {}, null, 2)}</pre>`);
        }
        break;

      // ─── Custom Background ───────────────────────────────────────────────
      case 'action:set_background':
        envManager.setBackground(msg.color, msg.gradient);
        break;

      // ─── Browser ─────────────────────────────────────────────────────────
      case 'action:open_browser': {
        const youtubeVideoId = extractYouTubeVideoId(msg.url);
        if (youtubeVideoId) {
          handlePlayYouTube({ videoId: youtubeVideoId, title: msg.title });
          break;
        }
        if (isYouTubeUrl(msg.url)) {
          const opened = window.open(msg.url, '_blank', 'noopener,noreferrer');
          if (!opened) {
            ui.addTranscript('agent', `[Open ${msg.title || 'YouTube Music'} in a new tab](${msg.url})`);
            ui.addNotification('YouTube blocks embedded pages; use the link in chat');
          }
          break;
        }
        browserViewer.open(msg.url, msg.title);
        if (overlayBridge.isActive()) {
          overlayBridge.addBrowser(msg.url, msg.title, avatar.getPosition());
        }
        break;
      }
      case 'action:control_browser': {
        const cmd = msg.command as string;
        if (cmd === 'close') {
          browserViewer.close();
          overlayBridge.removeProxy('__browser');
        } else if (cmd === 'navigate' && msg.url) {
          browserViewer.navigateTo(msg.url);
          overlayBridge.updateBrowserUrl(msg.url);
        } else if (cmd === 'back') {
          browserViewer.goBack();
        } else if (cmd === 'forward') {
          browserViewer.goForward();
        }
        break;
      }

      // ─── Vision ──────────────────────────────────────────────────────────
      case 'action:request_capture': {
        if (!visionEnabled) {
          connection.send({
            type: 'event:camera_frame',
            image: '',
            error: 'Vision is disabled by user',
            spatialContext: buildSpatialContext(),
          });
          throw new Error('Vision is disabled by user');
        }
        const image = captureFrame(renderer);
        spatialUI.showCaptureFlash(camera);
        ui.addTranscript('agent', '[Agent requested a view capture]');
        connection.send({
          type: 'event:camera_frame',
          image,
          prompt: msg.prompt ?? undefined,
          spatialContext: buildSpatialContext(),
        });
        break;
      }

      // ─── Error Handling ───────────────────────────────────────────────
      case 'action:error': {
        const code = msg.code ?? 'internal_error';
        const errMsg = msg.message ?? 'An error occurred';
        responseControl.update({ agentState: 'error' });
        console.error(`[ngram-ar] Agent error (${code}): ${errMsg}`);
        ui.addTranscript('system', `⚠ ${errMsg}`);

        // Visual feedback: briefly show concerned state then recover
        if (code === 'agent_offline') {
          agentState.setState('error');
          avatar.setEmote('concerned', 0.6);
        } else if (code === 'api_timeout') {
          agentState.setState('idle');
          avatar.setEmote('thoughtful', 0.4);
        } else {
          agentState.setState('idle');
        }
        break;
      }
      default:
        if (msg.actionId) throw new Error(`Unsupported spatial action: ${type}`);
    }

    // Also dispatch through the router for any additional listeners
    router.dispatch(msg);
  }

  connection.onMessage((msg: SpatialAction) => {
    dispatchWithReceipt(msg, () => dispatchSpatialAction(msg), (event) => connection.send(event));
  });

  // ─── Agent State Handling ──────────────────────────────────────────────────
  // Autonomous agents (via WebSocket binding) push state changes that drive
  // visual indicators — thinking animations, tool-running progress, etc.

  let currentAgentState: VisualAgentState = 'idle';
  let thinkingStartTime = 0;
  let thinkingAnimPhase = -1;
  let messagingStartedAt = 0;
  let messagingIdleTimer: ReturnType<typeof setTimeout> | null = null;
  const MESSAGING_MIN_VISIBLE_MS = 1800;

  const THINKING_PHASES: Array<{ after: number; anim: string; emote: string; intensity: number }> = [
    { after: 0,  anim: 'thinking',     emote: 'thoughtful', intensity: 0.5 },
    { after: 5,  anim: 'coding',       emote: 'attentive',  intensity: 0.6 },
    { after: 11, anim: 'explaining',   emote: 'curious',    intensity: 0.5 },
    { after: 16, anim: 'coding',       emote: 'thoughtful', intensity: 0.7 },
    { after: 22, anim: 'enteringCode', emote: 'curious',    intensity: 0.6 },
    { after: 28, anim: 'thinking',     emote: 'calm',       intensity: 0.4 },
  ];

  function updateThinkingAnim(): void {
    if (currentAgentState !== 'thinking' && currentAgentState !== 'planning' && currentAgentState !== 'tool_running') return;
    const elapsed = (Date.now() - thinkingStartTime) / 1000;
    let phase = 0;
    for (let i = THINKING_PHASES.length - 1; i >= 0; i--) {
      if (elapsed >= THINKING_PHASES[i].after) { phase = i; break; }
    }
    if (phase !== thinkingAnimPhase) {
      thinkingAnimPhase = phase;
      const p = THINKING_PHASES[phase];
      avatar.setAnimation(p.anim);
      avatar.setEmote(p.emote, p.intensity);
    }
  }

  const isThinkingAgentState = (state: VisualAgentState): boolean => (
    state === 'thinking' || state === 'planning' || state === 'tool_running'
  );

  function clearMessagingIdleTimer(): void {
    if (!messagingIdleTimer) return;
    clearTimeout(messagingIdleTimer);
    messagingIdleTimer = null;
  }

  function applyAgentState(
    state: VisualAgentState,
    tool?: { name: string; description?: string },
    message?: string,
  ): void {
    const wasThinking = isThinkingAgentState(currentAgentState);
    currentAgentState = state;
    agentState.setState(state, tool?.name, message);

    if (isThinkingAgentState(state)) {
      if (!wasThinking) {
        thinkingStartTime = Date.now();
        thinkingAnimPhase = -1;
      }
      updateThinkingAnim();

      // The 3D braille spinner already shows thinking state above the avatar.
      // Only show the DOM busy indicator for tool_running (which has useful label info).
      if (state === 'tool_running') {
        if (tool) ui.addToolTrace(tool.name, tool.description);
        ui.showBusy(tool ? `Running ${tool.name}…` : 'Working…');
      } else {
        ui.hideBusy();
      }
    } else if (state === 'messaging') {
      thinkingAnimPhase = -1;
      avatar.setAnimation('texting');
      avatar.setEmote('attentive', 0.65);
      ui.hideBusy();
    } else if (state === 'error') {
      avatar.setEmote('concerned', 0.7);
      ui.hideBusy();
      if (message) ui.addToolTrace('error', message);
    } else if (state === 'idle') {
      thinkingAnimPhase = -1;
      avatar.setAnimation('idle');
      avatar.setEmote('calm', 0.2);
      ui.hideBusy();
    }
  }

  function handleAgentState(msg: SpatialAction): void {
    const state = (msg as any).state as VisualAgentState ?? 'idle';
    // Reflect actual completion immediately, even if an avatar animation lingers.
    responseControl.update({ agentState: state });
    const tool = (msg as any).tool as { name: string; description?: string } | undefined;
    const message = (msg as any).message as string | undefined;

    if (state === 'messaging') {
      clearMessagingIdleTimer();
      if (currentAgentState !== 'messaging') messagingStartedAt = Date.now();
      applyAgentState(state, tool, message);
      return;
    }

    if (state === 'idle' && currentAgentState === 'messaging') {
      const remaining = MESSAGING_MIN_VISIBLE_MS - (Date.now() - messagingStartedAt);
      if (remaining > 0) {
        clearMessagingIdleTimer();
        messagingIdleTimer = setTimeout(() => {
          messagingIdleTimer = null;
          if (currentAgentState === 'messaging') applyAgentState('idle');
        }, remaining);
        return;
      }
    }

    clearMessagingIdleTimer();
    applyAgentState(state, tool, message);
  }

  function maintainAgentProcessAnimation(): void {
    if (currentAgentState === 'messaging') {
      // Walking owns the mixer until arrival. Reassert the process animation
      // afterward, and recover if a transient behavior interrupted it.
      if (!avatar.isWalking()) avatar.setAnimation('texting');
      return;
    }
    updateThinkingAnim();
  }

  function handleGoIdle(): void {
    responseControl.update({ agentState: 'idle' });
    clearMessagingIdleTimer();
    messagingStartedAt = 0;
    applyAgentState('idle');
    avatar.setGazeTarget(null);
    followTarget = null;
  }

  // ─── Streaming Speech ─────────────────────────────────────────────────────
  let activeStreamId: string | null = null;
  let streamedText = '';

  function pickSpeakAnim(): string {
    const r = Math.random();
    if (r < 0.45) return 'talking';
    if (r < 0.80) return 'explaining';
    return 'nodding';
  }

  function handleSpeakStreamStart(msg: SpatialAction) {
    activeStreamId = (msg as any).streamId;
    responseControl.update({ playing: true });
    streamedText = '';
    avatar.setSpeaking(true);
    avatar.setAnimation(pickSpeakAnim());
    ambient.recordInteraction();
  }

  function handleSpeakStreamDelta(msg: SpatialAction) {
    if ((msg as any).streamId !== activeStreamId) return;
    const delta = (msg as any).delta as string ?? '';
    streamedText += delta;

    if (!pendingSpeakCount) showSpeechCaption(captionPages(streamedText).at(-1)?.text ?? '');
  }

  function handleSpeakStreamEnd(msg: SpatialAction) {
    if ((msg as any).streamId !== activeStreamId) return;
    const fullText = (msg as any).fullText as string ?? streamedText;
    activeStreamId = null;
    streamedText = '';

    handleSpeak({ ...msg, text: fullText });
  }

  // --- Spawn handling ---
  // In AR: hide avatar until user places it via hit-test
  // On desktop: spawn immediately at origin

  async function handleSpawn(msg: any) {
    if (agentSpawned || agentSpawning) return;
    if (xr.isARActive) {
      pendingSpawn = true;
      if (!xr.isPlacementMode) {
        xr.enterPlacementMode();
      }
      spatialUI.showStatus('Point at the floor. Trigger or pinch to place.');
      return;
    }
    const restoredAvatar = !msg.position ? durableSceneState.avatar : undefined;
    const pos = msg.position ?? (restoredAvatar?.position
      ? { x: restoredAvatar.position[0], y: restoredAvatar.position[1], z: restoredAvatar.position[2] }
      : { x: 0, y: 0, z: 0 });
    agentSpawning = true;
    await avatar.spawn(scene, new THREE.Vector3(pos.x, pos.y, pos.z));
    const group = avatar.getGroup();
    if (restoredAvatar?.quaternion && group) group.quaternion.fromArray(restoredAvatar.quaternion);
    else avatar.faceToward(camera.position);
    if (Number.isFinite(restoredAvatar?.scale)) avatar.setScale(restoredAvatar.scale);
    agentSpawned = true;
    agentSpawning = false;
    agentVisible = true;
    scheduleSceneSave();
  }

  xr.onPlacement(async (position) => {
    if (agentSpawned) {
      avatar.moveTo(position);
      avatar.show();
    } else {
      await avatar.spawn(scene, position);
      agentSpawned = true;
    }
    agentVisible = true;
    avatar.faceToward(camera.position);
    pendingSpawn = false;
    spatialUI.showStatus('Right here with you. Squeeze grip to talk.');
    setTimeout(() => spatialUI.hideStatus(), 4000);
    savePlacement(position);
  });

  function savePlacement(_pos: THREE.Vector3) {
    scheduleSceneSave();
  }

  // --- Movement ---
  function handleMoveTo(msg: any) {
    if (!agentSpawned) return;
    const { target, speed } = msg;
    ambient.recordInteraction();

    let dest: THREE.Vector3;
    const pos = avatar.getPosition();

    if (target === 'user') {
      const dir = new THREE.Vector3().subVectors(camera.position, pos).normalize();
      dest = pos.clone().add(dir.multiplyScalar(
        Math.max(0.5, pos.distanceTo(camera.position) - 1.2)
      ));
      dest.y = pos.y;
    } else if (typeof target === 'object' && target !== null) {
      dest = new THREE.Vector3(
        pos.x + (target.x ?? 0),
        pos.y,
        pos.z + (target.z ?? 0),
      );
    } else {
      return;
    }

    avatar.walkTo(dest, speed ?? 'walk', () => {
      scheduleSceneSave();
      connection.send({
        type: 'event:action_completed',
        action: 'move_to',
        actionTimestamp: msg.timestamp ?? Date.now(),
      });
    });
  }

  function handleLookAt(msg: any) {
    const { target, weight = 1 } = msg;
    if (target === 'user') {
      avatar.setGazeTarget(camera.position, weight);
    } else if (typeof target === 'object' && target !== null) {
      avatar.setGazeTarget(new THREE.Vector3(target.x, target.y, target.z), weight);
    } else {
      avatar.setGazeTarget(null);
    }
  }

  const gestureAnimationMap: Record<string, string> = {
    wave: 'waving', greet: 'greeting', greeting: 'greeting',
    nod: 'nodding', point: 'pointing',
    shrug: 'shrugging', celebrate: 'celebrating',
    explain: 'explaining', dance: 'dancing',
    texting: 'texting', text: 'texting',
    coding: 'coding',
    enteringcode: 'enteringCode',
    entering_code: 'enteringCode',
    entercode: 'enteringCode',
    enter_code: 'enteringCode',
    thinking: 'thinking',
    no: 'no',
    handraising: 'handRaising',
    hand_raising: 'handRaising',
    handraise: 'handRaising',
    hand_raise: 'handRaising',
    terrified: 'terrified',
    drunkwalk: 'drunkWalk',
    drunk_walk: 'drunkWalk',
    breakdancing: 'breakdancing',
    breakdance: 'breakdancing',
    twerking: 'twerking',
    twerk: 'twerking',
    macarena: 'macarena',
    hiphop: 'hipHop',
    hip_hop: 'hipHop',
    twistdance: 'twistDance',
    twist_dance: 'twistDance',
    cheering: 'cheering',
    cheer: 'cheering',
    clapping: 'clapping',
    clap: 'clapping',
  };

  function shouldPreserveCurrentAnimation(): boolean {
    return isDanceLikeClip(avatar.getActiveClipName());
  }

  function handleGesture(msg: any) {
    const anim = gestureAnimationMap[msg.gesture] ?? msg.gesture;
    avatar.setAnimation(anim);
  }

  async function handleMotionClip(msg: any) {
    const name = msg.name || `generated-${msg.requestId || Date.now()}`;
    agentState.setState('tool_running', 'motion', 'Loading generated movement');
    try {
      await avatar.playMotionClip(msg.clipUrl, name, Boolean(msg.loop));
      connection.send({
        type: 'event:action_completed',
        action: 'play_motion_clip',
        completedActionId: msg.actionId,
        actionTimestamp: msg.timestamp ?? Date.now(),
      });
    } catch (error: any) {
      console.error('[motion] Failed to load generated clip:', error);
      ui.addTranscript('system', `Generated motion could not be loaded: ${error?.message ?? error}`);
    } finally {
      agentState.setState('idle');
    }
  }

  function handleSetMode(msg: any) {
    switch (msg.mode) {
      case 'active':
        avatar.setEmote('neutral', 0);
        avatar.setAnimation('idle');
        break;
      case 'ambient':
        avatar.setEmote('neutral', 0.3);
        avatar.setAnimation('idle');
        break;
      case 'sleep':
        avatar.setEmote('neutral', 0.1);
        avatar.setAnimation('idle');
        break;
    }
  }

  function handleShowPanel(msg: any) {
    const panel = msg.panel;
    if (!panel) {
      console.warn('[main] show_panel: no panel data');
      return;
    }

    const title = panel.title ?? '';
    const content = panel.content ?? '';
    const panelId = panel.id ?? `panel-${Date.now()}`;

    // App panels get routed to the DOM overlay iframe system
    if (panel.type === 'app') {
      ui.addTranscript('agent', `🖥 App: ${title || 'Launched'}`);
      appPanels.open(panelId, title, content, {
        width: panel.width ? Math.round(panel.width * 1000) : undefined,
        height: panel.height ? Math.round(panel.height * 1000) : undefined,
      });
      // In AR mode, also create a spatial proxy so content is visible
      if (overlayBridge.isActive()) {
        overlayBridge.addAppPanel(panelId, title, content, avatar.getPosition());
      }
      return;
    }

    if (!agentSpawned) {
      // Transcript is a fallback only. A live embodied shell owns panels as
      // world objects rather than duplicating them into chat.
      if (content) {
        const label = title ? `**${title}**\n${content}` : content;
        ui.addTranscript('agent', `📋 ${label}`);
      }
      return;
    }

    panels.show(
      {
        id: panelId,
        type: panel.type ?? 'card',
        title,
        content,
        position: panel.position,
        width: panel.width,
        height: panel.height,
        pinned: panel.pinned,
      },
      avatar.getPosition(),
    );
  }

  function handleHidePanel(msg: any) {
    if (msg.panelId) panels.hide(msg.panelId);
    else panels.hideAll();
  }

  function handleFollow(msg: any) {
    if (msg.target === null || msg.target === 'none') {
      followTarget = null;
      return;
    }
    followTarget = msg.target ?? 'user';
    followDistance = msg.distance ?? 1.5;
  }

  function handleHighlight(msg: any) {
    highlights.show(msg.target, msg.color, msg.duration);
  }

  // --- Music / Audio ---
  function handlePlayAudio(msg: any) {
    musicPanelMode = true;
    musicLastUrl = msg.url ?? '';
    musicLastTitle = msg.title ?? '';
    musicPlayer.play({
      url: msg.url,
      title: msg.title,
      volume: msg.volume,
      loop: msg.loop,
      spatial: msg.spatial,
    });
    if (overlayBridge.isActive()) {
      overlayBridge.addMusic(
        musicLastTitle || 'Audio stream',
        musicLastUrl,
        true,
        msg.volume ?? 0.5,
        avatar.getPosition(),
      );
    }
    avatar.setAnimation('dancing');
  }

  function handleStopAudio(): void {
    musicPlayer.stop();
    musicPanelMode = false;
    musicLastUrl = '';
    musicLastTitle = '';
    if (overlayBridge.isActive()) {
      overlayBridge.removeProxy('__music');
    }
  }

  function handlePauseAudio(msg: any): void {
    if ((msg as any).resume) musicPlayer.resume();
    else musicPlayer.pause();
    if (musicPanelMode && overlayBridge.isActive() && overlayBridge.hasProxy('__music')) {
      const state = musicPlayer.getState();
      overlayBridge.updateMusic(
        musicLastTitle || state.title || 'Audio stream',
        musicLastUrl,
        state.playing && !state.paused,
        state.volume,
      );
    }
  }

  function handleSetAudioVolume(msg: any): void {
    musicPlayer.setVolume((msg as any).volume ?? 0.5);
    if (musicPanelMode && overlayBridge.isActive() && overlayBridge.hasProxy('__music')) {
      const state = musicPlayer.getState();
      overlayBridge.updateMusic(
        musicLastTitle || state.title || 'Audio stream',
        musicLastUrl,
        state.playing && !state.paused,
        state.volume,
      );
    }
  }

  function isYouTubeUrl(rawUrl: string): boolean {
    try {
      const host = new URL(rawUrl).hostname.toLowerCase().replace(/^www\./, '');
      return host === 'youtu.be'
        || host === 'youtube.com'
        || host === 'm.youtube.com'
        || host === 'music.youtube.com'
        || host === 'youtube-nocookie.com';
    } catch {
      return false;
    }
  }

  function extractYouTubeVideoId(rawUrl: string): string | null {
    try {
      const url = new URL(rawUrl);
      const host = url.hostname.toLowerCase().replace(/^www\./, '');
      let candidate = '';
      if (host === 'youtu.be') {
        candidate = url.pathname.split('/').filter(Boolean)[0] ?? '';
      } else if (isYouTubeUrl(rawUrl)) {
        candidate = url.searchParams.get('v') ?? '';
        if (!candidate) {
          const parts = url.pathname.split('/').filter(Boolean);
          if (parts.length >= 2 && ['embed', 'shorts', 'live'].includes(parts[0])) {
            candidate = parts[1];
          }
        }
      }
      return /^[A-Za-z0-9_-]{6,32}$/.test(candidate) ? candidate : null;
    } catch {
      return null;
    }
  }

  function handlePlayYouTube(msg: any) {
    clearYtAudioFallbackTimer();
    ytAudioFallbackRunning = false;
    ytFallbackMode = false;
    ytArLinkMode = false;
    ytLastUrl = `https://www.youtube.com/watch?v=${encodeURIComponent(msg.videoId ?? '')}`;

    // In AR/headset contexts, YouTube iframe playback is frequently blocked.
    // Use a dedicated AR panel that points users to open YouTube directly.
    if (xr.isARActive) {
      youtubePlayer.stop();
      ytArLinkMode = true;
      if (overlayBridge.isActive()) {
        overlayBridge.addYouTube(msg.title ?? '', ytLastUrl, avatar.getPosition());
      }
      const opened = window.open(ytLastUrl, '_blank', 'noopener,noreferrer');
      if (opened) {
        spatialUI.showStatus('Opened YouTube in new tab');
      } else {
        spatialUI.showStatus('Use panel link: Open in New Tab');
      }
      setTimeout(() => spatialUI.hideStatus(), 6000);
      return;
    }

    void youtubePlayer.play(msg.videoId, {
      title: msg.title,
      volume: msg.volume,
      startAt: msg.startAt,
      arAudioUnlock: xr.isARActive,
    });
    if (overlayBridge.isActive()) {
      overlayBridge.addYouTube(msg.title ?? '', ytLastUrl, avatar.getPosition());
    }
    if (xr.isARActive) {
      queueMicrotask(() => {
        if (youtubePlayer.needsArAudioUnlock) {
          spatialUI.showStatus('Squeeze grip once to unmute YouTube');
          setTimeout(() => spatialUI.hideStatus(), 10000);
        }
      });
    }
  }

  function handleControlYouTube(msg: any): void {
    const cmd = (msg as any).command as 'pause' | 'resume' | 'stop' | 'seek' | 'volume';

    if (ytArLinkMode) {
      if (cmd === 'stop') {
        ytArLinkMode = false;
        if (overlayBridge.isActive()) overlayBridge.removeProxy('__youtube');
      } else if (cmd === 'resume') {
        const opened = ytLastUrl ? window.open(ytLastUrl, '_blank', 'noopener,noreferrer') : null;
        if (!opened && xr.isARActive) {
          spatialUI.showStatus('Use panel link: Open in New Tab');
          setTimeout(() => spatialUI.hideStatus(), 4000);
        }
      }
      return;
    }

    if (ytFallbackMode) {
      if (cmd === 'pause') {
        musicPlayer.pause();
        if (overlayBridge.isActive()) overlayBridge.updateYouTube(youtubePlayer.getState().title, ytLastUrl, false);
      } else if (cmd === 'resume') {
        musicPlayer.resume();
        if (overlayBridge.isActive()) overlayBridge.updateYouTube(youtubePlayer.getState().title, ytLastUrl, true);
      } else if (cmd === 'stop') {
        clearYtAudioFallbackTimer();
        ytAudioFallbackRunning = false;
        ytFallbackMode = false;
        musicPlayer.stop();
        if (overlayBridge.isActive()) overlayBridge.removeProxy('__youtube');
      } else if (cmd === 'volume') {
        const vol100 = (msg as any).volume as number | undefined;
        if (typeof vol100 === 'number') {
          musicPlayer.setVolume(Math.max(0, Math.min(1, vol100 / 100)));
        }
      }
      return;
    }

    youtubePlayer.command(cmd, {
      seekTo: (msg as any).seekTo,
      volume: (msg as any).volume,
    });
    if (cmd === 'stop') {
      overlayBridge.removeProxy('__youtube');
    } else if (cmd === 'pause') {
      overlayBridge.updateYouTube(youtubePlayer.getState().title, ytLastUrl, false);
    } else if (cmd === 'resume') {
      overlayBridge.updateYouTube(youtubePlayer.getState().title, ytLastUrl, true);
    }
  }

  // --- Speech ---
  let pendingSpeakCount = 0;

  function showSpeechCaption(text: string): void {
    if (!xr.isARActive) ui.showSubtitle(shellName, text, 0);
    if (!agentSpawned || !agentVisible) return;
    const head = avatar.getVoicePosition();
    const clearance = Math.max(0.04, avatar.getVisualHeight() * 0.15);
    if (xr.isARActive) spatialUI.showBubble(text, head, clearance, true);
    else bubbles.showAtAvatar(text, scene, head, clearance);
  }

  function handleSpeak(msg: any) {
    pendingSpeakCount++;
    responseControl.update({ playing: true });
    ambient.recordInteraction();

    if (msg.text) {
      ui.addTranscript('agent', msg.text);
    }

    const actionTimestamp = msg.timestamp ?? Date.now();

    const onEnd = (cancelled = false) => {
      pendingSpeakCount--;
      responseControl.update({ playing: pendingSpeakCount > 0 || activeStreamId !== null });
      if (!cancelled) connection.send({
        type: 'event:action_completed',
        action: 'speak',
        actionTimestamp,
      });

      if (pendingSpeakCount <= 0) {
        pendingSpeakCount = 0;
        avatar.setSpeaking(false);
        if (!shouldPreserveCurrentAnimation()) {
          avatar.setAnimation('idle');
        }
        lastSpeechEndTime = Date.now();
        ui.hideSubtitle();
        if (xr.isARActive) {
          spatialUI.hideBubble();
        } else {
          bubbles.hide(scene);
        }
      }
    };

    speech.playResponse({
      text: msg.text, audioData: msg.audioData, audioUrl: msg.audioUrl,
      speed: msg.voiceConfig?.speed, voice: msg.voiceConfig?.voice,
      onStart: () => { avatar.setSpeaking(true); avatar.setAnimation(pickSpeakAnim()); },
      onCaption: showSpeechCaption,
    }, onEnd);
  }

  function setMicAppearance(active: boolean): void {
    ui.setMicActive(active);
    spatialUI.setMicState(active, camera);
    radialMenu.setMicState(active);
    wristMenu.setMicState(active);
  }

  // --- Mic toggle (shared between DOM button and controller) ---
  async function toggleMic() {
    speech.ensureAudioContext();
    ambient.recordInteraction();

    const listeningMode = xr.isARActive ? 'recorded' : 'browser';
    const voiceInputSupported = listeningMode === 'browser'
      ? speech.browserSpeechSupported
      : speech.recordedSpeechSupported;

    if (!voiceInputSupported) {
      if (xr.isARActive) {
        spatialUI.showBubble('Microphone not available', camera.position);
      } else {
        ui.addTranscript('agent', '[Desktop voice input requires Chrome, Edge, or another Chromium-based browser.]');
      }
      return;
    }

    if (micActive) {
      speech.stopListening();
      micActive = false;
      setMicAppearance(false);
    } else {
      try {
        await speech.startListening((text) => {
          if (listeningMode === 'browser') {
            ui.textInput.value = mergeVoiceDraft(ui.textInput.value, text);
            ui.textInput.dispatchEvent(new Event('input', { bubbles: true }));
            ui.focusInput();
            ui.textInput.setSelectionRange(ui.textInput.value.length, ui.textInput.value.length);
            return;
          }

          ui.addTranscript('user', text);
          ambient.recordInteraction();

          if (visionEnabled && containsVisionTrigger(text)) {
            const image = captureFrame(renderer);
            spatialUI.showCaptureFlash(camera);
            connection.send({
              type: 'event:camera_frame',
              image,
              prompt: text,
              spatialContext: buildSpatialContext(),
            });
          } else {
            connection.send({
              type: 'event:user_speech',
              text,
              isFinal: true,
              spatialContext: buildSpatialContext(),
            });
          }
        }, listeningMode);
        micActive = true;
        setMicAppearance(true);
      } catch (err: any) {
        const errMsg = `Mic error: ${err?.message ?? err}`;
        if (xr.isARActive) {
          spatialUI.showBubble(errMsg, camera.position);
        } else {
          ui.addTranscript('agent', `[${errMsg}]`);
        }
      }
    }
  }

  // --- Mic state callback ---
  speech.onListeningState((state, detail) => {
    switch (state) {
      case 'recording':
        setMicAppearance(true);
        break;
      case 'transcribing':
        if (!xr.isARActive) break;
        if (agentSpawned && agentVisible) {
          spatialUI.showThinking(avatar.getPosition());
        } else if (agentSpawned) {
          bubbles.showThinking(scene, avatar.getPosition());
        }
        break;
      case 'idle':
        setMicAppearance(false);
        micActive = false;
        break;
      case 'error':
        setMicAppearance(false);
        micActive = false;
        if (detail) {
          if (xr.isARActive) spatialUI.showBubble(detail, camera.position);
          else ui.addTranscript('agent', `[Voice input: ${detail}]`);
        }
        break;
    }
  });

  // --- Desktop UI event listeners ---
  function notifyCommand(text: string): void { ui.addNotification(text); showCommandNotice(text); }
  function sendTextMessage() {
    const text = ui.textInput.value.trim();
    if (!text) return;
    const command = parseSlashCommand(text);
    if (command) {
      if (!command.valid) { notifyCommand('Unknown command. Type /help to see commands.'); return; }
      if (['compact', 'pause', 'resume'].includes(command.name) && !connection.isConnected()) {
        notifyCommand('Connect an agent before using this command.'); return;
      }
      if (command.name === 'stop') { stopResponse(); notifyCommand('Response stopped.'); }
      if (command.name === 'pause') {
        clearResponsePlayback();
        connection.send({ type: 'event:inference_control', command: 'pause' });
      }
      if (command.name === 'resume') connection.send({ type: 'event:inference_control', command: 'resume' });
      if (command.name === 'compact') {
        if (!contextDisplay.compacting) connection.send({ type: 'event:compact_context' });
        notifyCommand(contextDisplay.compacting ? 'Context compaction is already running.' : 'Compaction requested.');
      }
      if (command.name === 'context') notifyCommand(updateContextDisplay(contextDisplay, { phase: 'usage' }).title);
      if (command.name === 'help') notifyCommand(SLASH_COMMANDS.map(item => `/${item.name} — ${item.description}`).join('\n'));
      if (command.name === 'voice') {
        document.getElementById('settings-btn')?.click();
        document.getElementById('settings-tab-voice')?.click();
      }
      ui.textInput.value = '';
      ui.textInput.dispatchEvent(new Event('input'));
      return;
    }
    ui.addTranscript('user', text);
    ui.showSubtitle('You', text, 2500);
    ambient.recordInteraction();
    connection.send({
      type: 'event:user_speech',
      text,
      isFinal: true,
      spatialContext: buildSpatialContext(),
    });
    ui.textInput.value = '';
    ui.textInput.style.height = 'auto';
    ui.textInput.dispatchEvent(new Event('input'));
  }

  attachSlashCommands(ui.textInput, sendTextMessage);
  function clearResponsePlayback(): void {
    speech.stopPlayback();
    pendingSpeakCount = 0;
    activeStreamId = null;
    streamedText = '';
    responseControl.update({ playing: false });
    avatar.setSpeaking(false);
    if (!shouldPreserveCurrentAnimation()) avatar.setAnimation('idle');
    if (xr.isARActive) spatialUI.hideBubble();
    else bubbles.hide(scene);
    ui.hideBusy();
    ui.hideSubtitle();
  }
  function stopResponse(): void {
    clearResponsePlayback();
    responseControl.reset();
    connection.send({ type: 'event:cancel_turn' });
  }
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && responseControl.isActive()) {
      event.preventDefault();
      stopResponse();
    }
  });
  ui.textInput.addEventListener('keydown', (e) => {
    if (!e.defaultPrevented && !e.isComposing && e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendTextMessage();
    }
  });

  ui.micBtn.addEventListener('click', () => toggleMic());

  // --- Keyboard shortcuts ---
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && ui.isDrawerOpen()) {
      ui.toggleDrawer();
    }
    if ((e.key === '/' || e.key === 'k') && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      ui.focusInput();
    }
  });

  // (Agent cards are loaded dynamically from /api/shells)

  // --- Action Tester ---
  ui.onTestAction((actionType, args) => {
    const msg: SpatialAction = {
      type: `action:${actionType}`,
      timestamp: Date.now(),
      sessionId: connection.sessionId,
      ...args,
    };
    ui.devtools.logMessage('inbound', msg as unknown as Record<string, unknown>);

    switch (msg.type) {
      case 'action:speak':
        handleSpeak(msg);
        break;
      case 'action:move_to':
        handleMoveTo(msg);
        break;
      case 'action:emote':
        avatar.setEmote(msg.emotion, msg.intensity ?? 0.7);
        break;
      case 'action:gesture':
        handleGesture(msg);
        break;
      case 'action:look_at':
        handleLookAt(msg);
        break;
      case 'action:face_user':
        avatar.faceToward(camera.position);
        scheduleSceneSave();
        break;
      case 'action:go_idle':
        handleGoIdle();
        break;
      case 'action:set_mode':
        handleSetMode(msg);
        break;
      case 'action:set_agent_state':
        handleAgentState(msg);
        break;
    }
  });

  // --- Create Shell ---
  ui.onCreateAgent(async (data) => {
    try {
      const res = await fetch('/api/shells', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({ error: 'Unknown error' }));
        ui.addTranscript('agent', `[Failed to create agent: ${(err as any).error}]`);
        (window as any).__ngramArResetCreateModal?.();
        return;
      }

      const result = await res.json() as any;
      ui.addTranscript('agent', `[Shell "${result.name}" created. Pair it with: ngram ar setup <entity> --shell shells/${result.slug}]`);

      addAgentCard({ name: result.name, description: result.description ?? '', slug: result.slug, binding: result.bindingType });
      (window as any).__ngramArResetCreateModal?.();
    } catch (err: any) {
      ui.addTranscript('agent', `[Error creating agent: ${err?.message ?? err}]`);
      (window as any).__ngramArResetCreateModal?.();
    }
  });

  interface ShellCardInfo {
    name: string;
    description: string;
    slug: string;
    binding?: string;
    voice?: string;
    behaviors?: number;
  }

  function addAgentCard(info: ShellCardInfo): void {
    const agentList = document.querySelector('.agent-list');
    if (!agentList) return;

    const card = document.createElement('div');
    card.className = 'agent-card';
    card.setAttribute('data-agent', info.slug);

    const initial = info.name.charAt(0).toUpperCase();
    const subtitle = info.description || info.slug;

    const bindingLabel = info.binding === 'openai' ? 'ngram gateway' : info.binding ?? '';
    const voiceLabel = info.voice ?? '';
    const behaviorLabel = info.behaviors ? `${info.behaviors} behavior${info.behaviors > 1 ? 's' : ''}` : '';

    const tags = [bindingLabel, voiceLabel, behaviorLabel].filter(Boolean);

    card.innerHTML = `
      <div class="agent-card-header">
        <div class="agent-card-header-info">
          <span class="agent-card-name">${info.name}</span>
          <span class="agent-card-subtitle">${subtitle}</span>
        </div>
      </div>
      <div class="agent-card-detail">
        <div class="agent-card-detail-wrap">
          <div class="agent-card-detail-inner">
            <div class="agent-card-avatar">${initial}</div>
            <div class="agent-card-meta">
              <div class="agent-card-tags">${tags.map(t => `<span class="agent-card-tag">${t}</span>`).join('')}</div>
            </div>
          </div>
        </div>
      </div>
    `;

    card.addEventListener('click', () => {
      if (activeShellSlug !== info.slug) {
        document.querySelectorAll('.agent-card.loading').forEach(c => c.classList.remove('loading'));
        card.classList.add('loading');
        connection.switchShell(info.slug);
      }
    });

    agentList.appendChild(card);
  }

  // --- Browse Shells ---
  ui.onShellSelect((slug) => {
    connection.switchShell(slug);
  });

  // --- Shell Save ---
  ui.onShellSave(async (yaml) => {
    const slug = activeShellSlug;
    if (!slug) throw new Error('No active shell to save');

    const res = await fetch(`/api/shells/${encodeURIComponent(slug)}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'text/yaml' },
      body: yaml,
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ error: 'Unknown error' }));
      throw new Error((err as any).error ?? 'Save failed');
    }

    connection.reconnect();
  });

  // --- Agent Menu ---
  ui.onAgentMenu(async (action, slug) => {
    if (action === 'remove') {
      try {
        await fetch(`/api/shells/${encodeURIComponent(slug)}`, { method: 'DELETE' });
        const card = document.querySelector(`[data-agent="${slug}"]`);
        card?.remove();
        ui.addTranscript('agent', `[Agent "${slug}" removed.]`);
      } catch (err: any) {
        ui.addTranscript('agent', `[Failed to remove: ${err?.message ?? err}]`);
      }
    } else if (action === 'duplicate') {
      try {
        const res = await fetch(`/api/shells/${encodeURIComponent(slug)}`);
        if (!res.ok) return;
        const shell = await res.json() as any;
        const newData = { name: shell.name + ' Copy', voice: shell.voice?.voice };
        const createRes = await fetch('/api/shells', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(newData),
        });
        if (createRes.ok) {
          const result = await createRes.json() as any;
          addAgentCard({ name: result.name, description: result.description ?? '', slug: result.slug });
          ui.addTranscript('agent', `[Agent duplicated as "${result.name}".]`);
        }
      } catch (err: any) {
        ui.addTranscript('agent', `[Failed to duplicate: ${err?.message ?? err}]`);
      }
    } else if (action.startsWith('rename:')) {
      const newName = action.slice(7);
      try {
        const res = await fetch(`/api/shell-config?shell=${encodeURIComponent(slug)}`);
        if (!res.ok) return;
        let yaml = await res.text();
        yaml = yaml.replace(/^name:\s*.*$/m, `name: ${newName}`);
        await fetch(`/api/shells/${encodeURIComponent(slug)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'text/yaml' },
          body: yaml,
        });
        const nameEl = document.querySelector(`[data-agent="${slug}"] .agent-card-name`);
        if (nameEl) nameEl.textContent = newName;
        if (slug === activeShellSlug) {
          shellName = newName;
          ui.setShellInfo(newName, 'connected', true);
        }
        ui.addTranscript('agent', `[Agent renamed to "${newName}".]`);
      } catch (err: any) {
        ui.addTranscript('agent', `[Failed to rename: ${err?.message ?? err}]`);
      }
    }
  });

  // --- Custom Action ---
  ui.onCustomAction((action) => {
    const msg: SpatialAction = {
      type: action.type as string ?? 'action:speak',
      timestamp: Date.now(),
      sessionId: connection.sessionId,
      ...action,
    };
    ui.devtools.logMessage('inbound', msg as unknown as Record<string, unknown>);
    switch (msg.type) {
      case 'action:speak': handleSpeak(msg); break;
      case 'action:move_to': handleMoveTo(msg); break;
      case 'action:emote': avatar.setEmote(msg.emotion, msg.intensity ?? 0.7); break;
      case 'action:gesture': handleGesture(msg); break;
      case 'action:look_at': handleLookAt(msg); break;
      case 'action:face_user': avatar.faceToward(camera.position); scheduleSceneSave(); break;
      case 'action:go_idle': handleGoIdle(); break;
      case 'action:set_agent_state': handleAgentState(msg); break;
      case 'action:set_mode': handleSetMode(msg); break;
      case 'action:show_panel': handleShowPanel(msg); break;
      case 'action:highlight': handleHighlight(msg); break;
      case 'action:play_audio': handlePlayAudio(msg); break;
      case 'action:stop_audio': handleStopAudio(); break;
      case 'action:pause_audio': handlePauseAudio(msg); break;
      case 'action:set_audio_volume': handleSetAudioVolume(msg); break;
      case 'action:play_youtube': handlePlayYouTube(msg); break;
      case 'action:control_youtube': handleControlYouTube(msg); break;
      case 'action:terminal_output': terminalViewer.write(msg); break;
      case 'action:spawn_object': sceneObjects.spawnObject(msg.objectId, msg.shape, avatar.getPosition(), { color: msg.color, size: msg.size, position: msg.position, label: msg.label, physics: msg.physics }); break;
      case 'action:spawn_text': sceneObjects.spawnText(msg.objectId, msg.text, avatar.getPosition(), { position: msg.position, size: msg.size, color: msg.color }); break;
      case 'action:spawn_image': sceneObjects.spawnImage(msg.objectId, msg.url, avatar.getPosition(), { position: msg.position, width: msg.width }); break;
      case 'action:spawn_toy': sceneObjects.spawnToy(msg.objectId, msg.toyType, avatar.getPosition(), { color: msg.color, position: msg.position, impulse: msg.impulse }); break;
      case 'action:spawn_model': sceneObjects.spawnModel(msg.objectId, msg.url, avatar.getPosition(), { position: msg.position, scale: msg.scale, rotation: msg.rotation, physics: msg.physics, label: msg.label }); break;
      case 'action:remove_object': sceneObjects.remove(msg.objectId); break;
      case 'action:clear_objects': sceneObjects.clearAll(); break;
      case 'action:draw_line': drawings.drawLine(msg.drawingId, msg.points, { color: msg.color, width: msg.width }); break;
      case 'action:draw_arrow': { const f = msg.from === 'agent' ? avatar.getPosition() : msg.from === 'user' ? camera.position.clone() : new THREE.Vector3(msg.from.x, msg.from.y, msg.from.z); const t2 = msg.to === 'agent' ? avatar.getPosition() : msg.to === 'user' ? camera.position.clone() : new THREE.Vector3(msg.to.x, msg.to.y, msg.to.z); drawings.drawArrow(msg.drawingId, f, t2, { color: msg.color, label: msg.label }); break; }
      case 'action:draw_annotation': drawings.drawAnnotation(msg.drawingId, msg.position, msg.text, { color: msg.color, style: msg.style }); break;
      case 'action:clear_drawings': drawings.clearAll(); break;
      case 'action:set_environment': envManager.setEnvironment(msg.preset); ui.updateSettingsEnvPreset(msg.preset); break;
      case 'action:set_lighting': envManager.setLighting({ color: msg.color, intensity: msg.intensity, mood: msg.mood }); break;
      case 'action:spawn_particles': envManager.spawnParticles(msg.effectId, msg.particleType, { position: msg.position, duration: msg.duration, intensity: msg.intensity }); break;
      case 'action:clear_environment': envManager.clearEnvironment(); ui.updateSettingsEnvPreset('default'); break;
      case 'action:update_app_panel': appPanels.sendData(msg.panelId, msg.data ?? {}); if (overlayBridge.isActive() && overlayBridge.hasProxy(msg.panelId)) overlayBridge.updateAppPanel(msg.panelId, '', `<pre style="font-size:11px;color:rgba(255,255,255,0.7);">${JSON.stringify(msg.data ?? {}, null, 2)}</pre>`); break;
      case 'action:set_background': envManager.setBackground(msg.color, msg.gradient); break;
    }
  });

  // Load existing shells into the sidebar
  fetch('/api/shells').then((r) => r.json()).then((data: any) => {
    const shells = data?.shells ?? [];
    for (const s of shells) {
      addAgentCard({ name: s.name, description: s.description ?? '', slug: s.slug, binding: s.binding, voice: s.voice, behaviors: s.behaviors });
    }
  }).catch(() => {});

  // --- AR button (desktop) ---
  ui.arBtn.addEventListener('click', async () => {
    speech.ensureAudioContext();

    if (xr.isARActive) {
      xr.enterPlacementMode();
      spatialUI.showStatus('Point at the floor. Trigger or pinch to place.');
      return;
    }

    ui.setStatus('entering AR…', false);

    try {
      if (controls) controls.enabled = false;
      renderer.setPixelRatio(1);

      // Hide any pre-spawned avatar — we'll require fresh placement
      if (agentSpawned) {
        avatar.hide();
        agentVisible = false;
      }

      desktopBackground = scene.background;
      scene.background = null;
      const groundStage = scene.getObjectByName('ground-stage');
      if (groundStage) groundStage.visible = false;

      await xr.startAR(renderer, scene, camera);
      document.body.classList.add('ar-active');
      overlayBridge.enable();
      panels.setImmersive(true);

      // Migrate visible terminal to spatial proxy
      if (terminalViewer.isVisible) {
        overlayBridge.addTerminal(
          () => terminalViewer.getContentHTML(),
          avatar.getPosition(),
        );
      }

      const savedApps = appPanels.getSavedState();
      for (const panel of savedApps) {
        overlayBridge.addAppPanel(panel.id, panel.title, panel.htmlContent, avatar.getPosition());
      }

      const savedYouTube = youtubePlayer.getSavedState();
      if (savedYouTube?.open) {
        const url = `https://www.youtube.com/watch?v=${encodeURIComponent(savedYouTube.videoId)}`;
        overlayBridge.addYouTube(savedYouTube.title, url, avatar.getPosition());
      }

      const savedBrowser = browserViewer.getSavedState();
      if (savedBrowser?.open) {
        overlayBridge.addBrowser(savedBrowser.url, savedBrowser.title, avatar.getPosition());
      }

      // Always start in placement mode
      xr.enterPlacementMode();

      if (!helpShownThisSession) {
        helpShownThisSession = true;
        setTimeout(() => spatialUI.showHelp(camera), 500);
      }

      spatialUI.showStatus('Point at the floor. Trigger or pinch to place.');
    } catch (err: any) {
      const msg = err?.message ?? String(err);
      console.error('[xr] failed to start AR', err);
      ui.setStatus('AR failed', false);
      ui.addTranscript('agent', `[AR error: ${msg}]`);
    }
  });

  xr.onSessionEnd(() => {
    document.body.classList.remove('ar-active');
    overlayBridge.disable();
    panels.setImmersive(false);
    if (controls) controls.enabled = true;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    // Restore container-based sizing after AR
    const vc = ui.viewportContainer;
    if (vc) {
      renderer.setSize(vc.clientWidth, vc.clientHeight);
      camera.aspect = vc.clientWidth / vc.clientHeight;
      camera.updateProjectionMatrix();
    }
    if (desktopBackground !== null) {
      scene.background = desktopBackground;
    }
    const groundStage = scene.getObjectByName('ground-stage');
    if (groundStage) groundStage.visible = true;
    spatialUI.hideStatus();
    spatialUI.hideBubble();
    spatialUI.hideHelp();
    if (agentSpawned && !agentVisible) {
      avatar.show();
      agentVisible = true;
    }
  });

  // --- Controller actions ---
  async function cycleShell() {
    try {
      const res = await fetch('/api/shells');
      const data = await res.json();
      const shells = data.shells as Array<{ slug: string }>;
      if (shells.length < 2) return;
      const idx = shells.findIndex((s) => s.slug === activeShellSlug);
      const next = shells[(idx + 1) % shells.length];
      connection.switchShell(next.slug);
    } catch (e) {
      console.warn('[radial-menu] Failed to cycle shell:', e);
    }
  }

  function handleMenuAction(id: string) {
    if (id === 'reposition') {
      xr.enterPlacementMode();
      spatialUI.showStatus('Point at the floor. Trigger or pinch to place.');
    } else if (id === 'mic') {
      toggleMic();
    } else if (id === 'switch') {
      cycleShell();
    } else if (id === 'theme') {
      ui.toggleTheme();
    } else if (id === 'vision') {
      const image = captureFrame(renderer);
      spatialUI.showCaptureFlash(camera);
      ui.addTranscript('user', '[Shared current view with agent]');
      connection.send({
        type: 'event:camera_frame',
        image,
        prompt: 'The user manually shared their current view with you. Describe what you see.',
        spatialContext: buildSpatialContext(),
      });
    } else if (id === 'resize') {
      resizeMode = true;
      panels.enterResizeMode();
      spatialUI.showStatus('Stick up or down to resize your ngram. Grab a panel to resize it. A to finish.');
    } else if (id === 'terminal') {
      terminalViewer.toggle();
    }
  }

  wristMenu.onSelect((id) => {
    handleMenuAction(id);
  });

  wristMenu.onResize((delta) => {
    if (agentSpawned && agentVisible) {
      avatar.adjustScale(delta);
      scheduleSceneSave();
    }
  });

  xr.onController((action) => {
    const now = performance.now();
    switch (action) {
      case 'squeeze': {
        if (youtubePlayer.tryConsumeArAudioUnlock()) {
          spatialUI.hideStatus();
          break;
        }
        toggleMic();
        break;
      }

      case 'right_stick_click': {
        if (now - lastStickClickTime < BUTTON_DEBOUNCE) return;
        lastStickClickTime = now;
        if (resizeMode) {
          resizeMode = false;
          panels.exitResizeMode();
          spatialUI.hideStatus();
          break;
        }
        if (radialMenu.isOpen) {
          radialMenu.close();
        } else {
          if (wristMenu.isOpen) wristMenu.close();
          radialMenu.open(camera);
        }
        break;
      }

      case 'a_button': {
        if (now - lastAButtonTime < BUTTON_DEBOUNCE) return;
        lastAButtonTime = now;
        if (resizeMode) {
          resizeMode = false;
          panels.exitResizeMode();
          spatialUI.hideStatus();
          break;
        }
        if (radialMenu.isOpen) {
          const selected = radialMenu.select();
          radialMenu.close();
          if (selected) handleMenuAction(selected);
          break;
        }
        if (xr.isARActive) {
          xr.enterPlacementMode();
          spatialUI.showStatus('Point at the floor. Trigger or pinch to place.');
        }
        break;
      }

      case 'b_button':
        if (now - lastBButtonTime < BUTTON_DEBOUNCE) return;
        lastBButtonTime = now;
        if (resizeMode) {
          resizeMode = false;
          panels.exitResizeMode();
          spatialUI.hideStatus();
          break;
        }
        if (radialMenu.isOpen) {
          radialMenu.close();
          break;
        }
        xr.endAR();
        break;
    }
  });

  xr.onThumbstick((x, y) => {
    if (radialMenu.isOpen) {
      radialMenu.highlight(x, y);
    } else if (resizeMode) {
      if (Math.abs(y) > 0.15 && agentSpawned) {
        avatar.adjustScale(-y * 0.02);
        scheduleSceneSave();
      }
    }
  });

  // --- DOM AR buttons (fallback if DOM overlay works) ---
  repositionBtn.addEventListener('click', () => {
    if (xr.isARActive) {
      xr.enterPlacementMode();
      spatialUI.showStatus('Point at the floor. Trigger or pinch to place.');
    }
  });

  exitArBtn.addEventListener('click', () => {
    xr.endAR();
  });

  // --- Hand tracking ---
  handTracker.onGesture((gesture, hand, pos) => {
    ambient.recordInteraction();

    // Only send clearly intentional gestures to the agent binding
    const intentionalGestures = new Set(['thumbs_up']);
    if (!intentionalGestures.has(gesture)) return;

    lastUserGesture = {
      name: gesture,
      hand,
      position: {
        x: rounded(pos.x),
        y: rounded(pos.y),
        z: rounded(pos.z),
      },
      observedAt: Date.now(),
    };

    connection.send({
      type: 'event:user_gesture',
      gesture,
      hand,
      position: pos,
      spatialContext: buildSpatialContext(),
    });
  });

  // --- Ambient ---
  ambient.onMode((mode) => {
    connection.send({ type: 'event:mode_change', mode });
  });

  // --- Follow logic ---
  function updateFollow() {
    if (!agentSpawned || !agentVisible || !followTarget || avatar.isWalking()) return;
    const avatarPos = avatar.getPosition();
    let targetPos: THREE.Vector3;

    if (followTarget === 'user') {
      targetPos = camera.position.clone();
    } else {
      return;
    }

    const dist = avatarPos.distanceTo(targetPos);
    if (dist > followDistance + 0.5) {
      const dir = targetPos.clone().sub(avatarPos).normalize();
      const dest = targetPos.clone().sub(dir.multiplyScalar(followDistance));
      dest.y = avatarPos.y;
      avatar.walkTo(dest, 'walk');
    }
  }

  // ─── Chat Persistence ──────────────────────────────────────────────────────
  let currentChatId: string | null = localStorage.getItem('ngram_ar:chatId') ?? null;
  let chatSaveTimer: ReturnType<typeof setTimeout> | null = null;

  function scheduleChatSave(): void {
    if (chatSaveTimer) clearTimeout(chatSaveTimer);
    chatSaveTimer = setTimeout(() => saveCurrentChat(), 3000);
  }

  async function saveCurrentChat(): Promise<void> {
    if (!activeShellSlug) return;
    const messages = ui.getMessages();
    if (messages.length === 0) return;

    try {
      const res = await fetch(`/api/shells/${encodeURIComponent(activeShellSlug)}/chats`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: currentChatId, messages }),
      });
      if (res.ok) {
        const data = await res.json() as { id: string; title: string };
        if (!currentChatId) {
          currentChatId = data.id;
          localStorage.setItem('ngram_ar:chatId', data.id);
          ui.setActiveChatId(data.id);
        }
        refreshChatList();
      }
    } catch { /* silent */ }
  }

  async function refreshChatList(): Promise<void> {
    if (!activeShellSlug) return;
    try {
      const res = await fetch(`/api/shells/${encodeURIComponent(activeShellSlug)}/chats`);
      if (res.ok) {
        const data = await res.json() as { chats: Array<{ id: string; title: string; createdAt: string; messageCount: number; lastMessage?: string; shellSlug?: string; shellName?: string }> };
        ui.setChatList(data.chats);
        ui.setRecentChats(data.chats.map((c) => ({
          id: c.id,
          title: c.title,
          createdAt: c.createdAt,
          lastMessage: c.lastMessage ?? '',
          shellSlug: c.shellSlug ?? activeShellSlug,
          shellName: c.shellName ?? shellName,
        })));
      }
    } catch { /* silent */ }
  }

  async function loadChat(chatId: string): Promise<void> {
    if (!activeShellSlug) return;
    try {
      const res = await fetch(`/api/shells/${encodeURIComponent(activeShellSlug)}/chats/${chatId}`);
      if (res.ok) {
        const data = await res.json() as { id: string; messages: Array<{ role: string; text: string; time: string }> };
        currentChatId = data.id;
        localStorage.setItem('ngram_ar:chatId', data.id);
        ui.loadMessages(data.messages);
        ui.setActiveChatId(data.id);
      }
    } catch { /* silent */ }
  }

  // Auto-save after each message
  const origAddTranscript = ui.addTranscript;
  ui.addTranscript = (role: 'user' | 'agent', text: string) => {
    origAddTranscript(role, text);
    scheduleChatSave();
  };

  ui.onNewChat(() => {
    if (ui.getMessages().length > 0) {
      saveCurrentChat();
    }
    currentChatId = null;
    localStorage.removeItem('ngram_ar:chatId');
    ui.clearTranscript();
    ui.setActiveChatId(null);
    connection.reconnect();
  });

  ui.onChatSelect((chatId) => {
    if (chatId === currentChatId) return;
    if (ui.getMessages().length > 0 && currentChatId) {
      saveCurrentChat();
    }
    loadChat(chatId);
  });

  // All handlers registered — now connect the WebSocket
  connection.start();

  const audioListenerPosition = new THREE.Vector3();
  const audioListenerForward = new THREE.Vector3();
  const audioListenerUp = new THREE.Vector3();
  const audioListenerQuaternion = new THREE.Quaternion();

  // --- Render loop ---
  renderer.setAnimationLoop((_time, frame) => {
    const dt = clock.getDelta();
    totalElapsed += dt;

    const listenerCamera = xr.isARActive ? renderer.xr.getCamera(camera) : camera;
    listenerCamera.getWorldPosition(audioListenerPosition);
    listenerCamera.getWorldDirection(audioListenerForward);
    listenerCamera.getWorldQuaternion(audioListenerQuaternion);
    audioListenerUp.set(0, 1, 0).applyQuaternion(audioListenerQuaternion).normalize();
    speech.setListenerPose(
      audioListenerPosition.x,
      audioListenerPosition.y,
      audioListenerPosition.z,
      audioListenerForward.x,
      audioListenerForward.y,
      audioListenerForward.z,
      audioListenerUp.x,
      audioListenerUp.y,
      audioListenerUp.z,
    );
    musicPlayer.setListenerPose(
      audioListenerPosition.x,
      audioListenerPosition.y,
      audioListenerPosition.z,
      audioListenerForward.x,
      audioListenerForward.y,
      audioListenerForward.z,
      audioListenerUp.x,
      audioListenerUp.y,
      audioListenerUp.z,
    );

    if (agentSpawned && agentVisible) {
      avatar.update(dt);

      const agentPos = avatar.getPosition();
      const voicePos = avatar.getVoicePosition();
      speech.setSpatialPosition(voicePos.x, voicePos.y, voicePos.z);
      if (musicPlayer.isPlaying) {
        musicPlayer.setSpatialPosition(voicePos.x, voicePos.y, voicePos.z);
      }

      const sc = avatar.getScale();
      const vh = avatar.getVisualHeight();
      sceneObjects.setAvatarScale(sc);
      panels.setAvatarVisualHeight(vh);
      overlayBridge.setAvatarScale(sc);
      overlayBridge.setAnchorVisualHeight(vh);
      if (xr.isARActive) {
        spatialUI.updateBubblePosition(voicePos, Math.max(0.04, vh * 0.15));
      } else {
        bubbles.updatePosition(voicePos, Math.max(0.04, vh * 0.15));
      }

      const dist = camera.position.distanceTo(agentPos);
      const bucket = Math.round(dist * 4);
      if (bucket !== lastProximityBucket) {
        const approaching = bucket < lastProximityBucket;
        lastProximityBucket = bucket;
        lastDistance = dist;
        lastApproaching = approaching;
        lastProximityObservedAt = Date.now();
        connection.send({
          type: 'event:user_proximity',
          distance: parseFloat(dist.toFixed(3)),
          approaching,
          spatialContext: buildSpatialContext(),
        });
      }

      // Gaze detection
      const now = performance.now();
      if (now - lastGazeSendTime > 1000) {
        const dir = new THREE.Vector3();
        camera.getWorldDirection(dir);
        const raycaster = new THREE.Raycaster(camera.position, dir);
        const sphere = new THREE.Sphere(agentPos, 0.8);
        const looking = raycaster.ray.intersectsSphere(sphere, new THREE.Vector3()) !== null;

        if (looking !== wasLookingAtAgent) {
          wasLookingAtAgent = looking;
          connection.send({
            type: 'event:user_gaze',
            lookingAtAgent: looking,
            direction: vec(dir),
            spatialContext: buildSpatialContext(),
          });
          lastGazeSendTime = now;
        }
      }

      updateFollow();

      // Run the behavior engine if available, fall back to ambient behavior
      if (behaviorEngine) {
        const avatarT = avatar.getPosition();
        const ctx: BehaviorContext = {
          scene: {
            anchors: [],
            userTransform: {
              position: { x: camera.position.x, y: camera.position.y, z: camera.position.z },
              rotation: { x: camera.quaternion.x, y: camera.quaternion.y, z: camera.quaternion.z, w: camera.quaternion.w },
            },
            userGazeDirection: (() => {
              const d = new THREE.Vector3();
              camera.getWorldDirection(d);
              return { x: d.x, y: d.y, z: d.z };
            })(),
          },
          embodiment: {
            transform: {
              position: { x: avatarT.x, y: avatarT.y, z: avatarT.z },
              rotation: { x: 0, y: 0, z: 0, w: 1 },
            },
            animation: (avatar.getActiveClipName() || 'idle') as any,
            gazeTarget: null,
            expression: 'neutral',
            expressionIntensity: 0,
            isSpeaking: false,
            mode: ambient.getMode() as any,
          },
          deltaTime: dt,
          elapsedTime: totalElapsed,
        };
        lastBehaviorOutput = behaviorEngine.update(ctx);
        avatar.applyBehaviorOutput(lastBehaviorOutput);
        behaviorPanel.updateOutput(lastBehaviorOutput);
      } else {
        ambient.update(
          dt, totalElapsed, agentPos, camera.position,
          (t, w) => avatar.setGazeTarget(t, w),
          (s) => avatar.setAnimation(s),
        );
      }

      animPanel.refresh();

      agentState.update(dt, avatar.getPosition(), xr.isARActive, avatar.getScale());
      maintainAgentProcessAnimation();

      // Shadow
      if (shadowPlane) {
        shadowPlane.visible = true;
        shadowPlane.position.set(agentPos.x, agentPos.y + 0.005, agentPos.z);
      }
    }

    if (desktopNavigation?.update(dt)) {
      desktopCameraState = captureCameraState();
      scheduleSceneSave();
    }

    if (controls && controls.enabled) {
      controls.update();
    }

    highlights.update(dt, totalElapsed);

    if (agentSpawned && agentVisible) {
      const agentP = avatar.getPosition();
      const sensorCtx: SensorContext = {
        userPosition: { x: camera.position.x, y: camera.position.y, z: camera.position.z },
        userGaze: (() => { const d = new THREE.Vector3(); camera.getWorldDirection(d); return { x: d.x, y: d.y, z: d.z }; })(),
        agentPosition: { x: agentP.x, y: agentP.y, z: agentP.z },
        agentState: avatar.isSpeaking() ? 'speaking' : 'idle',
        isSpeaking: avatar.isSpeaking(),
        timeSinceLastInteraction: ambient.getTimeSinceLastInteraction(),
        timeSinceLastSpeech: lastSpeechEndTime > 0 ? (Date.now() - lastSpeechEndTime) / 1000 : 9999,
        sceneAnchorCount: 0,
        prevSceneAnchorCount: prevSceneAnchorCount,
      };
      behaviorSensors.update(sensorCtx, performance.now());
      prevSceneAnchorCount = sensorCtx.sceneAnchorCount;
    }

    if (frame && xr.isARActive) {
      xr.processFrame(frame);
      xr.animateReticle(dt);

      radialMenu.update(camera);
      spatialUI.positionMicIndicator(camera);
      spatialUI.updateMicPulse(dt, speech.getAudioLevel(), speech.getFrequencyBins());
      spatialUI.positionStatus(camera);
      spatialUI.billboardToCamera(camera);

      if (xr.getReferenceSpace()) {
        const refSpace = xr.getReferenceSpace()!;
        handTracker.processFrame(frame, refSpace);
        wristMenu.processFrame(frame, refSpace, camera);
        xrPointers.update(frame, refSpace);
        const pointers = xrPointers.getPointers();
        panels.updateAR(pointers, camera);
        sceneObjects.updateAR(pointers);
        overlayBridge.updateAR(pointers, camera);
      }
      overlayBridge.update(dt, camera);
    }

    panels.update(camera, dt);
    sceneObjects.update(dt, camera);
    drawings.update(dt, camera);
    envManager.update(dt);
    renderer.render(scene, camera);
    bubbles.render(scene, camera);
  });
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}

main().catch((err) => {
  console.error('[ngram-ar] init failed', err);
  const sub = document.getElementById('subtitle-text');
  if (sub) {
    sub.textContent = `Init error: ${err?.message ?? err}`;
    document.getElementById('subtitle')?.classList.add('visible');
  }
});
