// @ts-nocheck
import type { Vec3, PanelSpec, ShellMode, SurfaceCapabilities, SceneAnchor, AgentProcessState, AgentToolInfo, SceneObjectShape, SceneObjectPosition, ToyType, AnnotationStyle, EnvironmentPreset, LightingMood, ParticleType } from "./types.js";
export interface ProtocolMessage {
    type: string;
    timestamp: number;
    sessionId: string;
    /** Unique ID for correlating actions with completion events. */
    actionId?: string;
}
export interface SpeakAction extends ProtocolMessage {
    type: "action:speak";
    text: string;
    /** Pre-synthesized audio (base64). If absent, shell uses its voice profile. */
    audioData?: string;
    /** Browser speech choices supplied by the gateway; never includes credentials. */
    voiceConfig?: { voice?: string; speed?: number };
    visemes?: [number, number, number][];
}
export interface EmoteAction extends ProtocolMessage {
    type: "action:emote";
    emotion: string;
    intensity?: number;
    duration?: number;
}
export interface MoveToAction extends ProtocolMessage {
    type: "action:move_to";
    /** Named anchor ("desk", "user", "window") or coordinates */
    target: string | Vec3;
    speed?: "walk" | "fast" | "instant";
}
export interface LookAtAction extends ProtocolMessage {
    type: "action:look_at";
    target: string | Vec3;
    /** 0–1 blend weight */
    weight?: number;
}
export type GestureType = "wave" | "greet" | "nod" | "point" | "shrug" | "celebrate" | "explain" | "dance" | "texting" | "coding" | "enteringCode" | "thinking" | "no" | "handRaising" | "terrified" | "drunkWalk" | "breakdancing" | "twerking" | "macarena" | "hipHop" | "twistDance" | "cheering" | "clapping";
export interface GestureAction extends ProtocolMessage {
    type: "action:gesture";
    gesture: GestureType | (string & {});
}
export interface ShowPanelAction extends ProtocolMessage {
    type: "action:show_panel";
    panel: PanelSpec;
}
export interface HidePanelAction extends ProtocolMessage {
    type: "action:hide_panel";
    panelId: string;
}
export interface SetModeAction extends ProtocolMessage {
    type: "action:set_mode";
    mode: ShellMode;
}
export interface FollowAction extends ProtocolMessage {
    type: "action:follow";
    target: string;
    distance?: number;
}
export interface HighlightAction extends ProtocolMessage {
    type: "action:highlight";
    target: string;
    color?: string;
    duration?: number;
}
export interface GoIdleAction extends ProtocolMessage {
    type: "action:go_idle";
}
export interface SpawnAction extends ProtocolMessage {
    type: "action:spawn";
    position?: Vec3;
}
export interface SetAgentStateAction extends ProtocolMessage {
    type: "action:set_agent_state";
    state: AgentProcessState;
    tool?: AgentToolInfo;
    message?: string;
}
export interface SpeakStreamStartAction extends ProtocolMessage {
    type: "action:speak_stream_start";
    streamId: string;
}
export interface SpeakStreamDeltaAction extends ProtocolMessage {
    type: "action:speak_stream_delta";
    streamId: string;
    delta: string;
}
export interface SpeakStreamEndAction extends ProtocolMessage {
    type: "action:speak_stream_end";
    streamId: string;
    fullText: string;
    audioData?: string;
    visemes?: [number, number, number][];
}
export interface SpawnObjectAction extends ProtocolMessage {
    type: "action:spawn_object";
    objectId: string;
    shape: SceneObjectShape;
    color?: string;
    size?: number;
    position?: SceneObjectPosition;
    label?: string;
    physics?: boolean;
}
export interface SpawnTextAction extends ProtocolMessage {
    type: "action:spawn_text";
    objectId: string;
    text: string;
    position?: SceneObjectPosition;
    size?: number;
    color?: string;
}
export interface SpawnImageAction extends ProtocolMessage {
    type: "action:spawn_image";
    objectId: string;
    url: string;
    position?: SceneObjectPosition;
    width?: number;
}
export interface SpawnToyAction extends ProtocolMessage {
    type: "action:spawn_toy";
    objectId: string;
    toyType: ToyType;
    color?: string;
    position?: SceneObjectPosition;
    impulse?: Vec3;
}
export interface RemoveObjectAction extends ProtocolMessage {
    type: "action:remove_object";
    objectId: string;
}
export interface ClearObjectsAction extends ProtocolMessage {
    type: "action:clear_objects";
}
export interface DrawLineAction extends ProtocolMessage {
    type: "action:draw_line";
    drawingId: string;
    points: Vec3[];
    color?: string;
    width?: number;
}
export interface DrawArrowAction extends ProtocolMessage {
    type: "action:draw_arrow";
    drawingId: string;
    from: Vec3 | "agent" | "user";
    to: Vec3 | "agent" | "user";
    color?: string;
    label?: string;
}
export interface DrawAnnotationAction extends ProtocolMessage {
    type: "action:draw_annotation";
    drawingId: string;
    position: Vec3;
    text: string;
    color?: string;
    style?: AnnotationStyle;
}
export interface ClearDrawingsAction extends ProtocolMessage {
    type: "action:clear_drawings";
}
export interface SetEnvironmentAction extends ProtocolMessage {
    type: "action:set_environment";
    preset: EnvironmentPreset;
}
export interface SetLightingAction extends ProtocolMessage {
    type: "action:set_lighting";
    color?: string;
    intensity?: number;
    mood?: LightingMood;
}
export interface SpawnParticlesAction extends ProtocolMessage {
    type: "action:spawn_particles";
    effectId: string;
    particleType: ParticleType;
    position?: Vec3 | "ambient";
    duration?: number;
    intensity?: number;
}
export interface ClearEnvironmentAction extends ProtocolMessage {
    type: "action:clear_environment";
}
export interface SpawnModelAction extends ProtocolMessage {
    type: "action:spawn_model";
    objectId: string;
    url: string;
    position?: SceneObjectPosition;
    scale?: number;
    rotation?: {
        x: number;
        y: number;
        z: number;
    };
    physics?: boolean;
    label?: string;
}
export interface UpdateAppPanelAction extends ProtocolMessage {
    type: "action:update_app_panel";
    panelId: string;
    data: Record<string, unknown>;
}
export interface SetBackgroundAction extends ProtocolMessage {
    type: "action:set_background";
    color?: string;
    gradient?: {
        center: string;
        edge: string;
    };
}
export interface PlayAudioAction extends ProtocolMessage {
    type: "action:play_audio";
    url: string;
    /** Optional human-readable title (song name, etc.) */
    title?: string;
    /** 0–1 volume. Default 0.5 */
    volume?: number;
    /** Loop playback. Default false */
    loop?: boolean;
    /** If true, audio is spatialized at the agent's position */
    spatial?: boolean;
}
export interface StopAudioAction extends ProtocolMessage {
    type: "action:stop_audio";
}
export interface PauseAudioAction extends ProtocolMessage {
    type: "action:pause_audio";
    /** If true, resume playback. If false/absent, pause. */
    resume?: boolean;
}
export interface SetAudioVolumeAction extends ProtocolMessage {
    type: "action:set_audio_volume";
    /** 0–1 */
    volume: number;
}
export interface PlayYouTubeAction extends ProtocolMessage {
    type: "action:play_youtube";
    videoId: string;
    title?: string;
    /** 0–100 volume (YouTube API uses 0–100) */
    volume?: number;
    /** Start time in seconds */
    startAt?: number;
}
export interface ControlYouTubeAction extends ProtocolMessage {
    type: "action:control_youtube";
    command: "pause" | "resume" | "stop" | "seek" | "volume";
    /** Seek target in seconds */
    seekTo?: number;
    /** 0–100 */
    volume?: number;
}
export interface OpenBrowserAction extends ProtocolMessage {
    type: "action:open_browser";
    url: string;
    title?: string;
}
export interface ControlBrowserAction extends ProtocolMessage {
    type: "action:control_browser";
    command: "close" | "navigate" | "back" | "forward";
    url?: string;
}
export interface GenerateMotionAction extends ProtocolMessage {
    type: "action:generate_motion";
    requestId: string;
    prompt: string;
    durationSeconds?: number;
    constraints?: {
        rootTarget?: "stationary" | "user" | "forward" | "left" | "right";
        waypoints?: Vec3[];
    };
    loop?: boolean;
}
export interface PlayMotionClipAction extends ProtocolMessage {
    type: "action:play_motion_clip";
    requestId: string;
    /** Signed or public clip URL returned by the server-side motion provider. */
    clipUrl: string;
    format: "fbx" | "glb" | "gltf";
    name?: string;
    loop?: boolean;
}
export type ErrorCode = "agent_offline" | "api_timeout" | "api_error" | "invalid_response" | "binding_error" | "internal_error";
export interface ErrorAction extends ProtocolMessage {
    type: "action:error";
    code: ErrorCode;
    message: string;
    /** Hint for the surface: how long to wait before retrying (ms). */
    retryAfterMs?: number;
}
export interface TerminalOutputAction extends ProtocolMessage {
    type: "action:terminal_output";
    /** Shell command that was run (shown as `$ command`) */
    command?: string;
    /** Command / tool output text */
    output?: string;
    /** Tool name that produced this output */
    tool?: string;
    /** If true, marks this entry as an error */
    error?: boolean;
    /** If true, clear all previous terminal content */
    clear?: boolean;
}
export type SpatialAction = InferenceStatusAction | ContextStatusAction | TurnCancelledAction | SpeakAction | EmoteAction | MoveToAction | LookAtAction | GestureAction | ShowPanelAction | HidePanelAction | SetModeAction | FollowAction | HighlightAction | GoIdleAction | SpawnAction | SetAgentStateAction | SpeakStreamStartAction | SpeakStreamDeltaAction | SpeakStreamEndAction | SpawnObjectAction | SpawnTextAction | SpawnImageAction | SpawnToyAction | RemoveObjectAction | ClearObjectsAction | DrawLineAction | DrawArrowAction | DrawAnnotationAction | ClearDrawingsAction | SetEnvironmentAction | SetLightingAction | SpawnParticlesAction | ClearEnvironmentAction | PlayAudioAction | StopAudioAction | PauseAudioAction | SetAudioVolumeAction | PlayYouTubeAction | ControlYouTubeAction | TerminalOutputAction | SpawnModelAction | UpdateAppPanelAction | SetBackgroundAction | OpenBrowserAction | ControlBrowserAction | GenerateMotionAction | PlayMotionClipAction | ErrorAction | RequestCaptureAction;
export interface SpatialContextSnapshot {
    version: "1.0";
    observedAt: number;
    surface: {
        mode: "desktop" | "ar" | "vr";
        capabilities: SurfaceCapabilities;
    };
    user: {
        position: Vec3;
        rotation: { x: number; y: number; z: number; w: number };
        gazeDirection: Vec3;
        lookingAtAgent: boolean;
        lastGesture?: {
            name: string;
            hand: "left" | "right";
            position: Vec3;
            observedAt: number;
        };
    };
    agent: {
        spawned: boolean;
        visible: boolean;
        position: Vec3;
        distanceMeters: number | null;
        animation: string;
        speaking: boolean;
        scale: number;
    };
    proximity: {
        distanceMeters: number | null;
        approaching: boolean;
    };
    scene: {
        anchors: SceneAnchor[];
        anchorCount: number;
        objectCount: number;
    };
}
export interface UserSpeechEvent extends ProtocolMessage {
    type: "event:user_speech";
    text: string;
    isFinal: boolean;
    confidence?: number;
    spatialContext?: SpatialContextSnapshot;
}
export interface CancelTurnEvent extends ProtocolMessage {
    type: "event:cancel_turn";
}
export interface InferenceControlEvent extends ProtocolMessage {
    type: 'event:inference_control';
    command: 'pause' | 'resume' | 'status';
}
export interface InferenceStatusAction extends ProtocolMessage {
    type: 'action:inference_status';
    paused: boolean;
}
export interface CompactContextEvent extends ProtocolMessage {
  type: 'event:compact_context';
}
export interface ContextStatusAction extends ProtocolMessage {
  type: 'action:context_status';
  phase: 'usage' | 'compacting' | 'compacted' | 'failed' | 'unchanged';
  source?: string;
  automatic?: boolean;
  estimatedTokens?: number;
  inputBudgetTokens?: number;
  messagesBefore?: number;
  messagesAfter?: number;
}
export interface TurnCancelledAction extends ProtocolMessage {
    type: "action:turn_cancelled";
    reason: "stopped" | "timeout";
}
export interface UserProximityEvent extends ProtocolMessage {
    type: "event:user_proximity";
    distance: number;
    approaching: boolean;
}
export interface UserGestureEvent extends ProtocolMessage {
    type: "event:user_gesture";
    gesture: string;
    hand: "left" | "right";
    position: Vec3;
}
export interface UserGazeEvent extends ProtocolMessage {
    type: "event:user_gaze";
    lookingAtAgent: boolean;
    direction?: Vec3;
}
export interface SceneReadyEvent extends ProtocolMessage {
    type: "event:scene_ready";
    anchors: SceneAnchor[];
    capabilities: SurfaceCapabilities;
}
export interface SceneUpdateEvent extends ProtocolMessage {
    type: "event:scene_update";
    anchors: SceneAnchor[];
}
export interface ActionCompletedEvent extends ProtocolMessage {
    type: "event:action_completed";
    action: string;
    /** The actionId of the completed action, for correlation. */
    completedActionId?: string;
    /** Accepted means execution was started, not that an async action finished. */
    status?: "accepted" | "completed" | "failed";
    error?: string;
    actionTimestamp: number;
}
export interface ShellReadyEvent extends ProtocolMessage {
    type: "event:shell_ready";
    shellName: string;
    capabilities: SurfaceCapabilities;
}
export interface BehaviorTriggerEvent extends ProtocolMessage {
    type: "event:behavior_trigger";
    behaviorId: string;
    triggerType: string;
    context: Record<string, unknown>;
}
export interface PanelInteractionEvent extends ProtocolMessage {
    type: "event:panel_interaction";
    panelId: string;
    action: string;
    data?: Record<string, unknown>;
}
export type ShellEvent = InferenceControlEvent | CompactContextEvent | CancelTurnEvent | UserSpeechEvent | UserProximityEvent | UserGestureEvent | UserGazeEvent | SceneReadyEvent | SceneUpdateEvent | ActionCompletedEvent | ShellReadyEvent | BehaviorTriggerEvent | PanelInteractionEvent | CameraFrameEvent;
/** Surface → Gateway → Agent: a captured camera frame from the user's view. */
export interface CameraFrameEvent extends ProtocolMessage {
    type: "event:camera_frame";
    /** Base64 JPEG data URI (data:image/jpeg;base64,...) */
    image: string;
    /** Optional user prompt accompanying the image */
    prompt?: string;
    /** Present when the surface could not capture a frame. */
    error?: string;
    spatialContext?: SpatialContextSnapshot;
}
/** Agent → Gateway → Surface: request the surface to capture and send a frame. */
export interface RequestCaptureAction extends ProtocolMessage {
    type: "action:request_capture";
    /** Optional prompt to include when the frame is returned */
    prompt?: string;
}
export type NgramArWireMessage = SpatialAction | ShellEvent;

// ─── Spatial Action API ──────────────────────────────────────────────────────
// The structured vocabulary through which agents express intent in space.
// The agent decides WHAT it wants to do. The shell decides HOW.
//
// Two flows:
//   Binding → Shell:  "spatial actions"  (agent intentions)
//   Shell → Binding:  "shell events"     (what's happening in the world)
//
// All messages are JSON over WebSocket.
// ─── Protocol Version ────────────────────────────────────────────────────────
// Bump MINOR for additive changes, MAJOR for breaking changes.
export const PROTOCOL_VERSION = "1.1";
// ─── Helpers ─────────────────────────────────────────────────────────────────
export function createAction(type, sessionId, data) {
    return {
        type,
        timestamp: Date.now(),
        sessionId,
        actionId: crypto.randomUUID(),
        ...data,
    };
}
export function createEvent(type, sessionId, data) {
    return { type, timestamp: Date.now(), sessionId, ...data };
}
export function isAction(msg) {
    return msg.type.startsWith("action:");
}
export function isEvent(msg) {
    return msg.type.startsWith("event:");
}
