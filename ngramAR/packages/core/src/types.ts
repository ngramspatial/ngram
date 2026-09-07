// @ts-nocheck
export interface Vec3 {
    x: number;
    y: number;
    z: number;
}
export interface Quat {
    x: number;
    y: number;
    z: number;
    w: number;
}
export interface Transform {
    position: Vec3;
    rotation: Quat;
    scale?: Vec3;
}
export interface ShellDefinition {
    name: string;
    description?: string;
    /** 3D model: "default" for procedural, or path to .glb */
    model: string;
    /** World-space scale for the model */
    scale?: number;
    /** Animation pack: "standard" for built-in, or a map of state → clip names */
    animationPack: string | Record<string, string>;
    /** Active spatial behaviors */
    behaviorPack: string[];
    /** Voice configuration */
    voice: VoiceProfile;
    /** How tool calls appear in space */
    toolSurfaces?: string[];
    /** Connection to the agent intelligence */
    binding: BindingConfig;
    /** Persistent memory storage */
    memory?: MemoryConfig;
    /** Placement configuration */
    placement?: PlacementConfig;
    /** Ambient behavior configuration */
    ambient?: AmbientConfig;
    /** Appearance settings */
    appearance?: AppearanceConfig;
}
export interface PlacementConfig {
    defaultScale?: number;
    minScale?: number;
    maxScale?: number;
    anchorMode?: 'floor' | 'table' | 'floating';
}
export interface AmbientConfig {
    idleTimeout?: number;
    sleepTimeout?: number;
    ambientBehaviors?: string[];
}
export interface AppearanceConfig {
    shadowEnabled?: boolean;
    glowEnabled?: boolean;
    particlesEnabled?: boolean;
    speechBubbleStyle?: 'default' | 'minimal' | 'none';
}
export type VoiceProvider = "edge" | "openai" | "cartesia" | "elevenlabs" | "browser";
export interface VoiceProfile {
    provider: VoiceProvider | (string & {});
    voice: string;
    apiKey?: string;
    speed?: number;
    /** Cartesia model ID (default: sonic-3.6) */
    model?: string;
    /** Cartesia emotion tag (e.g. "calm", "curious", "content") */
    emotion?: string;
}
export type BindingType = "openai" | "ngram_entity";
export interface BindingConfig {
    type: BindingType | (string & {});
    model?: string;
    apiKey?: string;
    baseUrl?: string;
    /** System prompt / personality sent to the agent */
    system?: string;
    /** Extra config passed to the binding adapter */
    options?: Record<string, unknown>;
}
export interface MemoryConfig {
    path: string;
}
export type AnimationState =
    | "idle"
    | "talking"
    | "thinking"
    | "gesturing"
    | "waving"
    | "nodding"
    | "pointing"
    | "reacting"
    | "walking"
    | "appearing"
    | "disappearing"
    | "sleeping"
    | "texting"
    | "coding"
    | "enteringCode"
    | "explaining"
    | "shrugging"
    | "greeting"
    | "celebrating"
    | "cheering"
    | "clapping"
    | "dancing"
    | "no"
    | "handRaising"
    | "terrified"
    | "drunkWalk"
    | "breakdancing"
    | "twerking"
    | "macarena"
    | "hipHop"
    | "twistDance";
export type ShellMode = "active" | "ambient" | "sleep";
export interface EmbodimentState {
    transform: Transform;
    animation: AnimationState;
    gazeTarget: Vec3 | null;
    expression: string;
    expressionIntensity: number;
    isSpeaking: boolean;
    mode: ShellMode;
}
export interface SceneState {
    anchors: SceneAnchor[];
    userTransform: Transform | null;
    userGazeDirection: Vec3 | null;
    lightEstimate?: {
        intensity: number;
        direction: Vec3;
        color: {
            r: number;
            g: number;
            b: number;
        };
    };
}
export interface SceneAnchor {
    id: string;
    label: string;
    transform: Transform;
    semanticType?: string;
    dimensions?: Vec3;
}
export type PanelType = "card" | "markdown" | "code" | "image" | "video" | "chart" | "html" | "sandbox";
export interface PanelSpec {
    id: string;
    type: PanelType | (string & {});
    title?: string;
    content: string;
    position?: Vec3 | "auto";
    width?: number;
    height?: number;
    pinned?: boolean;
}
export interface SurfaceCapabilities {
    ar: boolean;
    handTracking: boolean;
    eyeTracking: boolean;
    spatialAudio: boolean;
    hitTest: boolean;
    planeDetection: boolean;
    meshDetection: boolean;
    anchors: boolean;
}
export type SessionStatus = "initializing" | "active" | "paused" | "ended";
export interface SessionInfo {
    id: string;
    shellName: string;
    status: SessionStatus;
    surfaceType: string;
    startedAt: number;
}
export type MessageRole = "user" | "agent" | "system";
export interface ConversationMessage {
    role: MessageRole;
    content: string;
    timestamp: number;
}
export type SceneObjectShape = "cube" | "sphere" | "cylinder" | "cone" | "torus" | "plane";
export type ToyType = "ball" | "bouncy_ball" | "beach_ball" | "dice" | "marble";
export type SceneObjectPosition = "here" | "left" | "right" | "above" | "front" | Vec3;
export interface SceneObjectSpec {
    objectId: string;
    shape: SceneObjectShape;
    color?: string;
    size?: number;
    position?: SceneObjectPosition;
    label?: string;
    physics?: boolean;
}
export interface SceneTextSpec {
    objectId: string;
    text: string;
    position?: SceneObjectPosition;
    size?: number;
    color?: string;
}
export interface SceneImageSpec {
    objectId: string;
    url: string;
    position?: SceneObjectPosition;
    width?: number;
}
export interface SceneToySpec {
    objectId: string;
    toyType: ToyType;
    color?: string;
    position?: SceneObjectPosition;
    impulse?: Vec3;
}
export interface SceneModelSpec {
    objectId: string;
    url: string;
    position?: SceneObjectPosition;
    scale?: number;
    rotation?: Vec3;
    physics?: boolean;
    label?: string;
}
export type AnnotationStyle = "label" | "callout" | "pin";
export interface DrawLineSpec {
    drawingId: string;
    points: Vec3[];
    color?: string;
    width?: number;
}
export interface DrawArrowSpec {
    drawingId: string;
    from: Vec3 | "agent" | "user";
    to: Vec3 | "agent" | "user";
    color?: string;
    label?: string;
}
export interface DrawAnnotationSpec {
    drawingId: string;
    position: Vec3;
    text: string;
    color?: string;
    style?: AnnotationStyle;
}
export type EnvironmentPreset = "default" | "workshop" | "cozy" | "nature" | "space" | "party" | "focus" | "night";
export type LightingMood = "warm" | "cool" | "neutral" | "dramatic";
export type ParticleType = "sparkles" | "fireflies" | "confetti" | "snow" | "embers" | "bubbles";
export interface LightingSpec {
    color?: string;
    intensity?: number;
    mood?: LightingMood;
}
export interface ParticleSpec {
    effectId: string;
    particleType: ParticleType;
    position?: Vec3 | "ambient";
    duration?: number;
    intensity?: number;
}
export type AgentProcessState = "idle" | "thinking" | "tool_running" | "planning" | "listening" | "messaging" | "error";
export interface AgentToolInfo {
    name: string;
    description?: string;
    progress?: number;
    output?: string;
}
export interface AgentSessionStart {
    type: "session.start";
    sessionId: string;
    systemPrompt: string;
    shellName: string;
}
export interface AgentSessionEvent {
    type: "session.event";
    id: string;
    sessionId: string;
    event: Record<string, unknown>;
}
export interface AgentSessionStop {
    type: "session.stop";
}
export type AgentInboundMessage = AgentSessionStart | AgentSessionEvent | AgentSessionStop | {
    type: "ping";
};
export interface AgentActionsMessage {
    type: "actions";
    actions: Record<string, unknown>[];
    replyTo?: string;
}
export interface AgentStateMessage {
    type: "state";
    state: AgentProcessState;
    tool?: AgentToolInfo;
    message?: string;
}
export type AgentOutboundMessage = AgentActionsMessage | AgentStateMessage | {
    type: "session.ready";
} | {
    type: "pong";
};
