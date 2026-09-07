// @ts-nocheck
import { readFile, appendFile, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import path from "node:path";
export class MemoryManager {
    memoryPath;
    constructor(memoryPath) {
        this.memoryPath = memoryPath;
    }
    get longTermPath() {
        return path.join(this.memoryPath, "MEMORY.md");
    }
    get dailyDir() {
        return path.join(this.memoryPath, "memory");
    }
    todayFileName() {
        const now = new Date();
        const yyyy = now.getFullYear();
        const mm = String(now.getMonth() + 1).padStart(2, "0");
        const dd = String(now.getDate()).padStart(2, "0");
        return `${yyyy}-${mm}-${dd}.md`;
    }
    get todayPath() {
        return path.join(this.dailyDir, this.todayFileName());
    }
    async ensureDirs() {
        await mkdir(this.dailyDir, { recursive: true });
    }
    async safeRead(filePath) {
        try {
            return await readFile(filePath, "utf-8");
        }
        catch {
            return "";
        }
    }
    async loadContext() {
        await this.ensureDirs();
        const longTerm = await this.safeRead(this.longTermPath);
        const daily = await this.safeRead(this.todayPath);
        const parts = [];
        if (longTerm) {
            parts.push("## Long-term Memory\n" + longTerm);
        }
        if (daily) {
            parts.push("## Today's Notes\n" + daily);
        }
        return parts.join("\n\n");
    }
    async appendDaily(content) {
        await this.ensureDirs();
        // Use appendFile to avoid read-then-write race condition
        const prefix = existsSync(this.todayPath) ? "\n" : "";
        await appendFile(this.todayPath, prefix + content, "utf-8");
    }
    async summarizeAndFlush(messages) {
        if (messages.length === 0)
            return;
        const topics = extractTopics(messages);
        if (topics.length === 0)
            return;
        const timestamp = new Date().toLocaleTimeString("en-US", {
            hour: "2-digit",
            minute: "2-digit",
        });
        const summary = `### Session @ ${timestamp}\n` +
            topics.map((t) => `- ${t}`).join("\n");
        await this.appendDaily(summary);
    }
}
// ─── Heuristic Topic Extraction ─────────────────────────────────────────────
// Pulls key phrases from the conversation without requiring an LLM call.
function extractTopics(messages) {
    const topics = [];
    const seen = new Set();
    for (const msg of messages) {
        if (msg.role === "system")
            continue;
        const sentences = msg.content
            .split(/[.!?\n]+/)
            .map((s) => s.trim())
            .filter((s) => s.length > 10 && s.length < 200);
        for (const sentence of sentences) {
            const key = sentence.toLowerCase().slice(0, 60);
            if (seen.has(key))
                continue;
            seen.add(key);
            if (isNoteworthySentence(sentence)) {
                topics.push(msg.role === "user"
                    ? `User: ${sentence}`
                    : sentence);
            }
        }
        if (topics.length >= 10)
            break;
    }
    return topics;
}
const NOTEWORTHY_PATTERNS = [
    /\bmy\b/i,
    /\bI\s+(am|have|want|need|like|prefer|work|live)\b/i,
    /\bwe\s+(should|decided|agreed|need)\b/i,
    /\bremember\b/i,
    /\bimportant\b/i,
    /\bsummary\b/i,
    /\bdecided\b/i,
    /\baction\s+item/i,
    /\btodo\b/i,
    /\bnext\s+step/i,
];
function isNoteworthySentence(sentence) {
    return NOTEWORTHY_PATTERNS.some((p) => p.test(sentence));
}
