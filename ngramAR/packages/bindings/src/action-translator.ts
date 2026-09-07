// @ts-nocheck
import { createAction } from "@ngram-ar/core";
const EXCITED_WORDS = /\b(wow|amazing|fantastic|incredible|absolutely|definitely)\b/i;
const SORRY_WORDS = /\b(sorry|unfortunately|apologize|regret|afraid)\b/i;
const HAPPY_WORDS = /\b(great|awesome|wonderful|excellent|love|glad|haha|lol|😄)\b/i;
const THINKING_WORDS = /\b(hmm|let me think|interesting|consider|perhaps|maybe)\b/i;
const CODE_BLOCK_RE = /```(\w*)\n([\s\S]*?)```/g;
const ACTION_TAG_RE = /\[(move|wave|idle)(?::([^\]]*))?\]/gi;
const MOVE_TARGETS = {
    user: "user",
    away: { x: 0, y: 0, z: 2 },
    left: { x: -1.5, y: 0, z: 0 },
    right: { x: 1.5, y: 0, z: 0 },
    forward: { x: 0, y: 0, z: -1.5 },
    back: { x: 0, y: 0, z: 1.5 },
    random: "random",
};
export class ActionTranslator {
    translateResponse(text, sessionId) {
        const actions = [];
        const parsedActions = this.extractActionTags(text, sessionId);
        const cleanText = text.replace(ACTION_TAG_RE, "").replace(/\s{2,}/g, " ").trim();
        actions.push(createAction("action:speak", sessionId, { text: cleanText }));
        const emote = this.detectEmotion(cleanText);
        if (emote) {
            actions.push(createAction("action:emote", sessionId, emote));
        }
        actions.push(createAction("action:look_at", sessionId, {
            target: "user",
        }));
        actions.push(...parsedActions);
        const codeBlocks = this.extractCodeBlocks(cleanText);
        for (const block of codeBlocks) {
            actions.push(createAction("action:show_panel", sessionId, {
                panel: {
                    id: `code-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
                    type: "code",
                    title: block.language || "Code",
                    content: block.code,
                },
            }));
        }
        return actions;
    }
    extractActionTags(text, sessionId) {
        const actions = [];
        let match;
        while ((match = ACTION_TAG_RE.exec(text)) !== null) {
            const action = match[1].toLowerCase();
            const param = (match[2] || "").toLowerCase().trim();
            switch (action) {
                case "move": {
                    const target = MOVE_TARGETS[param] ?? MOVE_TARGETS["forward"];
                    if (param === "random") {
                        const angle = Math.random() * Math.PI * 2;
                        const dist = 0.8 + Math.random() * 1.2;
                        actions.push(createAction("action:move_to", sessionId, {
                            target: { x: Math.cos(angle) * dist, y: 0, z: Math.sin(angle) * dist },
                            speed: "walk",
                        }));
                    }
                    else {
                        actions.push(createAction("action:move_to", sessionId, {
                            target,
                            speed: "walk",
                        }));
                    }
                    break;
                }
                case "wave":
                    actions.push(createAction("action:gesture", sessionId, {
                        gesture: "wave",
                    }));
                    break;
                case "idle":
                    actions.push(createAction("action:go_idle", sessionId, {}));
                    break;
            }
        }
        ACTION_TAG_RE.lastIndex = 0;
        return actions;
    }
    detectEmotion(text) {
        if (SORRY_WORDS.test(text)) {
            return { emotion: "concerned", intensity: 0.6 };
        }
        if (HAPPY_WORDS.test(text)) {
            return { emotion: "happy", intensity: 0.8 };
        }
        if (THINKING_WORDS.test(text)) {
            return { emotion: "thoughtful", intensity: 0.5 };
        }
        if (text.includes("!") || EXCITED_WORDS.test(text)) {
            return { emotion: "excited", intensity: 0.7 };
        }
        if (text.includes("?")) {
            return { emotion: "curious", intensity: 0.5 };
        }
        return null;
    }
    extractCodeBlocks(text) {
        const blocks = [];
        let match;
        while ((match = CODE_BLOCK_RE.exec(text)) !== null) {
            blocks.push({ language: match[1] ?? "", code: match[2] ?? "" });
        }
        CODE_BLOCK_RE.lastIndex = 0;
        return blocks;
    }
}
