// @ts-nocheck
import { randomBytes } from "node:crypto";
import WebSocket from "ws";
/**
 * WebSocket client binding: ngram AR Node gateway connects to the Python entity bridge
 * (`Entity` + `perceive`) using the ngram Presence Protocol.
 */
export class EntityBridgeBinding {
    bridgeUrl;
    token;
    arCognitionContextMarkdown;
    arSessionId;
    shellName;
    shellSlug;
    brainConfig;
    responseTimeoutMs;
    surfaceReadyEvent = null;
    ws = null;
    started = false;
    stopping = false;
    systemPrompt = "";
    connectPromise = null;
    reconnectTimer = null;
    reconnectDelay = 1000;
    heartbeatTimer = null;
    pending = new Map();
    brainPending = new Map();
    proactive;
    sessionReadyResolve = null;
    sessionReadyReject = null;
    constructor(config, ctx) {
        this.bridgeUrl = config.bridgeUrl.replace(/\/+$/, "");
        this.token = config.token?.trim() || undefined;
        this.arCognitionContextMarkdown = (config.arCognitionContextMarkdown ?? "").trim();
        this.arSessionId = ctx.sessionId;
        this.shellName = ctx.shellName;
        this.shellSlug = ctx.shellSlug;
        this.brainConfig = config.brainConfig ?? null;
        this.responseTimeoutMs = config.responseTimeoutMs ?? 600000;
    }
    onProactiveAction(callback) {
        this.proactive = callback;
    }
    async start(systemPrompt) {
        this.systemPrompt = systemPrompt;
        this.stopping = false;
        await this.ensureConnected();
    }
    async ensureConnected() {
        if (this.started && this.ws?.readyState === WebSocket.OPEN)
            return;
        if (this.connectPromise)
            return this.connectPromise;
        this.connectPromise = this.openConnection().finally(() => {
            this.connectPromise = null;
        });
        return this.connectPromise;
    }
    async openConnection() {
        if (this.stopping)
            throw new Error("binding stopped");
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        const options = this.token
            ? { headers: { Authorization: `Bearer ${this.token}` } }
            : undefined;
        const ws = new WebSocket(this.bridgeUrl, options);
        this.ws = ws;
        ws.on("message", (data) => {
            this.routeMessage(data.toString()).catch((e) => {
                console.error("[entity-bridge-binding] message error:", e);
            });
        });
        ws.on("close", () => this.handleDisconnect(ws));
        ws.on("error", (e) => {
            if (this.started)
                console.error("[entity-bridge-binding] socket error:", e);
        });
        await new Promise((resolve, reject) => {
            const t = setTimeout(() => {
                ws.close();
                reject(new Error("Entity bridge connection timeout"));
            }, 15000);
            ws.once("open", () => {
                clearTimeout(t);
                resolve();
            });
            ws.once("error", (e) => {
                clearTimeout(t);
                reject(e);
            });
        });
        if (this.ws !== ws || this.stopping)
            throw new Error("Entity bridge connection was superseded");
        this.sessionReadyResolve = null;
        this.sessionReadyReject = null;
        const readyPromise = new Promise((resolve, reject) => {
            this.sessionReadyResolve = resolve;
            this.sessionReadyReject = reject;
        });
        const startPayload = {
            type: "session.start",
            workStatus: true,
            sessionId: this.arSessionId,
            systemPrompt: this.systemPrompt,
            shellName: this.shellName,
            shellSlug: this.shellSlug,
            surfaceReady: this.surfaceReadyEvent,
        };
        if (this.arCognitionContextMarkdown) {
            startPayload["arCognitionContextMarkdown"] = this.arCognitionContextMarkdown;
        }
        ws.send(JSON.stringify(startPayload));
        const readyTimeout = setTimeout(() => {
            if (this.sessionReadyReject) {
                this.sessionReadyReject(new Error("Timed out waiting for session.ready from entity bridge"));
                this.sessionReadyReject = null;
                this.sessionReadyResolve = null;
            }
        }, 15000);
        try {
            await readyPromise;
        }
        finally {
            clearTimeout(readyTimeout);
        }
        if (this.ws !== ws || ws.readyState !== WebSocket.OPEN)
            throw new Error("Entity bridge disconnected during session start");
        if (this.brainConfig) {
            try {
                await this.sendBrainConfiguration(ws, this.brainConfig);
            }
            catch (e) {
                // Keep the bridge usable on the deployment-configured brain.
                console.error("[entity-bridge-binding] saved brain could not be restored:", e instanceof Error ? e.message : String(e));
            }
        }
        if (this.ws !== ws || ws.readyState !== WebSocket.OPEN)
            throw new Error("Entity bridge disconnected during brain configuration");
        this.started = true;
        this.reconnectDelay = 1000;
        this.startHeartbeat(ws);
    }
    startHeartbeat(ws) {
        this.stopHeartbeat();
        this.heartbeatTimer = setInterval(() => {
            if (this.ws === ws && ws.readyState === WebSocket.OPEN) {
                try {
                    ws.send(JSON.stringify({ type: "ping" }));
                }
                catch {
                    ws.close();
                }
            }
        }, 20000);
    }
    stopHeartbeat() {
        if (this.heartbeatTimer) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
    }
    handleDisconnect(ws) {
        if (this.ws !== ws)
            return;
        this.ws = null;
        this.started = false;
        this.stopHeartbeat();
        if (this.sessionReadyReject) {
            this.sessionReadyReject(new Error("Entity bridge disconnected"));
            this.sessionReadyReject = null;
            this.sessionReadyResolve = null;
        }
        for (const [, p] of this.pending) {
            clearTimeout(p.timeout);
            p.reject(new Error("Entity bridge disconnected; event delivery outcome is unknown"));
        }
        this.pending.clear();
        for (const [, p] of this.brainPending) {
            clearTimeout(p.timeout);
            p.reject(new Error("Entity bridge disconnected during brain configuration"));
        }
        this.brainPending.clear();
        // session.ready can be followed immediately by replayed activity while
        // a saved brain configuration is still being restored. Reset the
        // surface on any current-socket disconnect so that replayed state
        // cannot remain stuck merely because `started` was not set yet.
        if (!this.stopping && this.proactive) {
            try {
                this.proactive([{ type: "action:work_connection", connected: false, sessionId: this.arSessionId, timestamp: Date.now() }, {
                        type: "action:set_agent_state",
                        state: "idle",
                        sessionId: this.arSessionId,
                        timestamp: Date.now(),
                    }]);
            }
            catch {
                /* keep reconnecting even if a surface callback failed */
            }
        }
        this.scheduleReconnect();
    }
    scheduleReconnect() {
        if (this.stopping || this.reconnectTimer)
            return;
        const delay = this.reconnectDelay;
        this.reconnectDelay = Math.min(this.reconnectDelay * 2, 30000);
        this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.ensureConnected().catch(() => this.scheduleReconnect());
        }, delay);
    }
    async routeMessage(raw) {
        let msg;
        try {
            msg = JSON.parse(raw);
        }
        catch {
            return;
        }
        const t = msg["type"];
        if (t === "pong")
            return;
        if (t === "session.ready") {
            if (this.sessionReadyResolve) {
                this.sessionReadyResolve();
                this.sessionReadyResolve = null;
                this.sessionReadyReject = null;
            }
            return;
        }
        if (t === "actions") {
            const actionsIn = msg["actions"] ?? [];
            const actions = actionsIn;
            if (actions.some((action) => action.type === "action:turn_cancelled")) {
                for (const [, pending] of this.pending) {
                    clearTimeout(pending.timeout);
                    pending.resolve([]);
                }
                this.pending.clear();
            }
            const replyTo = msg["replyTo"];
            if (replyTo && this.pending.has(replyTo)) {
                const p = this.pending.get(replyTo);
                p.parts.push(...actions);
                this.pending.delete(replyTo);
                clearTimeout(p.timeout);
                p.resolve(p.parts);
                return;
            }
            if (!replyTo && this.proactive && actions.length > 0) {
                this.proactive(actions);
            }
            return;
        }
        if (t === "brain.configured") {
            const replyTo = msg["replyTo"];
            const pending = replyTo ? this.brainPending.get(replyTo) : null;
            if (!pending)
                return;
            this.brainPending.delete(replyTo);
            clearTimeout(pending.timeout);
            if (msg["ok"] === false) {
                pending.reject(new Error(String(msg["error"] ?? "Brain configuration failed")));
            }
            else {
                pending.resolve(msg["status"] ?? {});
            }
            return;
        }
        if (t === "error") {
            const errMsg = String(msg["message"] ?? "bridge error");
            if (this.sessionReadyReject && !this.started) {
                this.sessionReadyReject(new Error(errMsg));
                this.sessionReadyReject = null;
                this.sessionReadyResolve = null;
            }
            for (const [, p] of this.pending) {
                clearTimeout(p.timeout);
                p.reject(new Error(errMsg));
            }
            this.pending.clear();
        }
    }
    async stop() {
        this.stopping = true;
        this.started = false;
        this.stopHeartbeat();
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            try {
                this.ws.send(JSON.stringify({ type: "session.stop" }));
            }
            catch {
                /* ignore */
            }
        }
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
        if (this.sessionReadyReject) {
            this.sessionReadyReject(new Error("binding stopped"));
            this.sessionReadyReject = null;
            this.sessionReadyResolve = null;
        }
        for (const [, p] of this.pending) {
            clearTimeout(p.timeout);
            p.reject(new Error("binding stopped"));
        }
        this.pending.clear();
        for (const [, p] of this.brainPending) {
            clearTimeout(p.timeout);
            p.reject(new Error("binding stopped"));
        }
        this.brainPending.clear();
    }
    async handleEvent(event) {
        if (event.type === "event:shell_ready") {
            this.surfaceReadyEvent = event;
        }
        await this.ensureConnected();
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN)
            throw new Error("Entity bridge could not reconnect");
        const id = randomBytes(12).toString("hex");
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                if (this.pending.has(id)) {
                    this.pending.delete(id);
                    reject(new Error("Timed out waiting for entity bridge response"));
                }
            }, this.responseTimeoutMs);
            this.pending.set(id, { resolve, reject, parts: [], timeout });
            const payload = {
                type: "session.event",
                id,
                event: event,
            };
            try {
                this.ws.send(JSON.stringify(payload));
            }
            catch (e) {
                this.pending.delete(id);
                clearTimeout(timeout);
                reject(e instanceof Error ? e : new Error(String(e)));
                return;
            }
        });
    }
    async sendBrainConfiguration(ws, config, interrupt = false) {
        const id = randomBytes(12).toString("hex");
        return new Promise((resolve, reject) => {
            const timeout = setTimeout(() => {
                if (this.brainPending.has(id)) {
                    this.brainPending.delete(id);
                    reject(new Error("Timed out while connecting the brain provider"));
                }
            }, 60000);
            this.brainPending.set(id, { resolve, reject, timeout });
            try {
                ws.send(JSON.stringify({ type: "brain.configure", id, config, interrupt }));
            }
            catch (e) {
                this.brainPending.delete(id);
                clearTimeout(timeout);
                reject(e instanceof Error ? e : new Error(String(e)));
            }
        });
    }
    async configureInference(config) {
        const previous = this.brainConfig;
        this.brainConfig = config;
        try {
            await this.ensureConnected();
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN)
                throw new Error("Entity bridge is not connected");
            return await this.sendBrainConfiguration(this.ws, config, true);
        }
        catch (error) {
            this.brainConfig = previous;
            throw error;
        }
    }
    async injectBehaviorPrompt(prompt, context) {
        const sid = typeof context["sessionId"] === "string" ? context["sessionId"] : this.arSessionId;
        const synthetic = {
            type: "event:behavior_prompt",
            sessionId: sid,
            prompt,
            context,
        };
        return this.handleEvent(synthetic);
    }
}
