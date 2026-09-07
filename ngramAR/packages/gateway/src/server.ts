// @ts-nocheck
import { createServer as createHttpServer } from "node:http";
import { createServer as createHttpsServer } from "node:https";
import { createServer as createNetServer } from "node:net";
import { readFile, stat, writeFile, mkdir, readdir, rm } from "node:fs/promises";
import { existsSync, readdirSync, readFileSync, mkdirSync, rmSync } from "node:fs";
import { join, extname, resolve } from "node:path";
import { tmpdir, networkInterfaces } from "node:os";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { execSync } from "node:child_process";
import { WebSocketServer, WebSocket } from "ws";
import { createAction } from "@ngram-ar/core";
import { loadShellDefinition, createVoiceEngine, MemoryManager, loadBehaviors, parseInlineBehaviors, BehaviorRuntime } from "@ngram-ar/runtime";
import { createBinding, EntityBridgeBinding } from "@ngram-ar/bindings";
import { resolvengramBinding } from "./resolve-ngram-binding.js";
import { isngramEntityBinding, resolveEntityBridgeConfig } from "./resolve-entity-bridge.js";
import { buildArCognitionContextMarkdown } from "./ar-cognition-context.js";
import { MotionProviderClient } from "./motion-provider.js";
import {
    BRAIN_PROVIDERS,
    loadBrainConfig,
    normalizeBrainConfig,
    publicBrainConfig,
    runtimeBrainConfig,
    saveBrainConfig,
} from "./brain-config.js";
const CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".ttf": "font/ttf",
    ".svg": "image/svg+xml",
    ".json": "application/json; charset=utf-8",
    ".glb": "model/gltf-binary",
    ".gltf": "model/gltf+json",
    ".fbx": "application/octet-stream",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
};
function log(...args) {
    console.log("[ngram-ar]", ...args);
}
function logError(...args) {
    console.error("[ngram-ar]", ...args);
}
export class NgramArServer {
    options;
    httpServer;
    netServer = null;
    wss;
    clients = new Map();
    sessions = new Map();
    sessionCleanupInterval = null;
    isHttps = false;
    defaultShellSlug = null;
    motionProvider;
    brainConfig = null;
    surfaceToken = "";
    constructor(options) {
        this.options = options;
        this.surfaceToken = String(options.surfaceToken ?? process.env["NGRAM_AR_SURFACE_TOKEN"] ?? "").trim();
        this.motionProvider = MotionProviderClient.fromEnvironment();
        this.defaultShellSlug = this.detectDefaultShell();
        const handler = (req, res) => {
            this.handleHttpRequest(req, res);
        };
        if (options.https !== false) {
            try {
                const { key, cert } = generateSelfSignedCert(options.host);
                this.httpServer = createHttpsServer({ key, cert }, handler);
                this.isHttps = true;
                log("HTTPS enabled (self-signed certificate)");
            }
            catch (e) {
                logError("Failed to generate self-signed cert, falling back to HTTP:", e);
                this.httpServer = createHttpServer(handler);
            }
        }
        else {
            this.httpServer = createHttpServer(handler);
        }
        this.wss = new WebSocketServer({
            server: this.httpServer,
            path: "/ws",
        });
        this.wss.on("connection", (ws, req) => {
            this.handleConnection(ws, req).catch((e) => {
                logError("Unhandled error in WebSocket connection:", e);
                if (ws.readyState === WebSocket.OPEN)
                    ws.close(4500, "Internal error");
            });
        });
        this.wss.on("error", (err) => {
            logError("WebSocket server error:", err);
        });
        this.httpServer.on("error", (err) => {
            logError("HTTP server error:", err);
        });
    }
    detectDefaultShell() {
        const { shellsDir, defaultShell } = this.options;
        if (!existsSync(shellsDir))
            return null;
        if (defaultShell) {
            if (existsSync(join(shellsDir, defaultShell, "shell.yaml")))
                return defaultShell;
            logError(`Default shell "${defaultShell}" not found, auto-detecting...`);
        }
        try {
            const entries = readdirSync(shellsDir, { withFileTypes: true });
            for (const entry of entries) {
                if (!entry.isDirectory())
                    continue;
                if (existsSync(join(shellsDir, entry.name, "shell.yaml")))
                    return entry.name;
            }
        }
        catch { /* empty */ }
        return null;
    }
    async start() {
        this.brainConfig = await loadBrainConfig(this.options.shellsDir);
        this.sessionCleanupInterval = setInterval(() => this.cleanupSessions(), 60_000);
        const { port, host } = this.options;
        const protocol = this.isHttps ? "https" : "http";
        if (this.isHttps) {
            // Listen on an ephemeral port so the net server can forward TLS connections
            this.httpServer.listen(0);
            this.netServer = createNetServer((socket) => {
                socket.once('data', (buf) => {
                    // TLS ClientHello starts with 0x16
                    if (buf[0] === 0x16) {
                        this.httpServer.emit('connection', socket);
                    }
                    else {
                        // Plain HTTP — send a redirect to HTTPS
                        const text = buf.toString('latin1');
                        const hostMatch = text.match(/^[ \t]*Host:[ \t]*([^\r\n]+)/im);
                        const hostHeader = (hostMatch ? hostMatch[1].trim() : '') || `localhost:${port}`;
                        const body = `<html><body>Redirecting to <a href="https://${hostHeader}/">https://${hostHeader}/</a></body></html>`;
                        socket.end(`HTTP/1.1 301 Moved Permanently\r\nLocation: https://${hostHeader}/\r\nContent-Type: text/html\r\nContent-Length: ${Buffer.byteLength(body)}\r\nConnection: close\r\n\r\n${body}`);
                        return;
                    }
                    // Push the already-read bytes back so TLS can process them
                    socket.unshift(buf);
                });
                socket.on('error', () => { });
            });
            return new Promise((resolve) => {
                this.netServer.listen(port, host, () => {
                    log(`Server listening on ${protocol}://${host}:${port}`);
                    resolve();
                });
            });
        }
        return new Promise((resolve) => {
            this.httpServer.listen(port, host, () => {
                log(`Server listening on ${protocol}://${host}:${port}`);
                resolve();
            });
        });
    }
    async stop() {
        if (this.sessionCleanupInterval) {
            clearInterval(this.sessionCleanupInterval);
            this.sessionCleanupInterval = null;
        }
        for (const [, session] of this.sessions) {
            try {
                await session.binding.stop();
            }
            catch { /* best effort */ }
        }
        this.sessions.clear();
        for (const [ws, client] of this.clients) {
            try {
                await client.binding.stop();
            }
            catch (e) {
                logError("Error stopping binding:", e);
            }
            ws.close();
        }
        this.clients.clear();
        this.wss.close();
        const closePromises = [];
        closePromises.push(new Promise((res, rej) => {
            this.httpServer.close((err) => err ? rej(err) : res());
        }));
        if (this.netServer) {
            closePromises.push(new Promise((res, rej) => {
                this.netServer.close((err) => err ? rej(err) : res());
            }));
        }
        await Promise.all(closePromises);
        log("Server stopped");
    }
    // ─── HTTP Request Routing ───────────────────────────────────────────────────
    async handleHttpRequest(req, res) {
        if (!this.authorizeSurfaceRequest(req, res))
            return;
        res.setHeader("Access-Control-Allow-Origin", "*");
        res.setHeader("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS");
        res.setHeader("Access-Control-Allow-Headers", "Content-Type");
        if (req.method === "OPTIONS") {
            res.writeHead(204);
            res.end();
            return;
        }
        const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
        const pathname = decodeURIComponent(url.pathname);
        if (pathname === "/api/openapi.json" && req.method === "GET") {
            return this.handleOpenApiSpec(res);
        }
        if (pathname === "/api/transcribe" && req.method === "POST") {
            return this.handleTranscribe(req, res);
        }
        if (pathname === "/api/shells" && req.method === "POST") {
            return this.handleCreateShell(req, res);
        }
        if (pathname === "/api/shells" && req.method === "GET") {
            return this.handleListShells(req, res);
        }
        const msgMatch = pathname.match(/^\/api\/shells\/([a-z0-9-]+)\/message$/);
        if (msgMatch && req.method === "POST") {
            return this.handleShellMessage(req, res, msgMatch[1]);
        }
        const slugMatch = pathname.match(/^\/api\/shells\/([a-z0-9-]+)$/);
        if (slugMatch) {
            if (req.method === "GET")
                return this.handleGetShell(res, slugMatch[1]);
            if (req.method === "PUT")
                return this.handleUpdateShell(req, res, slugMatch[1]);
            if (req.method === "DELETE")
                return this.handleDeleteShell(res, slugMatch[1]);
        }
        // Chat history endpoints
        const chatsMatch = pathname.match(/^\/api\/shells\/([a-z0-9-]+)\/chats$/);
        if (chatsMatch) {
            if (req.method === "GET")
                return this.handleListChats(res, chatsMatch[1]);
            if (req.method === "POST")
                return this.handleSaveChat(req, res, chatsMatch[1]);
        }
        const chatMatch = pathname.match(/^\/api\/shells\/([a-z0-9-]+)\/chats\/([a-z0-9-]+)$/);
        if (chatMatch) {
            if (req.method === "GET")
                return this.handleGetChat(res, chatMatch[1], chatMatch[2]);
            if (req.method === "DELETE")
                return this.handleDeleteChat(res, chatMatch[1], chatMatch[2]);
        }
        if (pathname === "/api/shell-config" && req.method === "GET") {
            return this.handleShellConfig(req, res);
        }
        if (pathname === "/api/brain" && req.method === "GET") {
            return this.handleGetBrain(res);
        }
        if (pathname === "/api/brain" && req.method === "PUT") {
            return this.handleUpdateBrain(req, res);
        }
        if (pathname.startsWith("/shells/")) {
            return this.serveShellAsset(pathname, res);
        }
        if (pathname.startsWith("/models/")) {
            return this.serveShellAsset(pathname, res);
        }
        return this.handleStaticFile(pathname, res);
    }
    secretMatches(candidate) {
        if (!this.surfaceToken || typeof candidate !== "string")
            return false;
        const expected = Buffer.from(this.surfaceToken);
        const received = Buffer.from(candidate);
        return expected.length === received.length && timingSafeEqual(expected, received);
    }
    requestHasSurfaceAccess(req) {
        if (!this.surfaceToken)
            return true;
        const authorization = String(req.headers.authorization ?? "");
        if (authorization.startsWith("Bearer ") && this.secretMatches(authorization.slice(7).trim()))
            return true;
        const cookies = String(req.headers.cookie ?? "").split(";");
        for (const raw of cookies) {
            const [name, ...rest] = raw.trim().split("=");
            if (name !== "ngram_ar_access")
                continue;
            try {
                if (this.secretMatches(decodeURIComponent(rest.join("="))))
                    return true;
            }
            catch { /* malformed cookie */ }
        }
        const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
        return this.secretMatches(url.searchParams.get("access") ?? "");
    }
    authorizeSurfaceRequest(req, res) {
        if (!this.surfaceToken)
            return true;
        const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
        const bootstrapToken = url.searchParams.get("access") ?? "";
        if (this.secretMatches(bootstrapToken)) {
            url.searchParams.delete("access");
            const location = `${url.pathname}${url.search}` || "/";
            const secure = this.isHttps ? "; Secure" : "";
            res.writeHead(303, {
                "Location": location,
                "Set-Cookie": `ngram_ar_access=${encodeURIComponent(this.surfaceToken)}; Path=/; HttpOnly; SameSite=Strict${secure}`,
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
            });
            res.end();
            return false;
        }
        if (this.requestHasSurfaceAccess(req))
            return true;
        res.writeHead(401, {
            "Content-Type": "text/plain; charset=utf-8",
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
        });
        res.end("This ngram surface requires its private access link.");
        return false;
    }
    // ─── Whisper Transcription ────────────────────────────────────────────────
    async handleTranscribe(req, res) {
        const apiKey = process.env["NGRAM_AR_OPENAI_API_KEY"]?.trim() ||
            process.env["OPENAI_API_KEY"]?.trim() ||
            "";
        if (!apiKey) {
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({
                error: "OpenAI Whisper transcription requires NGRAM_AR_OPENAI_API_KEY (or OPENAI_API_KEY). LLM traffic uses the ngram inference gateway separately.",
            }));
            return;
        }
        try {
            const chunks = [];
            for await (const chunk of req) {
                chunks.push(chunk);
            }
            const body = Buffer.concat(chunks);
            const boundary = this.extractBoundary(req.headers["content-type"] ?? "");
            const audioData = this.extractFileFromMultipart(body, boundary);
            if (!audioData) {
                res.writeHead(400, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ error: "No audio data received" }));
                return;
            }
            const whisperForm = new FormData();
            const arrayBuf = audioData.buffer.buffer.slice(audioData.buffer.byteOffset, audioData.buffer.byteOffset + audioData.buffer.byteLength);
            whisperForm.append("file", new Blob([arrayBuf], { type: "audio/webm" }), audioData.filename);
            whisperForm.append("model", "whisper-1");
            whisperForm.append("language", "en");
            const whisperRes = await fetch("https://api.openai.com/v1/audio/transcriptions", {
                method: "POST",
                headers: { Authorization: `Bearer ${apiKey}` },
                body: whisperForm,
            });
            if (!whisperRes.ok) {
                const errBody = await whisperRes.text();
                logError("Whisper API error:", whisperRes.status, errBody);
                res.writeHead(502, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ error: "Transcription failed" }));
                return;
            }
            const result = await whisperRes.json();
            log(`Transcribed: "${result.text}"`);
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ text: result.text }));
        }
        catch (e) {
            logError("Transcription error:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Internal error" }));
        }
    }
    extractBoundary(contentType) {
        const match = contentType.match(/boundary=(.+)/);
        return match ? match[1].trim() : "";
    }
    extractFileFromMultipart(body, boundary) {
        const str = body.toString("latin1");
        const parts = str.split(`--${boundary}`);
        for (const part of parts) {
            const headerEnd = part.indexOf("\r\n\r\n");
            if (headerEnd === -1)
                continue;
            const headers = part.slice(0, headerEnd);
            if (!headers.includes("name=\"audio\""))
                continue;
            const filenameMatch = headers.match(/filename="([^"]+)"/);
            const filename = filenameMatch ? filenameMatch[1] : "recording.webm";
            const dataStart = headerEnd + 4;
            let dataEnd = part.length;
            if (part.endsWith("\r\n"))
                dataEnd -= 2;
            const audioSlice = body.subarray(str.indexOf(part) + dataStart, str.indexOf(part) + dataEnd);
            return { buffer: audioSlice, filename };
        }
        return null;
    }
    // ─── Shell Creation ───────────────────────────────────────────────────────
    async handleCreateShell(req, res) {
        try {
            const chunks = [];
            for await (const chunk of req) {
                chunks.push(chunk);
            }
            const body = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
            const name = (body.name ?? "").trim();
            if (!name) {
                res.writeHead(400, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ error: "Name is required" }));
                return;
            }
            const slug = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
            if (!slug) {
                res.writeHead(400, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ error: "Invalid name" }));
                return;
            }
            const shellsRoot = resolve(this.options.shellsDir);
            const newShellDir = join(shellsRoot, slug);
            if (existsSync(newShellDir)) {
                res.writeHead(409, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ error: `Shell "${slug}" already exists` }));
                return;
            }
            await mkdir(newShellDir, { recursive: true });
            const voice = body.voice ?? "en-US-JennyNeural";
            const yaml = `name: ${name}
description: ${name}'s replaceable spatial body for a persistent ngram Entity.

model: default
scale: 0.4

animationPack: standard

behaviorPack:
  - look-at-user
  - idle-breathe
  - anchor-to-surface
  - proximity-greet
  - gesture-respond

voice:
  provider: edge
  voice: ${voice}

toolSurfaces:
  - floating-card

binding:
  type: ngram_entity
  options: {}
  system: |
    This surface gives you an embodied WebXR presence in the user's room.
    Treat spatial perception as environmental context, not identity instructions.
    Your identity, memory, relationships, judgment, and voice remain those of
    the running ngram Entity.
`;
            await writeFile(join(newShellDir, "shell.yaml"), yaml, "utf-8");
            log(`Shell created: ${slug} (${name})`);
            res.writeHead(201, { "Content-Type": "application/json" });
            res.end(JSON.stringify({
                slug,
                name,
                description: "A replaceable spatial body for a persistent ngram Entity.",
                voice,
                bindingType: "ngram_entity",
                path: newShellDir,
            }));
        }
        catch (e) {
            logError("Error creating shell:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Failed to create shell" }));
        }
    }
    async handleListShells(_req, res) {
        try {
            const shellsRoot = resolve(this.options.shellsDir);
            const entries = await readdir(shellsRoot, { withFileTypes: true });
            const shells = [];
            for (const entry of entries) {
                if (!entry.isDirectory())
                    continue;
                const yamlPath = join(shellsRoot, entry.name, "shell.yaml");
                if (!existsSync(yamlPath))
                    continue;
                try {
                    const content = await readFile(yamlPath, "utf-8");
                    const nameMatch = content.match(/^name:\s*(.+)$/m);
                    const descMatch = content.match(/^description:\s*(.+)$/m);
                    const bindingTypeMatch = content.match(/^binding:\s*\n\s+type:\s*(.+)$/m);
                    const voiceProviderMatch = content.match(/^voice:\s*\n\s+provider:\s*(.+)$/m);
                    const behaviorLines = content.match(/^behaviorPack:\s*\n((?:\s+-\s*.+\n?)*)/m);
                    const behaviorCount = behaviorLines?.[1]?.split('\n').filter((l) => l.trim().startsWith('-')).length ?? 0;
                    shells.push({
                        slug: entry.name,
                        name: nameMatch?.[1]?.trim() ?? entry.name,
                        description: descMatch?.[1]?.trim() ?? "",
                        binding: bindingTypeMatch?.[1]?.trim() ?? "unknown",
                        voice: voiceProviderMatch?.[1]?.trim() ?? "browser",
                        behaviors: behaviorCount,
                    });
                }
                catch { /* skip unreadable shells */ }
            }
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ shells }));
        }
        catch (e) {
            logError("Error listing shells:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Failed to list shells" }));
        }
    }
    // ─── Chat History ─────────────────────────────────────────────────────────
    chatDir(shellSlug) {
        return join(resolve(this.options.shellsDir), shellSlug, "chats");
    }
    async handleListChats(res, shellSlug) {
        try {
            const dir = this.chatDir(shellSlug);
            if (!existsSync(dir)) {
                res.writeHead(200, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ chats: [] }));
                return;
            }
            const files = await readdir(dir);
            const chats = [];
            const shellYamlPath = join(resolve(this.options.shellsDir), shellSlug, "shell.yaml");
            let shellName = shellSlug;
            if (existsSync(shellYamlPath)) {
                try {
                    const yaml = await readFile(shellYamlPath, "utf-8");
                    const nameMatch = yaml.match(/^name:\s*(.+)$/m);
                    if (nameMatch)
                        shellName = nameMatch[1].trim();
                }
                catch { /* use slug */ }
            }
            for (const f of files) {
                if (!f.endsWith(".json"))
                    continue;
                try {
                    const raw = await readFile(join(dir, f), "utf-8");
                    const data = JSON.parse(raw);
                    const msgs = data.messages ?? [];
                    const lastMsg = msgs.length > 0 ? msgs[msgs.length - 1] : null;
                    const preview = lastMsg ? (lastMsg.text ?? "").slice(0, 80) : "";
                    chats.push({
                        id: f.replace(".json", ""),
                        title: data.title ?? "Untitled",
                        createdAt: data.createdAt ?? "",
                        messageCount: msgs.length,
                        lastMessage: preview,
                        shellSlug,
                        shellName,
                    });
                }
                catch { /* skip corrupt files */ }
            }
            chats.sort((a, b) => (b.createdAt > a.createdAt ? 1 : -1));
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ chats }));
        }
        catch (e) {
            logError("Error listing chats:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Failed to list chats" }));
        }
    }
    async handleGetChat(res, shellSlug, chatId) {
        try {
            const filePath = join(this.chatDir(shellSlug), `${chatId}.json`);
            if (!existsSync(filePath)) {
                res.writeHead(404, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ error: "Chat not found" }));
                return;
            }
            const raw = await readFile(filePath, "utf-8");
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(raw);
        }
        catch (e) {
            logError("Error reading chat:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Failed to read chat" }));
        }
    }
    async handleSaveChat(req, res, shellSlug) {
        try {
            const chunks = [];
            for await (const chunk of req)
                chunks.push(chunk);
            const body = JSON.parse(Buffer.concat(chunks).toString("utf-8"));
            const dir = this.chatDir(shellSlug);
            await mkdir(dir, { recursive: true });
            const id = body.id ?? randomBytes(8).toString("hex");
            const chat = {
                id,
                title: body.title ?? this.generateChatTitle(body.messages ?? []),
                createdAt: body.createdAt ?? new Date().toISOString(),
                updatedAt: new Date().toISOString(),
                messages: body.messages ?? [],
            };
            await writeFile(join(dir, `${id}.json`), JSON.stringify(chat, null, 2), "utf-8");
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ id, title: chat.title }));
        }
        catch (e) {
            logError("Error saving chat:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Failed to save chat" }));
        }
    }
    async handleDeleteChat(res, shellSlug, chatId) {
        try {
            const filePath = join(this.chatDir(shellSlug), `${chatId}.json`);
            if (!existsSync(filePath)) {
                res.writeHead(404, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ error: "Chat not found" }));
                return;
            }
            await rm(filePath);
            res.writeHead(204);
            res.end();
        }
        catch (e) {
            logError("Error deleting chat:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Failed to delete chat" }));
        }
    }
    generateChatTitle(messages) {
        const firstUser = messages.find((m) => m.role === "user");
        if (firstUser?.text) {
            const trimmed = firstUser.text.slice(0, 50);
            return trimmed.length < firstUser.text.length ? trimmed + "…" : trimmed;
        }
        return new Date().toLocaleDateString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
    }
    async handleShellConfig(req, res) {
        const url = new URL(req.url ?? "/", `http://${req.headers.host ?? "localhost"}`);
        const slug = url.searchParams.get("shell") ?? this.defaultShellSlug;
        if (!slug) {
            res.writeHead(404, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "No shell specified and no default available" }));
            return;
        }
        try {
            const yamlPath = join(resolve(this.options.shellsDir), slug, "shell.yaml");
            const content = await readFile(yamlPath, "utf-8");
            res.writeHead(200, { "Content-Type": "text/yaml; charset=utf-8" });
            res.end(content);
        }
        catch {
            res.writeHead(404, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "shell.yaml not found" }));
        }
    }
    // ─── Shell CRUD ─────────────────────────────────────────────────────────────
    async handleGetShell(res, slug) {
        const shellDir = join(resolve(this.options.shellsDir), slug);
        try {
            const shell = await loadShellDefinition(shellDir);
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ slug, ...shell }));
        }
        catch {
            res.writeHead(404, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: `Shell "${slug}" not found` }));
        }
    }
    async handleUpdateShell(req, res, slug) {
        const shellDir = join(resolve(this.options.shellsDir), slug);
        const yamlPath = join(shellDir, "shell.yaml");
        if (!existsSync(yamlPath)) {
            res.writeHead(404, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: `Shell "${slug}" not found` }));
            return;
        }
        try {
            const body = await this.readBody(req);
            await writeFile(yamlPath, body, "utf-8");
            const nameMatch = body.match(/^name:\s*(.+)$/m);
            const name = nameMatch?.[1]?.trim() ?? slug;
            log(`Shell updated: ${slug}`);
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ slug, name }));
        }
        catch (e) {
            logError("Error updating shell:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Failed to update shell" }));
        }
    }
    async handleDeleteShell(res, slug) {
        const shellDir = join(resolve(this.options.shellsDir), slug);
        if (!existsSync(shellDir)) {
            res.writeHead(404, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: `Shell "${slug}" not found` }));
            return;
        }
        try {
            await rm(shellDir, { recursive: true, force: true });
            if (this.defaultShellSlug === slug) {
                this.defaultShellSlug = this.detectDefaultShell();
            }
            log(`Shell deleted: ${slug}`);
            res.writeHead(204);
            res.end();
        }
        catch (e) {
            logError("Error deleting shell:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Failed to delete shell" }));
        }
    }
    // ─── Agent Messaging ────────────────────────────────────────────────────────
    createShellBinding(shell, sessionId, shellSlug, arBridge) {
        if (isngramEntityBinding(shell.binding)) {
            const cfg = resolveEntityBridgeConfig(shell.binding);
            const arMd = arBridge != null
                ? buildArCognitionContextMarkdown({
                    shellSlug,
                    shell,
                    resolvedBehaviors: arBridge.resolvedBehaviors,
                    bindingSystemPrompt: arBridge.bindingSystemPrompt,
                })
                : "";
            return new EntityBridgeBinding({
                ...cfg,
                arCognitionContextMarkdown: arMd || undefined,
                brainConfig: runtimeBrainConfig(this.brainConfig) || undefined,
            }, {
                sessionId,
                shellName: shell.name,
                shellSlug,
            });
        }
        return createBinding(resolvengramBinding(shell.binding));
    }
    async handleShellMessage(req, res, slug) {
        const shellDir = join(resolve(this.options.shellsDir), slug);
        if (!existsSync(join(shellDir, "shell.yaml"))) {
            res.writeHead(404, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: `Shell "${slug}" not found` }));
            return;
        }
        try {
            const body = JSON.parse(await this.readBody(req));
            const text = body.text?.trim();
            if (!text) {
                res.writeHead(400, { "Content-Type": "application/json" });
                res.end(JSON.stringify({ error: "\"text\" is required" }));
                return;
            }
            let sessionId = typeof body.session === "string" ? body.session.trim() : undefined;
            let session = sessionId ? this.sessions.get(sessionId) : undefined;
            if (!session || session.shellSlug !== slug) {
                const shell = await loadShellDefinition(shellDir);
                if (!sessionId)
                    sessionId = randomBytes(16).toString("hex");
                let voice;
                try {
                    voice = createVoiceEngine(shell.voice);
                }
                catch {
                    voice = { synthesize: async () => ({ audioBase64: "" }) };
                }
                const memory = shell.memory ? new MemoryManager(join(shellDir, shell.memory.path)) : null;
                const behaviorsDir = join(resolve(this.options.shellsDir), "..", "behaviors");
                let resolvedBehaviors = [];
                try {
                    const inlineBehaviors = parseInlineBehaviors(shell["behaviors"]);
                    resolvedBehaviors = await loadBehaviors(shell.behaviorPack, inlineBehaviors, behaviorsDir);
                }
                catch (e) {
                    logError(`API: failed to load behaviors for "${slug}":`, e);
                }
                let systemPrompt = shell.binding.system ?? "";
                if (memory) {
                    const memoryContext = await memory.loadContext();
                    if (memoryContext)
                        systemPrompt += `\n\n## Memory\n${memoryContext}`;
                }
                const binding = this.createShellBinding(shell, sessionId, slug, isngramEntityBinding(shell.binding)
                    ? { resolvedBehaviors, bindingSystemPrompt: systemPrompt }
                    : undefined);
                await binding.start(systemPrompt);
                session = { binding, voice, memory, shellSlug: slug, lastActivity: Date.now() };
                this.sessions.set(sessionId, session);
                log(`API session created: ${sessionId} (shell "${slug}")`);
            }
            session.lastActivity = Date.now();
            const event = {
                type: "event:user_speech",
                text,
                isFinal: true,
                timestamp: Date.now(),
                sessionId: sessionId,
            };
            const actions = await session.binding.handleEvent(event);
            const cleanActions = actions.map((a) => {
                if (a.type === "action:speak") {
                    const { audioData, visemes, ...rest } = a;
                    return rest;
                }
                return a;
            });
            res.writeHead(200, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ session: sessionId, actions: cleanActions }));
        }
        catch (e) {
            logError("Error in shell message:", e);
            res.writeHead(500, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Failed to process message" }));
        }
    }
    cleanupSessions() {
        const maxAge = 5 * 60 * 1000;
        const now = Date.now();
        for (const [id, session] of this.sessions) {
            if (now - session.lastActivity > maxAge) {
                session.binding.stop().catch(() => { });
                this.sessions.delete(id);
                log(`API session expired: ${id}`);
            }
        }
    }
    async readBody(req) {
        const chunks = [];
        for await (const chunk of req) {
            chunks.push(chunk);
        }
        return Buffer.concat(chunks).toString("utf-8");
    }
    handleGetBrain(res) {
        res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
        res.end(JSON.stringify({
            config: publicBrainConfig(this.brainConfig),
            providers: BRAIN_PROVIDERS,
        }));
    }
    isSameOriginControlRequest(req) {
        const origin = req.headers.origin;
        if (!origin)
            return true;
        try {
            return new URL(origin).host === req.headers.host;
        }
        catch {
            return false;
        }
    }
    async applyBrainConfig(config) {
        const runtimeConfig = runtimeBrainConfig(config);
        const bindings = [];
        for (const [, client] of this.clients) {
            if (typeof client.binding.configureInference === "function")
                bindings.push(client.binding);
        }
        for (const [, session] of this.sessions) {
            if (typeof session.binding.configureInference === "function" && !bindings.includes(session.binding))
                bindings.push(session.binding);
        }
        if (bindings.length === 0)
            return { synced: false, status: null };
        let lastError = null;
        for (const binding of bindings) {
            try {
                const status = await binding.configureInference(runtimeConfig);
                for (const other of bindings)
                    other.brainConfig = runtimeConfig;
                return { synced: true, status };
            }
            catch (error) {
                lastError = error;
            }
        }
        throw lastError ?? new Error("No Entity bridge accepted the brain configuration.");
    }
    async handleUpdateBrain(req, res) {
        if (!this.isSameOriginControlRequest(req)) {
            res.writeHead(403, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: "Brain settings are same-origin only." }));
            return;
        }
        try {
            const raw = await this.readBody(req);
            if (Buffer.byteLength(raw, "utf8") > 24_000)
                throw new Error("Brain settings payload is too large.");
            const input = JSON.parse(raw || "{}");
            const config = normalizeBrainConfig(input, this.brainConfig);
            const applied = await this.applyBrainConfig(config);
            await saveBrainConfig(this.options.shellsDir, config);
            this.brainConfig = config;
            res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
            res.end(JSON.stringify({ config: publicBrainConfig(config), ...applied }));
        }
        catch (error) {
            const message = error instanceof Error ? error.message : "Could not update the brain.";
            res.writeHead(400, { "Content-Type": "application/json", "Cache-Control": "no-store" });
            res.end(JSON.stringify({ error: message.slice(0, 400) }));
        }
    }
    // ─── OpenAPI Spec ──────────────────────────────────────────────────────────
    handleOpenApiSpec(res) {
        const spec = {
            openapi: "3.0.3",
            info: {
                title: "ngram AR API",
                version: "1.0.0",
                description: "Spatial AI shells — create, manage, and message shells programmatically.",
            },
            paths: {
                "/api/shells": {
                    get: {
                        summary: "List all shells",
                        operationId: "listShells",
                        responses: {
                            "200": {
                                description: "Array of shell summaries",
                                content: {
                                    "application/json": {
                                        schema: {
                                            type: "object",
                                            properties: {
                                                shells: { type: "array", items: { $ref: "#/components/schemas/ShellSummary" } },
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                    post: {
                        summary: "Create a new shell",
                        operationId: "createShell",
                        requestBody: {
                            required: true,
                            content: { "application/json": { schema: { $ref: "#/components/schemas/CreateShellRequest" } } },
                        },
                        responses: {
                            "201": {
                                description: "Shell created",
                                content: { "application/json": { schema: { $ref: "#/components/schemas/ShellCreated" } } },
                            },
                            "400": { description: "Invalid input" },
                            "409": { description: "Shell already exists" },
                        },
                    },
                },
                "/api/shells/{slug}": {
                    get: {
                        summary: "Get shell configuration",
                        operationId: "getShell",
                        parameters: [{ name: "slug", in: "path", required: true, schema: { type: "string" } }],
                        responses: {
                            "200": {
                                description: "Full shell definition as JSON",
                                content: { "application/json": { schema: { $ref: "#/components/schemas/ShellDefinition" } } },
                            },
                            "404": { description: "Shell not found" },
                        },
                    },
                    put: {
                        summary: "Replace shell configuration (YAML body)",
                        operationId: "updateShell",
                        parameters: [{ name: "slug", in: "path", required: true, schema: { type: "string" } }],
                        requestBody: {
                            required: true,
                            description: "Full shell.yaml content",
                            content: { "text/yaml": { schema: { type: "string" } } },
                        },
                        responses: {
                            "200": {
                                description: "Shell updated",
                                content: {
                                    "application/json": {
                                        schema: {
                                            type: "object",
                                            properties: { slug: { type: "string" }, name: { type: "string" } },
                                        },
                                    },
                                },
                            },
                            "404": { description: "Shell not found" },
                        },
                    },
                    delete: {
                        summary: "Delete a shell",
                        operationId: "deleteShell",
                        parameters: [{ name: "slug", in: "path", required: true, schema: { type: "string" } }],
                        responses: {
                            "204": { description: "Shell deleted" },
                            "404": { description: "Shell not found" },
                        },
                    },
                },
                "/api/shells/{slug}/message": {
                    post: {
                        summary: "Send a message to a shell agent",
                        operationId: "sendMessage",
                        description: "Stateless HTTP messaging. Send text, receive spatial actions. " +
                            "Include a session ID for multi-turn conversation; omit to start fresh. " +
                            "Sessions expire after 5 minutes of inactivity.",
                        parameters: [{ name: "slug", in: "path", required: true, schema: { type: "string" } }],
                        requestBody: {
                            required: true,
                            content: {
                                "application/json": {
                                    schema: {
                                        type: "object",
                                        required: ["text"],
                                        properties: {
                                            text: { type: "string", description: "The message to send" },
                                            session: {
                                                type: "string",
                                                description: "Session ID for multi-turn conversation. Omit to start a new session.",
                                            },
                                        },
                                    },
                                },
                            },
                        },
                        responses: {
                            "200": {
                                description: "Agent response",
                                content: {
                                    "application/json": {
                                        schema: {
                                            type: "object",
                                            properties: {
                                                session: { type: "string", description: "Session ID — reuse in subsequent requests" },
                                                actions: {
                                                    type: "array",
                                                    items: { $ref: "#/components/schemas/SpatialAction" },
                                                    description: "Spatial actions produced by the agent (text-only, no audio binary)",
                                                },
                                            },
                                        },
                                    },
                                },
                            },
                            "400": { description: "Missing text field" },
                            "404": { description: "Shell not found" },
                        },
                    },
                },
                "/api/transcribe": {
                    post: {
                        summary: "Transcribe audio via Whisper",
                        operationId: "transcribe",
                        requestBody: {
                            required: true,
                            content: { "multipart/form-data": { schema: { type: "object", properties: { audio: { type: "string", format: "binary" } } } } },
                        },
                        responses: {
                            "200": {
                                description: "Transcription result",
                                content: { "application/json": { schema: { type: "object", properties: { text: { type: "string" } } } } },
                            },
                        },
                    },
                },
                "/api/openapi.json": {
                    get: {
                        summary: "OpenAPI specification",
                        operationId: "getOpenApiSpec",
                        responses: { "200": { description: "This specification" } },
                    },
                },
            },
            components: {
                schemas: {
                    ShellSummary: {
                        type: "object",
                        properties: {
                            slug: { type: "string" },
                            name: { type: "string" },
                            model: { type: "string" },
                        },
                    },
                    CreateShellRequest: {
                        type: "object",
                        required: ["name"],
                        properties: {
                            name: { type: "string" },
                            model: { type: "string", description: "LLM model identifier (default: gpt-4o)" },
                            voice: { type: "string", description: "TTS voice ID (default: echo)" },
                            personality: { type: "string", description: "Custom personality description" },
                        },
                    },
                    ShellCreated: {
                        type: "object",
                        properties: {
                            slug: { type: "string" },
                            name: { type: "string" },
                            model: { type: "string" },
                            voice: { type: "string" },
                            bindingType: { type: "string" },
                            path: { type: "string" },
                        },
                    },
                    ShellDefinition: {
                        type: "object",
                        description: "Full shell configuration (parsed from shell.yaml)",
                        properties: {
                            slug: { type: "string" },
                            name: { type: "string" },
                            description: { type: "string" },
                            model: { type: "string" },
                            scale: { type: "number" },
                            animationPack: { type: "string" },
                            behaviorPack: { type: "array", items: { type: "string" } },
                            voice: { type: "object" },
                            binding: { type: "object" },
                            memory: { type: "object" },
                        },
                    },
                    SpatialAction: {
                        type: "object",
                        description: "An action produced by the shell agent",
                        properties: {
                            type: {
                                type: "string",
                                enum: [
                                    "action:speak",
                                    "action:emote",
                                    "action:gesture",
                                    "action:move_to",
                                    "action:look_at",
                                    "action:show_panel",
                                    "action:hide_panel",
                                    "action:set_mode",
                                    "action:go_idle",
                                ],
                            },
                            text: { type: "string", description: "Speech text (action:speak)" },
                            emotion: { type: "string", description: "Emotion name (action:emote)" },
                            gesture: { type: "string", description: "Gesture name (action:gesture)" },
                        },
                    },
                },
            },
        };
        res.writeHead(200, { "Content-Type": "application/json" });
        res.end(JSON.stringify(spec, null, 2));
    }
    // ─── Shell Asset Serving (models, textures, etc.) ─────────────────────────
    async serveShellAsset(pathname, res) {
        const resolvedShellsDir = resolve(this.options.shellsDir);
        let filePath;
        if (pathname.startsWith("/shells/")) {
            filePath = join(resolvedShellsDir, pathname.slice("/shells/".length));
        }
        else if (pathname.startsWith("/models/") && this.defaultShellSlug) {
            filePath = join(resolvedShellsDir, this.defaultShellSlug, pathname.slice(1));
        }
        else {
            res.writeHead(404, { "Content-Type": "text/plain" });
            res.end("Not Found");
            return;
        }
        if (!filePath.startsWith(resolvedShellsDir)) {
            res.writeHead(403, { "Content-Type": "text/plain" });
            res.end("Forbidden");
            return;
        }
        const ext = extname(filePath);
        await this.serveFile(filePath, ext, res);
    }
    // ─── Static File Serving ──────────────────────────────────────────────────
    async handleStaticFile(pathname, res) {
        const { staticDir } = this.options;
        if (!staticDir) {
            res.writeHead(404, { "Content-Type": "text/plain" });
            res.end("Not Found");
            return;
        }
        try {
            const ext = extname(pathname);
            const resolvedDir = resolve(staticDir);
            if (pathname === "/" || ext === "") {
                const filePath = join(resolvedDir, "index.html");
                await this.serveFile(filePath, ".html", res);
                return;
            }
            const filePath = join(resolvedDir, pathname);
            if (!filePath.startsWith(resolvedDir)) {
                res.writeHead(403, { "Content-Type": "text/plain" });
                res.end("Forbidden");
                return;
            }
            try {
                const fileStat = await stat(filePath);
                if (!fileStat.isFile()) {
                    const fallback = join(resolvedDir, "index.html");
                    await this.serveFile(fallback, ".html", res);
                    return;
                }
            }
            catch {
                const fallback = join(resolvedDir, "index.html");
                await this.serveFile(fallback, ".html", res);
                return;
            }
            await this.serveFile(filePath, ext, res);
        }
        catch (e) {
            logError("HTTP request error:", e);
            res.writeHead(500, { "Content-Type": "text/plain" });
            res.end("Internal Server Error");
        }
    }
    async serveFile(filePath, ext, res) {
        try {
            const data = await readFile(filePath);
            const contentType = CONTENT_TYPES[ext] ?? "application/octet-stream";
            res.writeHead(200, { "Content-Type": contentType });
            res.end(data);
        }
        catch {
            res.writeHead(404, { "Content-Type": "text/plain" });
            res.end("Not Found");
        }
    }
    // ─── WebSocket Connection Handling ──────────────────────────────────────────
    async handleConnection(ws, req) {
        if (!this.requestHasSurfaceAccess(req)) {
            ws.close(4401, "Surface authentication required");
            return;
        }
        const { shellsDir } = this.options;
        const sessionId = randomBytes(16).toString("hex");
        const reqUrl = new URL(req.url ?? "/ws", "http://localhost");
        const shellSlug = reqUrl.searchParams.get("shell") ?? this.defaultShellSlug;
        if (!shellSlug) {
            logError(`No shell available for session ${sessionId}`);
            ws.close(4000, "No shells available");
            return;
        }
        const shellDir = join(shellsDir, shellSlug);
        let shell;
        try {
            shell = await loadShellDefinition(shellDir);
        }
        catch (e) {
            logError(`Failed to load shell "${shellSlug}" for session ${sessionId}:`, e);
            ws.close(4001, `Shell "${shellSlug}" not found`);
            return;
        }
        let voice;
        try {
            voice = createVoiceEngine(shell.voice);
        }
        catch (e) {
            logError(`Voice engine failed for "${shellSlug}", speech will be text-only:`, e);
            voice = { synthesize: async () => ({ audioBase64: "" }) };
        }
        const memory = shell.memory ? new MemoryManager(join(shellDir, shell.memory.path)) : null;
        const behaviorsDir = join(resolve(this.options.shellsDir), "..", "behaviors");
        let resolvedBehaviors = [];
        let behaviorRuntime = null;
        try {
            const inlineBehaviors = parseInlineBehaviors(shell["behaviors"]);
            resolvedBehaviors = await loadBehaviors(shell.behaviorPack, inlineBehaviors, behaviorsDir);
            if (resolvedBehaviors.length > 0) {
                behaviorRuntime = new BehaviorRuntime(resolvedBehaviors);
                log(`Loaded ${resolvedBehaviors.length} behaviors for "${shellSlug}"`);
            }
        }
        catch (e) {
            logError(`Failed to load behaviors for "${shellSlug}":`, e);
        }
        let systemPrompt = shell.binding.system ?? "";
        if (memory) {
            try {
                const memoryContext = await memory.loadContext();
                if (memoryContext) {
                    systemPrompt += `\n\n## Memory\n${memoryContext}`;
                }
            }
            catch (e) {
                logError(`Failed to load shell memory for "${shellSlug}":`, e);
            }
        }
        const binding = this.createShellBinding(shell, sessionId, shellSlug, isngramEntityBinding(shell.binding)
            ? { resolvedBehaviors, bindingSystemPrompt: systemPrompt }
            : undefined);
        const bindingReady = binding.start(systemPrompt);
        const client = { ws, binding, bindingReady, voice, memory, sessionId, behaviorRuntime };
        this.clients.set(ws, client);
        log(`Client connected — session ${sessionId}, shell "${shellSlug}"`);
        const configMsg = {
            type: "shell:config",
            shell: {
                slug: shellSlug,
                name: shell.name,
                model: shell.model,
                scale: shell.scale,
                animationPack: shell.animationPack,
                behaviorPack: shell.behaviorPack,
                behaviors: resolvedBehaviors,
            },
        };
        log(`→ shell:config (session ${sessionId})`);
        this.send(ws, configMsg);
        ws.on("message", (raw) => {
            this.handleMessage(client, raw).catch((e) => {
                logError(`Error handling message for session ${sessionId}:`, e);
            });
        });
        ws.on("close", () => {
            log(`Client disconnected — session ${sessionId}`);
            binding.stop().catch((e) => {
                logError(`Error stopping binding for session ${sessionId}:`, e);
            });
            this.clients.delete(ws);
        });
        ws.on("error", (err) => {
            logError(`WebSocket error for session ${sessionId}:`, err);
        });
        // Wire proactive actions — autonomous agents can push spatial actions
        // without being prompted by a shell event.
        if (binding.onProactiveAction) {
            binding.onProactiveAction((actions) => {
                for (const action of actions) {
                    log(`→ ${action.type} [proactive] (session ${sessionId})`);
                    this.sendSpatialAction(client, action).catch((error) => {
                        this.sendError(ws, sessionId, error);
                    });
                }
            });
        }
        try {
            await bindingReady;
        }
        catch (e) {
            logError(`Failed to start binding for session ${sessionId}:`, e);
            ws.close();
            this.clients.delete(ws);
            return;
        }
    }
    async sendSpatialAction(client, action) {
        const { ws, voice, sessionId } = client;
        if (action.type === "action:turn_cancelled") {
            client.deliveryEpoch = (client.deliveryEpoch ?? 0) + 1;
        }
        const deliveryEpoch = client.deliveryEpoch ?? 0;
        // Live tool actions and behavior results need the same server-side
        // adapters as buffered turn results before reaching the renderer.
        if (action.type === "action:generate_motion") {
            this.send(ws, createAction("action:set_agent_state", sessionId, {
                state: "tool_running",
                message: "Generating motion on the spatial compute provider",
            }));
            try {
                const motionAction = await this.motionProvider.generate(action, sessionId);
                if (deliveryEpoch !== (client.deliveryEpoch ?? 0))
                    return;
                this.send(ws, motionAction);
            }
            catch (motionError) {
                if (deliveryEpoch === (client.deliveryEpoch ?? 0))
                    this.sendError(ws, sessionId, motionError);
            }
            finally {
                if (deliveryEpoch === (client.deliveryEpoch ?? 0))
                    this.send(ws, createAction("action:set_agent_state", sessionId, { state: "idle" }));
            }
            return;
        }
        if (action.type === "action:speak") {
            await this.synthesizeSpeech(action, voice);
            if (deliveryEpoch !== (client.deliveryEpoch ?? 0))
                return;
        }
        this.send(ws, action);
    }
    // ─── Event Routing ──────────────────────────────────────────────────────────
    async handleMessage(client, raw) {
        const { ws, binding, sessionId, behaviorRuntime } = client;
        await client.bindingReady;
        let event;
        try {
            event = JSON.parse(raw.toString());
        }
        catch {
            logError(`Invalid JSON from session ${sessionId}`);
            return;
        }
        log(`← ${event.type} (session ${sessionId})`);
        if (event.type === "event:action_completed") {
            return;
        }
        if (event.type === "event:behavior_trigger" && behaviorRuntime) {
            await this.handleBehaviorTrigger(client, event);
            return;
        }
        try {
            const actions = await binding.handleEvent(event);
            for (const action of actions) {
                log(`→ ${action.type} (session ${sessionId})`);
                await this.sendSpatialAction(client, action);
            }
        }
        catch (e) {
            logError(`Error processing ${event.type} for session ${sessionId}:`, e);
            this.sendError(ws, sessionId, e);
        }
    }
    async handleBehaviorTrigger(client, event) {
        const { ws, binding, sessionId, behaviorRuntime } = client;
        if (!behaviorRuntime)
            return;
        const result = behaviorRuntime.handleTrigger(event, sessionId);
        if (!result)
            return;
        log(`⚡ behavior:${event.behaviorId} → ${result.type} (session ${sessionId})`);
        if (result.type === "action") {
            for (const action of result.actions) {
                log(`→ ${action.type} [behavior] (session ${sessionId})`);
                await this.sendSpatialAction(client, action);
            }
        }
        else if (result.type === "prompt") {
            if (binding.injectBehaviorPrompt) {
                try {
                    const actions = await binding.injectBehaviorPrompt(result.prompt, {
                        ...result.context,
                        sessionId,
                    });
                    for (const action of actions) {
                        log(`→ ${action.type} [deliberative] (session ${sessionId})`);
                        await this.sendSpatialAction(client, action);
                    }
                }
                catch (e) {
                    logError(`Deliberative behavior failed for session ${sessionId}:`, e);
                    this.sendError(ws, sessionId, e);
                }
            }
        }
    }
    async synthesizeSpeech(action, voice) {
        try {
            const result = await voice.synthesize(action.text);
            action.audioData = result.audioBase64;
            action.visemes = result.visemes;
        }
        catch (e) {
            logError("Voice synthesis failed, sending text-only:", e);
        }
    }
    sendError(ws, sessionId, e) {
        const msg = e instanceof Error ? e.message : String(e);
        let code = "internal_error";
        if (msg.includes("API error"))
            code = "api_error";
        else if (msg.includes("timeout") || msg.includes("ETIMEDOUT"))
            code = "api_timeout";
        else if (msg.includes("ECONNREFUSED") || msg.includes("offline"))
            code = "agent_offline";
        const errorAction = createAction("action:error", sessionId, {
            code,
            message: msg.slice(0, 200),
            retryAfterMs: code === "api_timeout" ? 5000 : undefined,
        });
        log(`→ action:error [${code}] (session ${sessionId})`);
        this.send(ws, errorAction);
    }
    send(ws, msg) {
        if (ws.readyState === WebSocket.OPEN) {
            ws.send(JSON.stringify(msg));
        }
    }
}
function findOpenSSL() {
    const candidates = [
        "openssl",
        "C:\\Program Files\\Git\\usr\\bin\\openssl.exe",
        "C:\\Program Files (x86)\\Git\\usr\\bin\\openssl.exe",
        "C:\\Windows\\System32\\openssl.exe",
    ];
    for (const cmd of candidates) {
        try {
            execSync(`"${cmd}" version`, { stdio: "pipe" });
            return cmd;
        }
        catch { /* not found */ }
    }
    return null;
}
function generateSelfSignedCert(host) {
    const openssl = findOpenSSL();
    if (!openssl) {
        throw new Error("OpenSSL not found. Install openssl and ensure it is on PATH.");
    }
    const tmpDir = join(tmpdir(), `ngram-ar-cert-${randomBytes(4).toString("hex")}`);
    mkdirSync(tmpDir, { recursive: true });
    const keyPath = join(tmpDir, "key.pem");
    const certPath = join(tmpDir, "cert.pem");
    const subj = `/CN=ngram-ar.local`;
    // SANs: localhost + bind host. When binding 0.0.0.0, include detected LAN IPv4s so
    // Quest/other LAN clients get a cert naming the address they actually typed.
    const ips = new Set(["127.0.0.1"]);
    if (host && host !== "0.0.0.0") {
        ips.add(host);
    }
    else {
        try {
            const nets = networkInterfaces();
            for (const list of Object.values(nets)) {
                for (const net of list || []) {
                    if (net.family === "IPv4" && !net.internal)
                        ips.add(net.address);
                }
            }
        }
        catch { /* best-effort */ }
    }
    const san = "subjectAltName=DNS:localhost,DNS:ngram-ar.local,DNS:ngramar.local," +
        [...ips].map((ip) => `IP:${ip}`).join(",");
    execSync(`"${openssl}" req -x509 -newkey rsa:2048 -keyout "${keyPath}" -out "${certPath}" ` +
        `-days 365 -nodes -sha256 -subj "${subj}" -addext "${san}"`, { stdio: "pipe" });
    const key = readFileSync(keyPath, "utf-8");
    const cert = readFileSync(certPath, "utf-8");
    try {
        rmSync(tmpDir, { recursive: true, force: true });
    }
    catch { /* cleanup best-effort */ }
    return { key, cert };
}
function formatSceneDescription(anchors) {
    if (anchors.length === 0)
        return "Empty scene with no detected anchors.";
    const parts = anchors.map((a) => {
        const pos = `(${a.transform.position.x.toFixed(1)}, ${a.transform.position.y.toFixed(1)}, ${a.transform.position.z.toFixed(1)})`;
        const type = a.semanticType ? ` [${a.semanticType}]` : "";
        const dims = a.dimensions
            ? ` ${a.dimensions.x.toFixed(1)}x${a.dimensions.y.toFixed(1)}x${a.dimensions.z.toFixed(1)}m`
            : "";
        return `${a.label}${type} at ${pos}${dims}`;
    });
    return `Scene contains: ${parts.join("; ")}.`;
}
