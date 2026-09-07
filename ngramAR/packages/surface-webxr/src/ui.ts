// @ts-nocheck
import { marked } from 'marked';
import { isThemeName, nextTheme, normalizeTheme, themeUsesDarkPanels } from './theme.js';
import { providerLogo } from './provider-logos.js';

marked.setOptions({ breaks: true, gfm: true });

export interface CreateAgentData {
  name: string;
  voice: string;
}

export interface DevToolsHandle {
  logMessage: (direction: 'inbound' | 'outbound', msg: Record<string, unknown>) => void;
}

export type ShellSaveCallback = (yaml: string) => Promise<void>;
export type AgentMenuCallback = (action: string, slug: string) => void;
export type CustomActionCallback = (action: Record<string, unknown>) => void;
export type UITheme = 'light' | 'dark' | 'periwinkle';

export interface UIHandle {
  micBtn: HTMLButtonElement;
  sendBtn: HTMLButtonElement;
  textInput: HTMLTextAreaElement;
  arBtn: HTMLButtonElement;
  refocusBtn: HTMLButtonElement;
  transcriptEl: HTMLElement;
  viewportContainer: HTMLElement;
  setStatus: (text: string, connected: boolean) => void;
  addTranscript: (role: 'user' | 'agent', text: string) => void;
  addToolTrace: (toolName: string, description?: string) => void;
  showBusy: (label?: string) => void;
  hideBusy: () => void;
  showARButton: (visible: boolean) => void;
  setMicActive: (active: boolean) => void;
  setShellInfo: (name: string, status: string, connected: boolean) => void;
  showSubtitle: (speaker: string, text: string, duration?: number) => void;
  hideSubtitle: () => void;
  toggleDrawer: () => void;
  isDrawerOpen: () => boolean;
  focusInput: () => void;
  getTheme: () => UITheme;
  isDark: () => boolean;
  toggleTheme: () => void;
  onThemeChange: (cb: (theme: UITheme) => void) => void;
  onCreateAgent: (cb: (data: CreateAgentData) => void) => void;
  devtools: DevToolsHandle;
  onTestAction: (cb: (actionType: string, args: Record<string, unknown>) => void) => void;
  onShellSave: (cb: ShellSaveCallback) => void;
  onAgentMenu: (cb: AgentMenuCallback) => void;
  onCustomAction: (cb: CustomActionCallback) => void;
  setActiveShellSlug: (slug: string) => void;
  addNotification: (text: string) => void;
  onTerminalToggle: (cb: () => void) => void;
  clearTranscript: () => void;
  getMessages: () => Array<{ role: string; text: string; time: string }>;
  loadMessages: (messages: Array<{ role: string; text: string; time: string }>) => void;
  onNewChat: (cb: () => void) => void;
  onChatSelect: (cb: (chatId: string) => void) => void;
  setChatList: (chats: Array<{ id: string; title: string; createdAt: string; messageCount: number }>) => void;
  setActiveChatId: (id: string | null) => void;
  setRecentChats: (chats: Array<{ id: string; title: string; createdAt: string; lastMessage: string; shellName: string; shellSlug: string }>) => void;
  onSettingsEnvChange: (cb: (preset: string) => void) => void;
  onSettingsClearObjects: (cb: () => void) => void;
  onSettingsClearDrawings: (cb: () => void) => void;
  onSettingsClearEnv: (cb: () => void) => void;
  onSettingsClearAll: (cb: () => void) => void;
  updateSettingsObjectCount: (n: number) => void;
  updateSettingsEnvPreset: (preset: string) => void;
  onShellSelect: (cb: (slug: string) => void) => void;
}

const MAX_TRANSCRIPT_MESSAGES = 80;
const SUBTITLE_HOLD_MS = 4000;

const PLACEHOLDER_HINTS = [
  'Message or /command',
  'Ask your agent a question',
  'Try "Tell me about yourself"',
  'Give a command like "walk over there"',
  'Ask "What can you do?"',
  'Try "Show me something cool"',
  'Say "Hello" to start a conversation',
  'Ask about the weather, news, anything...',
  'Say "Come closer" or "Step back"',
  'Try "Wave at me"',
  'Ask "What are you thinking about?"',
  'Say "Celebrate!" and watch what happens',
  'Ask "Explain how ngram AR works"',
  'Try "Look at me"',
  'Ask "Who made you?"',
  'Say "Tell me a joke"',
  'Try "Nod if you understand"',
  'Ask "What do you see around you?"',
  'Say "Go idle" to let them rest',
  'Try "Shrug" for a reaction',
  'Ask "What makes you different?"',
  'Say something unexpected...',
];
const PLACEHOLDER_CYCLE_MS = 8000;
const PLACEHOLDER_FADE_MS = 500;

function formatTime(): string {
  const d = new Date();
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

export function setupUI(): UIHandle {
  const micBtn = document.getElementById('mic-btn') as HTMLButtonElement;
  const sendBtn = document.getElementById('send-btn') as HTMLButtonElement;
  const textInput = document.getElementById('text-input') as HTMLTextAreaElement;
  const arBtn = document.getElementById('ar-btn') as HTMLButtonElement;
  const refocusBtn = document.getElementById('refocus-btn') as HTMLButtonElement;
  const transcriptEl = document.getElementById('transcript') as HTMLElement;
  const viewportContainer = document.querySelector('.viewport-container') as HTMLElement;

  const shellNameEl = document.getElementById('shell-name') as HTMLElement;
  const shellStatusEl = document.getElementById('shell-status') as HTMLElement;
  const shellDotEl = document.getElementById('shell-dot') as HTMLElement;
  const drawerEl = document.getElementById('context-drawer') as HTMLElement;
  const drawerToggleBtn = document.getElementById('drawer-toggle') as HTMLButtonElement;
  const drawerCloseBtn = document.getElementById('drawer-close') as HTMLButtonElement;

  const statusDotEl = document.getElementById('status-dot') as HTMLElement;
  const statusTextEl = document.getElementById('status-text') as HTMLElement;

  const agentMiniAvatarEl = document.getElementById('agent-mini-avatar') as HTMLElement;

  const subtitleEl = document.getElementById('subtitle') as HTMLElement;
  const subtitleSpeakerEl = document.getElementById('subtitle-speaker') as HTMLElement;
  const subtitleTextEl = document.getElementById('subtitle-text') as HTMLElement;

  const themeToggleBtn = document.getElementById('theme-toggle') as HTMLButtonElement;
  const sunIcon = document.getElementById('theme-icon-sun') as HTMLElement;
  const moonIcon = document.getElementById('theme-icon-moon') as HTMLElement;
  const periwinkleIcon = document.getElementById('theme-icon-periwinkle') as HTMLElement;
  const sidebarToggleBtn = document.getElementById('sidebar-toggle') as HTMLButtonElement;

  let subtitleHideTimer: ReturnType<typeof setTimeout> | null = null;
  const themeCallbacks: Array<(theme: UITheme) => void> = [];

  function applyConnectedClass(el: HTMLElement | null, connected: boolean, connecting: boolean): void {
    if (!el) return;
    el.classList.remove('connected', 'connecting', 'disconnected');
    if (connected) el.classList.add('connected');
    else if (connecting) el.classList.add('connecting');
    else el.classList.add('disconnected');
  }

  // --- Theme ---
  function getTheme(): UITheme {
    return normalizeTheme(document.documentElement.getAttribute('data-theme')) as UITheme;
  }

  function updateThemeIcons(): void {
    const upcoming = nextTheme(getTheme()) as UITheme;
    if (sunIcon) sunIcon.style.display = upcoming === 'light' ? 'block' : 'none';
    if (moonIcon) moonIcon.style.display = upcoming === 'dark' ? 'block' : 'none';
    if (periwinkleIcon) periwinkleIcon.style.display = upcoming === 'periwinkle' ? 'block' : 'none';
    const label = upcoming[0].toUpperCase() + upcoming.slice(1);
    const hint = `Switch to ${label} theme`;
    themeToggleBtn?.setAttribute('aria-label', hint);
    if (themeToggleBtn) themeToggleBtn.title = hint;
  }

  function applyTheme(theme: UITheme, notify = true): void {
    const next = normalizeTheme(theme) as UITheme;
    const previous = getTheme();
    document.documentElement.setAttribute('data-theme', next);
    try { localStorage.setItem('ngram_ar_theme', next); } catch {}
    updateThemeIcons();
    const settingsSelect = document.getElementById('settings-theme') as HTMLSelectElement | null;
    if (settingsSelect) settingsSelect.value = next;
    if (notify && previous !== next) themeCallbacks.forEach((cb) => cb(next));
  }

  const saved = (() => { try { return localStorage.getItem('ngram_ar_theme'); } catch { return null; } })();
  if (isThemeName(saved)) {
    document.documentElement.setAttribute('data-theme', saved);
  }
  applyTheme(getTheme(), false);

  themeToggleBtn?.addEventListener('click', () => {
    applyTheme(nextTheme(getTheme()) as UITheme);
  });

  // --- Sidebar collapse ---
  sidebarToggleBtn?.addEventListener('click', () => {
    document.body.classList.toggle('sidebar-collapsed');
  });

  // --- Share button ---
  const shareBtn = document.getElementById('share-btn') as HTMLButtonElement;
  const shareToast = document.getElementById('share-toast') as HTMLElement;
  shareBtn?.addEventListener('click', () => {
    navigator.clipboard.writeText(window.location.href).then(() => {
      shareToast?.classList.add('visible');
      setTimeout(() => shareToast?.classList.remove('visible'), 2000);
    }).catch(() => {
      shareToast.textContent = 'Copy failed';
      shareToast?.classList.add('visible');
      setTimeout(() => { shareToast?.classList.remove('visible'); shareToast.textContent = 'Link copied!'; }, 2000);
    });
  });

  // --- Fullscreen ---
  const fullscreenBtn = document.getElementById('fullscreen-btn');
  const fsEnterIcon = document.getElementById('fullscreen-icon-enter');
  const fsExitIcon = document.getElementById('fullscreen-icon-exit');
  function updateFullscreenIcons(): void {
    const isFs = !!document.fullscreenElement;
    if (fsEnterIcon) fsEnterIcon.style.display = isFs ? 'none' : 'block';
    if (fsExitIcon) fsExitIcon.style.display = isFs ? 'block' : 'none';
  }
  fullscreenBtn?.addEventListener('click', () => {
    if (document.fullscreenElement) {
      document.exitFullscreen().catch(() => {});
    } else {
      document.documentElement.requestFullscreen().catch(() => {});
    }
  });
  document.addEventListener('fullscreenchange', updateFullscreenIcons);

  // --- Notifications ---
  const notifBtn = document.getElementById('notif-btn');
  const notifBadge = document.getElementById('notif-badge');
  const notifPanel = document.getElementById('notif-panel');
  const notifList = document.getElementById('notif-list');
  const notifClear = document.getElementById('notif-clear');
  const notifications: Array<{ text: string; time: Date }> = [];

  function renderNotifications(): void {
    if (!notifList) return;
    if (notifications.length === 0) {
      notifList.innerHTML = '<div class="notif-empty">No notifications yet.</div>';
      return;
    }
    notifList.innerHTML = '';
    for (let i = notifications.length - 1; i >= 0; i--) {
      const n = notifications[i];
      const item = document.createElement('div');
      item.className = 'notif-item';
      const ago = formatTimeAgo(n.time);
      item.innerHTML = `<div>${n.text}</div><div class="notif-item-time">${ago}</div>`;
      notifList.appendChild(item);
    }
  }

  function formatTimeAgo(date: Date): string {
    const sec = Math.floor((Date.now() - date.getTime()) / 1000);
    if (sec < 60) return 'just now';
    if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
    return `${Math.floor(sec / 86400)}d ago`;
  }

  function addNotification(text: string): void {
    notifications.push({ text, time: new Date() });
    if (notifications.length > 50) notifications.shift();
    notifBadge?.classList.add('visible');
    renderNotifications();
  }

  notifBtn?.addEventListener('click', (e) => {
    e.stopPropagation();
    notifPanel?.classList.toggle('open');
    if (notifPanel?.classList.contains('open')) {
      notifBadge?.classList.remove('visible');
      renderNotifications();
    }
  });

  notifClear?.addEventListener('click', () => {
    notifications.length = 0;
    renderNotifications();
    notifBadge?.classList.remove('visible');
  });

  document.addEventListener('click', (e) => {
    if (notifPanel?.classList.contains('open') && !notifPanel.contains(e.target as Node) && e.target !== notifBtn) {
      notifPanel.classList.remove('open');
    }
  });

  // --- Browse Shells ---
  const browseModal = document.getElementById('browse-shells-modal') as HTMLElement;
  const browseGrid = document.getElementById('shell-browser-grid') as HTMLElement;
  const browseClose = document.getElementById('browse-shells-close') as HTMLButtonElement;
  const marketplaceBtn = document.getElementById('marketplace-btn');
  const shellSelectCallbacks: Array<(slug: string) => void> = [];

  function closeBrowseModal(): void {
    browseModal?.classList.remove('open');
  }

  browseClose?.addEventListener('click', closeBrowseModal);
  browseModal?.addEventListener('click', (e) => {
    if (e.target === browseModal) closeBrowseModal();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && browseModal?.classList.contains('open')) closeBrowseModal();
  });

  async function openBrowseModal(): Promise<void> {
    browseModal?.classList.add('open');
    browseGrid.innerHTML = '<div class="shell-browser-empty">Loading shells...</div>';

    try {
      const res = await fetch('/api/shells');
      if (!res.ok) throw new Error('Failed to load');
      const data = await res.json() as { shells: Array<{ slug: string; name: string; description: string; binding: string; voice: string; behaviors: number }> };

      if (!data.shells?.length) {
        browseGrid.innerHTML = '<div class="shell-browser-empty">No shells found.</div>';
        return;
      }

      browseGrid.innerHTML = '';
      for (const shell of data.shells) {
        const card = document.createElement('div');
        card.className = 'shell-browser-card' + (shell.slug === currentShellSlug ? ' active' : '');

        const avatar = document.createElement('div');
        avatar.className = 'shell-browser-avatar';
        avatar.textContent = shell.name.charAt(0).toUpperCase();

        const info = document.createElement('div');
        info.className = 'shell-browser-info';

        const name = document.createElement('div');
        name.className = 'shell-browser-name';
        name.textContent = shell.name;

        const desc = document.createElement('div');
        desc.className = 'shell-browser-desc';
        desc.textContent = shell.description || 'No description';

        const tags = document.createElement('div');
        tags.className = 'shell-browser-tags';
        tags.innerHTML = [
          shell.binding,
          shell.voice,
          shell.behaviors + ' behavior' + (shell.behaviors !== 1 ? 's' : ''),
        ].map((t) => `<span class="shell-browser-tag">${t}</span>`).join('');

        info.appendChild(name);
        info.appendChild(desc);
        info.appendChild(tags);

        const actionWrap = document.createElement('div');
        actionWrap.className = 'shell-browser-action';
        const btn = document.createElement('button');
        btn.className = 'shell-browser-launch';
        btn.textContent = shell.slug === currentShellSlug ? 'Active' : 'Launch';
        btn.disabled = shell.slug === currentShellSlug;
        actionWrap.appendChild(btn);

        card.appendChild(avatar);
        card.appendChild(info);
        card.appendChild(actionWrap);

        card.addEventListener('click', () => {
          if (shell.slug === currentShellSlug) return;
          shellSelectCallbacks.forEach((cb) => cb(shell.slug));
          closeBrowseModal();
        });

        browseGrid.appendChild(card);
      }
    } catch {
      browseGrid.innerHTML = '<div class="shell-browser-empty">Failed to load shells.</div>';
    }
  }

  marketplaceBtn?.addEventListener('click', () => openBrowseModal());

  // --- Panel resize ---
  const resizeSidebar = document.getElementById('resize-sidebar');
  const resizeDrawer = document.getElementById('resize-drawer');
  const sidebarEl = document.querySelector('.sidebar') as HTMLElement;

  const UI_SCALE = 1.25;
  const SIDEBAR_MIN = 200;
  const SIDEBAR_MAX = 500;
  const DRAWER_MIN = 325;
  const DRAWER_MAX = 750;

  function setupPanelResize(
    handle: HTMLElement | null,
    getWidth: () => number,
    setWidth: (w: number) => void,
    side: 'left' | 'right',
  ): void {
    if (!handle) return;
    let startX = 0;
    let startW = 0;

    function onMove(e: PointerEvent): void {
      const dx = e.clientX - startX;
      const w = side === 'left' ? startW + dx : startW - dx;
      setWidth(w);
    }

    function onUp(): void {
      document.body.classList.remove('panel-resizing');
      handle!.classList.remove('dragging');
      document.removeEventListener('pointermove', onMove);
      document.removeEventListener('pointerup', onUp);
    }

    handle.addEventListener('pointerdown', (e: PointerEvent) => {
      e.preventDefault();
      startX = e.clientX;
      startW = getWidth();
      document.body.classList.add('panel-resizing');
      handle!.classList.add('dragging');
      document.addEventListener('pointermove', onMove);
      document.addEventListener('pointerup', onUp);
    });
  }

  setupPanelResize(
    resizeSidebar,
    () => sidebarEl?.offsetWidth ?? 275,
    (w) => {
      const clamped = Math.min(SIDEBAR_MAX, Math.max(SIDEBAR_MIN, w));
      document.documentElement.style.setProperty('--sidebar-w', clamped + 'px');
      document.documentElement.style.setProperty('--scene-sidebar-w', clamped / UI_SCALE + 'px');
    },
    'left',
  );

  setupPanelResize(
    resizeDrawer,
    () => drawerEl?.offsetWidth ?? 450,
    (w) => {
      const clamped = Math.min(DRAWER_MAX, Math.max(DRAWER_MIN, w));
      document.documentElement.style.setProperty('--drawer-w', clamped + 'px');
    },
    'right',
  );

  // --- Status ---
  function setStatus(text: string, connected: boolean): void {
    const isConnecting = !connected && text.toLowerCase().includes('connect');
    if (statusTextEl) statusTextEl.textContent = text;
    applyConnectedClass(statusDotEl, connected, isConnecting);

  }

  // --- Shell info ---
  function setShellInfo(name: string, status: string, connected: boolean): void {
    if (shellNameEl) shellNameEl.textContent = name;
    if (shellStatusEl) shellStatusEl.textContent = status;
    if (shellDotEl) shellDotEl.style.background = connected ? 'var(--success)' : 'var(--text-muted)';
    const initial = name.charAt(0).toUpperCase();
    if (agentMiniAvatarEl) agentMiniAvatarEl.textContent = initial;
  }

  // --- Context Menu ---
  const ctxMenuEl = document.getElementById('ctx-menu') as HTMLElement;

  interface CtxMenuItem {
    label: string;
    icon?: string;
    danger?: boolean;
    action: () => void;
  }

  function showContextMenu(x: number, y: number, items: (CtxMenuItem | 'sep')[]): void {
    if (!ctxMenuEl) return;
    ctxMenuEl.innerHTML = '';

    for (const item of items) {
      if (item === 'sep') {
        const sep = document.createElement('div');
        sep.className = 'ctx-menu-sep';
        ctxMenuEl.appendChild(sep);
        continue;
      }
      const btn = document.createElement('button');
      btn.className = 'ctx-menu-item' + (item.danger ? ' danger' : '');
      if (item.icon) btn.innerHTML = item.icon;
      const span = document.createElement('span');
      span.textContent = item.label;
      btn.appendChild(span);
      btn.addEventListener('click', () => {
        hideContextMenu();
        item.action();
      });
      ctxMenuEl.appendChild(btn);
    }

    // Position, keeping within viewport
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    ctxMenuEl.style.left = '0px';
    ctxMenuEl.style.top = '0px';
    ctxMenuEl.classList.add('visible');

    const rect = ctxMenuEl.getBoundingClientRect();
    const finalX = Math.min(x, vw - rect.width - 8);
    const finalY = Math.min(y, vh - rect.height - 8);
    ctxMenuEl.style.left = finalX + 'px';
    ctxMenuEl.style.top = finalY + 'px';
  }

  function hideContextMenu(): void {
    ctxMenuEl?.classList.remove('visible');
  }

  document.addEventListener('click', (e) => {
    if (ctxMenuEl && !ctxMenuEl.contains(e.target as Node)) hideContextMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') hideContextMenu();
  });

  // Agent card — mark selected
  const agentCardEl = document.getElementById('agent-dot');
  agentCardEl?.classList.add('selected');

  agentCardEl?.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const svgEye = '<svg viewBox="0 0 24 24"><path d="M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5ZM12 17c-2.76 0-5-2.24-5-5s2.24-5 5-5 5 2.24 5 5-2.24 5-5 5Zm0-8c-1.66 0-3 1.34-3 3s1.34 3 3 3 3-1.34 3-3-1.34-3-3-3Z"/></svg>';
    const svgEdit = '<svg viewBox="0 0 24 24"><path d="M3 17.25V21h3.75L17.81 9.94l-3.75-3.75L3 17.25ZM20.71 7.04a1 1 0 0 0 0-1.41l-2.34-2.34a1 1 0 0 0-1.41 0l-1.83 1.83 3.75 3.75 1.83-1.83Z"/></svg>';
    const svgCopy = '<svg viewBox="0 0 24 24"><path d="M16 1H4c-1.1 0-2 .9-2 2v14h2V3h12V1Zm3 4H8c-1.1 0-2 .9-2 2v14c0 1.1.9 2 2 2h11c1.1 0 2-.9 2-2V7c0-1.1-.9-2-2-2Zm0 16H8V7h11v14Z"/></svg>';
    const svgShell = '<svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2Zm0 18c-4.41 0-8-3.59-8-8s3.59-8 8-8 8 3.59 8 8-3.59 8-8 8Z"/><circle cx="12" cy="12" r="3"/></svg>';
    const svgTrash = '<svg viewBox="0 0 24 24"><path d="M6 19c0 1.1.9 2 2 2h8c1.1 0 2-.9 2-2V7H6v12ZM19 4h-3.5l-1-1h-5l-1 1H5v2h14V4Z"/></svg>';

    const slug = agentCardEl.getAttribute('data-agent') ?? 'canary';
    showContextMenu(e.clientX, e.clientY, [
      { label: 'Focus Camera', icon: svgEye, action: () => refocusBtn?.click() },
      { label: 'Rename', icon: svgEdit, action: () => {
        const newName = prompt('New name:');
        if (newName?.trim()) agentMenuCallbacks.forEach((cb) => cb('rename:' + newName.trim(), slug));
      }},
      { label: 'Duplicate', icon: svgCopy, action: () => agentMenuCallbacks.forEach((cb) => cb('duplicate', slug)) },
      'sep',
      { label: 'Change Shell', icon: svgShell, action: () => {
        const newShell = prompt('Shell slug:');
        if (newShell?.trim()) agentMenuCallbacks.forEach((cb) => cb('change-shell:' + newShell.trim(), slug));
      }},
      'sep',
      { label: 'Remove', icon: svgTrash, danger: true, action: () => {
        if (confirm(`Remove agent "${slug}"?`)) agentMenuCallbacks.forEach((cb) => cb('remove', slug));
      }},
    ]);
  });

  // --- Transcript ---
  function addTranscript(role: 'user' | 'agent', text: string): void {
    const timeStr = formatTime();
    messageLog.push({ role, text, time: timeStr });

    const msg = document.createElement('div');
    msg.className = `msg ${role}`;

    if (role === 'agent') {
      const avatar = document.createElement('div');
      avatar.className = 'msg-avatar';
      avatar.textContent = 'A';
      msg.appendChild(avatar);
    }

    const content = document.createElement('div');
    content.className = 'msg-content';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    if (role === 'agent') {
      bubble.innerHTML = marked.parse(text) as string;
    } else {
      bubble.textContent = text;
    }
    content.appendChild(bubble);

    const time = document.createElement('div');
    time.className = 'msg-time';
    time.textContent = timeStr;
    content.appendChild(time);

    msg.appendChild(content);
    transcriptEl.appendChild(msg);

    while (transcriptEl.children.length > MAX_TRANSCRIPT_MESSAGES) {
      transcriptEl.removeChild(transcriptEl.firstChild!);
    }

    transcriptEl.scrollTo({ top: transcriptEl.scrollHeight, behavior: 'smooth' });
  }

  // --- Tool Traces ---
  function addToolTrace(toolName: string, description?: string): void {
    const msg = document.createElement('div');
    msg.className = 'msg tool-trace';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';

    const header = document.createElement('div');
    header.className = 'tool-trace-header';
    header.innerHTML = `<svg class="tool-trace-icon" viewBox="0 0 24 24" fill="currentColor"><path d="M22.7 19l-9.1-9.1c.9-2.3.4-5-1.5-6.9-2-2-5-2.4-7.4-1.3L9 6 6 9 1.6 4.7C.4 7.1.9 10.1 2.9 12.1c1.9 1.9 4.6 2.4 6.9 1.5l9.1 9.1c.4.4 1 .4 1.4 0l2.3-2.3c.5-.4.5-1.1.1-1.4Z"/></svg><span class="tool-trace-name">${toolName}</span>`;
    bubble.appendChild(header);

    if (description) {
      const desc = document.createElement('div');
      desc.className = 'tool-trace-desc';
      desc.textContent = description;
      bubble.appendChild(desc);
    }

    msg.appendChild(bubble);
    transcriptEl.appendChild(msg);

    while (transcriptEl.children.length > MAX_TRANSCRIPT_MESSAGES) {
      transcriptEl.removeChild(transcriptEl.firstChild!);
    }

    transcriptEl.scrollTo({ top: transcriptEl.scrollHeight, behavior: 'smooth' });
  }

  // --- Busy Indicator ---
  let busyEl: HTMLDivElement | null = null;

  function showBusy(label?: string): void {
    hideBusy();
    const msg = document.createElement('div');
    msg.className = 'msg busy';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    bubble.innerHTML = `<div class="busy-dots"><span></span><span></span><span></span></div>${label ? `<span>${label}</span>` : ''}`;

    msg.appendChild(bubble);
    transcriptEl.appendChild(msg);
    busyEl = msg;

    transcriptEl.scrollTo({ top: transcriptEl.scrollHeight, behavior: 'smooth' });
  }

  function hideBusy(): void {
    if (busyEl) {
      busyEl.remove();
      busyEl = null;
    }
  }

  // --- Chat History ---
  const messageLog: Array<{ role: string; text: string; time: string }> = [];
  const newChatCallbacks: Array<() => void> = [];
  const chatSelectCallbacks: Array<(chatId: string) => void> = [];
  let activeChatId: string | null = null;

  function getMessages(): Array<{ role: string; text: string; time: string }> {
    return [...messageLog];
  }

  function clearTranscript(): void {
    messageLog.length = 0;
    transcriptEl.innerHTML = '';
  }

  function loadMessages(messages: Array<{ role: string; text: string; time: string }>): void {
    clearTranscript();
    for (const m of messages) {
      addTranscriptWithTime(m.role as 'user' | 'agent', m.text, m.time);
    }
    transcriptEl.scrollTop = transcriptEl.scrollHeight;
  }

  function addTranscriptWithTime(role: 'user' | 'agent', text: string, timeStr: string): void {
    const msg = document.createElement('div');
    msg.className = `msg ${role}`;

    if (role === 'agent') {
      const avatar = document.createElement('div');
      avatar.className = 'msg-avatar';
      avatar.textContent = 'A';
      msg.appendChild(avatar);
    }

    const content = document.createElement('div');
    content.className = 'msg-content';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    if (role === 'agent') {
      bubble.innerHTML = marked.parse(text) as string;
    } else {
      bubble.textContent = text;
    }
    content.appendChild(bubble);

    const time = document.createElement('div');
    time.className = 'msg-time';
    time.textContent = timeStr;
    content.appendChild(time);

    msg.appendChild(content);
    transcriptEl.appendChild(msg);

    messageLog.push({ role, text, time: timeStr });
  }

  function onNewChat(cb: () => void): void { newChatCallbacks.push(cb); }
  function onChatSelect(cb: (chatId: string) => void): void { chatSelectCallbacks.push(cb); }

  function setChatList(_chats: Array<{ id: string; title: string; createdAt: string; messageCount: number }>): void {
    // Superseded by setRecentChats — kept for interface compat
  }

  function setActiveChatId(id: string | null): void {
    activeChatId = id;
    document.querySelectorAll('.convo-chip[data-chat-id]').forEach((el) => {
      el.classList.toggle('active', el.getAttribute('data-chat-id') === id);
    });
  }

  function setRecentChats(chats: Array<{ id: string; title: string; createdAt: string; lastMessage: string; shellName: string; shellSlug: string }>): void {
    const strip = document.getElementById('recent-convo-list');
    if (!strip) return;
    strip.innerHTML = '';

    const newChip = document.createElement('button');
    newChip.className = 'convo-chip convo-chip-new';
    newChip.textContent = '+ New';
    newChip.addEventListener('click', () => newChatCallbacks.forEach((cb) => cb()));
    strip.appendChild(newChip);

    for (const chat of chats) {
      const chip = document.createElement('button');
      chip.className = 'convo-chip' + (chat.id === activeChatId ? ' active' : '');
      chip.setAttribute('data-chat-id', chat.id);

      const now = new Date();
      const created = new Date(chat.createdAt);
      const diffMin = Math.floor((now.getTime() - created.getTime()) / 60000);
      let timeLabel = '';
      if (diffMin < 1) timeLabel = 'now';
      else if (diffMin < 60) timeLabel = `${diffMin}m`;
      else if (diffMin < 1440) timeLabel = `${Math.floor(diffMin / 60)}h`;
      else timeLabel = created.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });

      const title = chat.title.length > 24 ? chat.title.slice(0, 22) + '…' : chat.title;
      chip.innerHTML = `${title} <span class="convo-chip-time">${timeLabel}</span>`;

      chip.addEventListener('click', () => {
        chatSelectCallbacks.forEach((cb) => cb(chat.id));
      });

      strip.appendChild(chip);
    }
  }

  // --- Subtitle ---
  function showSubtitle(speaker: string, text: string, duration?: number): void {
    if (subtitleHideTimer) {
      clearTimeout(subtitleHideTimer);
      subtitleHideTimer = null;
    }
    if (subtitleSpeakerEl) subtitleSpeakerEl.textContent = speaker + ':';
    if (subtitleTextEl) subtitleTextEl.textContent = text;
    if (subtitleEl) subtitleEl.classList.add('visible');
    if (duration !== 0) subtitleHideTimer = setTimeout(() => hideSubtitle(), duration ?? SUBTITLE_HOLD_MS);
  }

  function hideSubtitle(): void {
    if (subtitleHideTimer) { clearTimeout(subtitleHideTimer); subtitleHideTimer = null; }
    if (subtitleEl) subtitleEl.classList.remove('visible');
  }

  // --- Drawer ---
  function syncDrawerBtn(): void {
    drawerToggleBtn?.classList.toggle('active', drawerEl?.classList.contains('open'));
  }

  function toggleDrawer(): void {
    drawerEl?.classList.toggle('open');
    syncDrawerBtn();
  }

  function isDrawerOpen(): boolean {
    return drawerEl?.classList.contains('open') ?? false;
  }

  drawerToggleBtn?.addEventListener('click', toggleDrawer);
  drawerCloseBtn?.addEventListener('click', () => {
    drawerEl?.classList.remove('open');
    syncDrawerBtn();
  });

  // --- AR ---
  function showARButton(visible: boolean): void {
    arBtn?.classList.toggle('visible', visible);
  }

  function setMicActive(active: boolean): void {
    micBtn?.classList.toggle('active', active);
    micBtn?.setAttribute('aria-pressed', String(active));
    micBtn?.setAttribute('aria-label', active ? 'Stop voice input' : 'Start voice input');
    if (micBtn) micBtn.title = active ? 'Stop voice input' : 'Start voice input';
  }

  function focusInput(): void { textInput?.focus(); }

  function isDark(): boolean {
    return themeUsesDarkPanels(getTheme());
  }

  function toggleTheme(): void {
    themeToggleBtn?.click();
  }

  function onThemeChange(cb: (theme: UITheme) => void): void {
    themeCallbacks.push(cb);
  }

  // Suppress default context menu on non-input elements
  document.addEventListener('contextmenu', (e) => {
    const tag = (e.target as HTMLElement)?.tagName?.toLowerCase();
    if (tag !== 'input' && tag !== 'textarea') {
      e.preventDefault();
    }
  });

  // --- Animated placeholder ---
  const placeholderEl = document.getElementById('command-placeholder') as HTMLElement | null;
  let phIndex = 0;
  let phTimer: ReturnType<typeof setInterval> | null = null;

  function updatePlaceholderVisibility(): void {
    if (!placeholderEl) return;
    const hasValue = textInput && textInput.value.length > 0;
    const hasFocus = document.activeElement === textInput;
    if (hasValue) {
      placeholderEl.classList.add('hidden');
    } else {
      placeholderEl.classList.remove('hidden');
      if (hasFocus) {
        placeholderEl.style.opacity = '0.5';
      } else {
        placeholderEl.style.opacity = '';
      }
    }
  }

  function cyclePlaceholder(): void {
    if (!placeholderEl) return;
    const hasValue = textInput && textInput.value.length > 0;
    if (hasValue) return;

    placeholderEl.classList.add('fade-out');
    setTimeout(() => {
      phIndex = (phIndex + 1) % PLACEHOLDER_HINTS.length;
      placeholderEl.textContent = PLACEHOLDER_HINTS[phIndex];

      placeholderEl.classList.add('no-transition');
      placeholderEl.classList.remove('fade-out');
      placeholderEl.style.opacity = '0';
      placeholderEl.style.transform = 'translateY(calc(-50% + 10px))';

      // eslint-disable-next-line @typescript-eslint/no-unused-expressions
      placeholderEl.offsetHeight;

      placeholderEl.classList.remove('no-transition');
      placeholderEl.style.opacity = '';
      placeholderEl.style.transform = '';
      updatePlaceholderVisibility();
    }, PLACEHOLDER_FADE_MS);
  }

  if (placeholderEl) {
    placeholderEl.textContent = PLACEHOLDER_HINTS[0];
    phTimer = setInterval(cyclePlaceholder, PLACEHOLDER_CYCLE_MS);
  }

  function autoResizeTextarea(): void {
    if (!textInput) return;
    textInput.style.height = 'auto';
    textInput.style.height = textInput.scrollHeight + 'px';
    textInput.style.overflowY = textInput.scrollHeight > 120 ? 'auto' : 'hidden';
  }

  textInput?.addEventListener('input', () => {
    updatePlaceholderVisibility();
    autoResizeTextarea();
  });
  textInput?.addEventListener('focus', updatePlaceholderVisibility);
  textInput?.addEventListener('blur', updatePlaceholderVisibility);

  // --- Create Agent Modal ---
  const createModal = document.getElementById('create-modal') as HTMLElement;
  const createBtn = document.getElementById('create-agent-btn') as HTMLButtonElement;
  const createCloseBtn = document.getElementById('create-modal-close') as HTMLButtonElement;
  const createCancelBtn = document.getElementById('create-modal-cancel') as HTMLButtonElement;
  const createSubmitBtn = document.getElementById('create-modal-submit') as HTMLButtonElement;
  const createNameInput = document.getElementById('create-name') as HTMLInputElement;
  const createVoiceSelect = document.getElementById('create-voice') as HTMLSelectElement;

  const createAgentCallbacks: Array<(data: CreateAgentData) => void> = [];

  function openCreateModal(): void {
    createNameInput.value = '';
    createVoiceSelect.value = 'en-US-JennyNeural';
    createSubmitBtn.disabled = true;
    createModal?.classList.add('open');
    setTimeout(() => createNameInput?.focus(), 80);
  }

  function closeCreateModal(): void {
    createModal?.classList.remove('open');
  }

  createBtn?.addEventListener('click', openCreateModal);
  createCloseBtn?.addEventListener('click', closeCreateModal);
  createCancelBtn?.addEventListener('click', closeCreateModal);

  createModal?.addEventListener('click', (e) => {
    if (e.target === createModal) closeCreateModal();
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && createModal?.classList.contains('open')) {
      closeCreateModal();
    }
  });

  createNameInput?.addEventListener('input', () => {
    const valid = createNameInput.value.trim().length > 0;
    createSubmitBtn.disabled = !valid;
  });

  createSubmitBtn?.addEventListener('click', () => {
    const name = createNameInput.value.trim();
    if (!name) return;
    createSubmitBtn.disabled = true;
    createSubmitBtn.textContent = 'Creating...';

    const data: CreateAgentData = {
      name,
      voice: createVoiceSelect.value,
    };

    createAgentCallbacks.forEach((cb) => cb(data));
  });

  function onCreateAgent(cb: (data: CreateAgentData) => void): void {
    createAgentCallbacks.push(cb);
  }

  function resetCreateModal(): void {
    createSubmitBtn.disabled = false;
    createSubmitBtn.textContent = 'Create Shell';
    closeCreateModal();
  }

  // Expose resetCreateModal on the module for external use
  (window as any).__ngramArResetCreateModal = resetCreateModal;

  // --- DevTools Panel ---
  const devpanel = document.getElementById('devpanel') as HTMLElement;
  const devpanelClose = document.getElementById('devpanel-close') as HTMLButtonElement;
  const devpanelResize = document.getElementById('devpanel-resize') as HTMLElement;
  const devtoolsToggleBtn = document.getElementById('devtools-toggle') as HTMLButtonElement;
  const devpanelTabs = devpanel?.querySelectorAll('.devpanel-tab');
  const protoLog = document.getElementById('proto-log') as HTMLElement;
  const protoFilter = document.getElementById('proto-filter') as HTMLInputElement;
  const protoCount = document.getElementById('proto-count') as HTMLElement;
  const protoClear = document.getElementById('proto-clear') as HTMLButtonElement;
  const protoBadgeFilters = devpanel?.querySelectorAll('.proto-badge-filter');
  const shellYamlEl = document.getElementById('shell-yaml') as HTMLTextAreaElement;

  const testActionCallbacks: Array<(actionType: string, args: Record<string, unknown>) => void> = [];

  let devpanelOpen = false;
  let protoMessages: Array<{
    time: number;
    direction: 'inbound' | 'outbound';
    type: string;
    category: string;
    preview: string;
    raw: string;
  }> = [];
  let protoFilterText = '';
  let protoCategory = 'all';

  function toggleDevPanel(): void {
    devpanelOpen = !devpanelOpen;
    document.body.classList.toggle('devpanel-open', devpanelOpen);
    devtoolsToggleBtn?.classList.toggle('active', devpanelOpen);
    if (devpanelOpen && shellYamlEl?.textContent === 'Loading shell configuration...') {
      loadShellConfig();
    }
  }

  devtoolsToggleBtn?.addEventListener('click', toggleDevPanel);
  devpanelClose?.addEventListener('click', () => {
    devpanelOpen = false;
    document.body.classList.remove('devpanel-open');
    devtoolsToggleBtn?.classList.remove('active');
  });

  // Keyboard shortcut: Ctrl+Shift+D
  document.addEventListener('keydown', (e) => {
    if (e.ctrlKey && e.shiftKey && e.key === 'D') {
      e.preventDefault();
      toggleDevPanel();
    }
  });

  // Tab switching
  devpanelTabs?.forEach((tab) => {
    tab.addEventListener('click', () => {
      const target = tab.getAttribute('data-tab');
      if (!target) return;
      devpanelTabs.forEach((t) => t.classList.remove('active'));
      tab.classList.add('active');
      devpanel.querySelectorAll('.devpanel-view').forEach((v) => v.classList.remove('active'));
      const view = document.getElementById(`devview-${target}`);
      view?.classList.add('active');
    });
  });

  // Devpanel vertical resize
  if (devpanelResize) {
    let startY = 0;
    let startH = 0;

    function onDevResize(e: PointerEvent): void {
      const dy = startY - e.clientY;
      const h = Math.min(750, Math.max(175, startH + dy));
      document.documentElement.style.setProperty('--devpanel-h', h + 'px');
      document.documentElement.style.setProperty('--scene-devpanel-h', h / UI_SCALE + 'px');
    }

    function onDevResizeEnd(): void {
      document.body.classList.remove('devpanel-resizing');
      devpanelResize.classList.remove('dragging');
      document.removeEventListener('pointermove', onDevResize);
      document.removeEventListener('pointerup', onDevResizeEnd);
    }

    devpanelResize.addEventListener('pointerdown', (e: PointerEvent) => {
      e.preventDefault();
      startY = e.clientY;
      startH = devpanel?.offsetHeight ?? 325;
      document.body.classList.add('devpanel-resizing', 'panel-resizing');
      devpanelResize.classList.add('dragging');
      document.addEventListener('pointermove', onDevResize);
      document.addEventListener('pointerup', () => {
        document.body.classList.remove('devpanel-resizing', 'panel-resizing');
        devpanelResize.classList.remove('dragging');
        document.removeEventListener('pointermove', onDevResize);
      });
    });
  }

  // Protocol Inspector
  function getCategory(type: string): string {
    if (type.startsWith('action:')) return 'action';
    if (type.startsWith('event:')) return 'event';
    if (type.startsWith('shell:')) return 'shell';
    return 'other';
  }

  function getPreview(msg: Record<string, unknown>): string {
    const type = msg.type as string;
    if (type === 'action:speak') return (msg.text as string) ?? '';
    if (type === 'action:move_to') {
      const t = msg.target;
      if (typeof t === 'string') return `→ ${t}`;
      if (t && typeof t === 'object') return `→ (${(t as any).x},${(t as any).y},${(t as any).z})`;
    }
    if (type === 'action:emote') return `${msg.emotion} (${msg.intensity ?? '?'})`;
    if (type === 'action:gesture') return msg.gesture as string ?? '';
    if (type === 'event:user_speech') return (msg.text as string) ?? '';
    if (type === 'event:user_proximity') return `dist=${msg.distance}`;
    if (type === 'shell:config') return (msg as any).shell?.name ?? '';
    return '';
  }

  function formatTimestamp(ts: number): string {
    const d = new Date(ts);
    const hms = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    return hms + '.' + String(d.getMilliseconds()).padStart(3, '0');
  }

  function renderProtoEntry(entry: typeof protoMessages[0]): HTMLElement[] {
    const row = document.createElement('div');
    row.className = 'proto-entry';

    const time = document.createElement('span');
    time.className = 'proto-time';
    time.textContent = formatTimestamp(entry.time);

    const dir = document.createElement('span');
    dir.className = `proto-dir ${entry.direction}`;
    dir.textContent = entry.direction === 'inbound' ? '←' : '→';
    dir.title = entry.direction === 'inbound' ? 'From shell (event)' : 'To shell (action)';

    const badge = document.createElement('span');
    badge.className = `proto-type ${entry.category}`;
    badge.textContent = entry.type;

    const preview = document.createElement('span');
    preview.className = 'proto-preview';
    preview.textContent = entry.preview;

    row.appendChild(time);
    row.appendChild(dir);
    row.appendChild(badge);
    row.appendChild(preview);

    const jsonEl = document.createElement('div');
    jsonEl.className = 'proto-json';
    jsonEl.textContent = entry.raw;

    row.addEventListener('click', () => {
      row.classList.toggle('expanded');
    });

    return [row, jsonEl];
  }

  function shouldShowEntry(entry: typeof protoMessages[0]): boolean {
    if (protoCategory !== 'all' && entry.category !== protoCategory) return false;
    if (protoFilterText && !entry.type.includes(protoFilterText) && !entry.preview.toLowerCase().includes(protoFilterText)) return false;
    return true;
  }

  function refreshProtoLog(): void {
    if (!protoLog) return;
    protoLog.innerHTML = '';
    let count = 0;
    for (const entry of protoMessages) {
      if (!shouldShowEntry(entry)) continue;
      const [row, json] = renderProtoEntry(entry);
      protoLog.appendChild(row);
      protoLog.appendChild(json);
      count++;
    }
    if (protoCount) protoCount.textContent = `${count} message${count !== 1 ? 's' : ''}`;
  }

  function logMessage(direction: 'inbound' | 'outbound', msg: Record<string, unknown>): void {
    const type = (msg.type as string) ?? 'unknown';
    const entry = {
      time: Date.now(),
      direction,
      type,
      category: getCategory(type),
      preview: getPreview(msg),
      raw: JSON.stringify(msg, null, 2),
    };
    protoMessages.push(entry);

    // Keep max 500 messages
    if (protoMessages.length > 500) {
      protoMessages = protoMessages.slice(-500);
      refreshProtoLog();
      return;
    }

    if (shouldShowEntry(entry)) {
      const [row, json] = renderProtoEntry(entry);
      protoLog?.appendChild(row);
      protoLog?.appendChild(json);
      if (protoCount) {
        const count = protoLog?.querySelectorAll('.proto-entry').length ?? 0;
        protoCount.textContent = `${count} message${count !== 1 ? 's' : ''}`;
      }
      protoLog?.scrollTo({ top: protoLog.scrollHeight, behavior: 'smooth' });
    }
  }

  protoFilter?.addEventListener('input', () => {
    protoFilterText = protoFilter.value.trim().toLowerCase();
    refreshProtoLog();
  });

  protoBadgeFilters?.forEach((btn) => {
    btn.addEventListener('click', () => {
      protoBadgeFilters.forEach((b) => b.classList.remove('active'));
      btn.classList.add('active');
      protoCategory = btn.getAttribute('data-filter') ?? 'all';
      refreshProtoLog();
    });
  });

  protoClear?.addEventListener('click', () => {
    protoMessages = [];
    refreshProtoLog();
  });

  // Action Tester
  const actionBtns = devpanel?.querySelectorAll('.action-test-btn');
  actionBtns?.forEach((btn) => {
    btn.addEventListener('click', () => {
      const actionType = btn.getAttribute('data-action');
      const argsStr = btn.getAttribute('data-args');
      if (!actionType) return;
      let args: Record<string, unknown> = {};
      try { args = JSON.parse(argsStr ?? '{}'); } catch {}
      testActionCallbacks.forEach((cb) => cb(actionType, args));

      btn.classList.add('fired');
      setTimeout(() => btn.classList.remove('fired'), 400);
    });
  });

  function onTestAction(cb: (actionType: string, args: Record<string, unknown>) => void): void {
    testActionCallbacks.push(cb);
  }

  // Shell Config
  const shellSaveBtn = document.getElementById('shell-save-btn') as HTMLButtonElement;
  const shellSaveStatus = document.getElementById('shell-save-status') as HTMLElement;
  const shellSaveCallbacks: ShellSaveCallback[] = [];
  let currentShellSlug = '';

  async function loadShellConfig(): Promise<void> {
    try {
      const slug = currentShellSlug || '';
      const url = slug ? `/api/shell-config?shell=${encodeURIComponent(slug)}` : '/api/shell-config';
      const res = await fetch(url);
      if (res.ok) {
        const yaml = await res.text();
        if (shellYamlEl) shellYamlEl.value = yaml;
      }
    } catch { /* ignore */ }
  }

  shellSaveBtn?.addEventListener('click', async () => {
    if (!shellYamlEl?.value) return;
    shellSaveBtn.disabled = true;
    if (shellSaveStatus) { shellSaveStatus.textContent = 'Saving...'; shellSaveStatus.className = 'shell-save-status'; }

    try {
      for (const cb of shellSaveCallbacks) {
        await cb(shellYamlEl.value);
      }
      if (shellSaveStatus) {
        shellSaveStatus.textContent = 'Saved';
        shellSaveStatus.className = 'shell-save-status saved';
        setTimeout(() => { shellSaveStatus.textContent = ''; shellSaveStatus.className = 'shell-save-status'; }, 3000);
      }
    } catch (err: any) {
      if (shellSaveStatus) {
        shellSaveStatus.textContent = err?.message ?? 'Save failed';
        shellSaveStatus.className = 'shell-save-status error';
      }
    }
    shellSaveBtn.disabled = false;
  });

  function onShellSave(cb: ShellSaveCallback): void { shellSaveCallbacks.push(cb); }
  function setActiveShellSlug(slug: string): void { currentShellSlug = slug; }

  // --- Terminal sidebar toggle ---
  const terminalToggleCallbacks: Array<() => void> = [];
  const terminalToggleBtn = document.getElementById('terminal-toggle');
  terminalToggleBtn?.addEventListener('click', () => { terminalToggleCallbacks.forEach(cb => cb()); });
  function onTerminalToggle(cb: () => void): void { terminalToggleCallbacks.push(cb); }

  // --- Agent Context Menu Actions ---
  const agentMenuCallbacks: AgentMenuCallback[] = [];
  function onAgentMenu(cb: AgentMenuCallback): void { agentMenuCallbacks.push(cb); }

  // Rewire context menu on the default agent card
  agentCardEl?.removeEventListener('contextmenu', agentCardEl as any);

  // --- Agent Details Modal ---
  const detailsModal = document.getElementById('agent-details-modal') as HTMLElement;
  const detailsName = document.getElementById('agent-details-name') as HTMLElement;
  const detailsDesc = document.getElementById('agent-details-desc') as HTMLElement;
  const detailsGrid = document.getElementById('agent-details-grid') as HTMLElement;
  const detailsClose = document.getElementById('agent-details-close') as HTMLButtonElement;
  const detailsCancel = document.getElementById('agent-details-cancel') as HTMLButtonElement;
  const detailsRemove = document.getElementById('agent-details-remove') as HTMLButtonElement;
  let detailsSlug = '';

  function closeDetailsModal(): void {
    detailsModal?.classList.remove('open');
  }

  detailsClose?.addEventListener('click', closeDetailsModal);
  detailsCancel?.addEventListener('click', closeDetailsModal);
  detailsModal?.addEventListener('click', (e) => {
    if (e.target === detailsModal) closeDetailsModal();
  });

  detailsRemove?.addEventListener('click', () => {
    if (detailsSlug && confirm(`Remove agent "${detailsSlug}"?`)) {
      agentMenuCallbacks.forEach((cb) => cb('remove', detailsSlug));
      closeDetailsModal();
    }
  });

  function openAgentDetails(slug: string): void {
    detailsSlug = slug;
    detailsName.textContent = slug;
    detailsDesc.textContent = 'Loading…';
    detailsGrid.innerHTML = '';
    detailsModal?.classList.add('open');

    fetch(`/api/shells/${encodeURIComponent(slug)}`).then((r) => r.json()).then((shell: any) => {
      detailsName.textContent = shell.name ?? slug;
      detailsDesc.textContent = shell.description ?? '';

      const rows: Array<[string, string]> = [];
      if (shell.binding?.type) rows.push(['Binding', shell.binding.type]);
      if (shell.binding?.baseUrl) rows.push(['Endpoint', `<code>${shell.binding.baseUrl}</code>`]);
      if (shell.binding?.model) rows.push(['Model', shell.binding.model]);
      if (shell.voice?.provider) rows.push(['Voice', `${shell.voice.provider}${shell.voice.voice ? ' · ' + shell.voice.voice.slice(0, 8) + '…' : ''}`]);
      if (shell.model) rows.push(['3D Model', `<code>${shell.model}</code>`]);
      if (shell.scale) rows.push(['Scale', String(shell.scale)]);
      if (shell.behaviorPack?.length) rows.push(['Behaviors', shell.behaviorPack.join(', ')]);
      if (shell.animationPack) {
        const anims = typeof shell.animationPack === 'object' ? Object.keys(shell.animationPack) : [];
        if (anims.length) rows.push(['Animations', anims.join(', ')]);
      }

      detailsGrid.innerHTML = rows.map(([label, value]) =>
        `<div class="agent-details-label">${label}</div><div class="agent-details-value">${value}</div>`
      ).join('');
    }).catch(() => {
      detailsDesc.textContent = 'Failed to load details.';
    });
  }

  // Listen for right-click on agent cards (delegated from agent-list)
  document.querySelector('.agent-list')?.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const card = (e.target as HTMLElement).closest('.agent-card') as HTMLElement | null;
    if (!card) return;
    const slug = card.getAttribute('data-agent');
    if (slug) openAgentDetails(slug);
  });

  // Custom Action Builder
  const actionBuilderJson = document.getElementById('action-builder-json') as HTMLTextAreaElement;
  const actionBuilderSend = document.getElementById('action-builder-send') as HTMLButtonElement;
  const actionRecordBtn = document.getElementById('action-record-btn') as HTMLButtonElement;
  const customActionCallbacks: CustomActionCallback[] = [];
  let isRecording = false;

  actionBuilderSend?.addEventListener('click', () => {
    if (!actionBuilderJson?.value.trim()) return;
    try {
      const action = JSON.parse(actionBuilderJson.value);
      customActionCallbacks.forEach((cb) => cb(action));
      actionBuilderSend.classList.add('fired');
      setTimeout(() => actionBuilderSend.classList.remove('fired'), 400);
    } catch {
      actionBuilderJson.style.borderColor = 'var(--error)';
      setTimeout(() => { actionBuilderJson.style.borderColor = ''; }, 1000);
    }
  });

  actionRecordBtn?.addEventListener('click', () => {
    isRecording = !isRecording;
    actionRecordBtn.classList.toggle('recording', isRecording);
    actionRecordBtn.textContent = isRecording ? 'Stop' : 'Record';
  });

  const origLogMessage = logMessage;
  function logMessageWrapper(direction: 'inbound' | 'outbound', msg: Record<string, unknown>): void {
    origLogMessage(direction, msg);
    if (isRecording && direction === 'inbound' && actionBuilderJson) {
      const stripped = { ...msg };
      delete stripped.timestamp;
      delete stripped.sessionId;
      delete stripped.audioData;
      delete stripped.visemes;
      actionBuilderJson.value = JSON.stringify(stripped, null, 2);
    }
  }

  function onCustomAction(cb: CustomActionCallback): void { customActionCallbacks.push(cb); }

  const devtools: DevToolsHandle = { logMessage: logMessageWrapper };

  // --- Settings Modal ---
  const settingsModal = document.getElementById('settings-modal') as HTMLElement;
  const settingsBtn = document.getElementById('settings-btn') as HTMLButtonElement;
  const settingsClose = document.getElementById('settings-close') as HTMLButtonElement;
  const settingsThemeSelect = document.getElementById('settings-theme') as HTMLSelectElement;
  const settingsEnvPreset = document.getElementById('settings-env-preset') as HTMLSelectElement;
  const settingsObjCount = document.getElementById('settings-obj-count') as HTMLElement;
  const settingsClearObjects = document.getElementById('settings-clear-objects') as HTMLButtonElement;
  const settingsClearDrawings = document.getElementById('settings-clear-drawings') as HTMLButtonElement;
  const settingsClearEnv = document.getElementById('settings-clear-env') as HTMLButtonElement;
  const settingsClearAll = document.getElementById('settings-clear-all') as HTMLButtonElement;
  const brainModeButtons = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-brain-mode]'));
  const brainProviderPicker = document.getElementById('brain-provider-picker') as HTMLElement;
  const brainProviderGrid = document.getElementById('brain-provider-grid') as HTMLElement;
  const brainProviderCount = document.getElementById('brain-provider-count') as HTMLElement;
  const brainActiveLabel = document.getElementById('brain-active-label') as HTMLElement;
  const brainApiKey = document.getElementById('brain-api-key') as HTMLInputElement;
  const brainKeyField = document.getElementById('brain-key-field') as HTMLElement;
  const brainKeyToggle = document.getElementById('brain-key-toggle') as HTMLButtonElement;
  const brainModel = document.getElementById('brain-model') as HTMLInputElement;
  const brainModelField = document.getElementById('brain-model-field') as HTMLElement;
  const brainMemoryFields = document.getElementById('brain-memory-fields') as HTMLElement;
  const brainEmbeddingMode = document.getElementById('brain-embedding-mode') as HTMLSelectElement;
  const brainEmbeddingModel = document.getElementById('brain-embedding-model') as HTMLInputElement;
  const brainEmbeddingModelField = document.getElementById('brain-embedding-model-field') as HTMLElement;
  const brainBaseUrl = document.getElementById('brain-base-url') as HTMLInputElement;
  const brainSave = document.getElementById('brain-save') as HTMLButtonElement;
  const brainFeedback = document.getElementById('brain-feedback') as HTMLElement;
  const brainFrontierCopy = document.getElementById('brain-frontier-copy') as HTMLElement;

  type BrainMode = 'local' | 'private' | 'frontier';
  type BrainEmbeddingMode = 'provider' | 'existing';
  type BrainProvider = { id: string; name: string; mark: string; accent: string; defaultModel?: string; defaultEmbeddingModel?: string };
  type BrainPublicConfig = {
    configured: boolean;
    mode: BrainMode;
    provider: string;
    model: string;
    baseUrl: string;
    hasApiKey: boolean;
    embeddingMode?: BrainEmbeddingMode;
    embeddingModel?: string;
    lastFrontierProvider?: string;
    savedFrontierProfiles?: Record<string, {
      provider: string;
      model: string;
      baseUrl: string;
      hasApiKey: boolean;
      embeddingMode?: BrainEmbeddingMode;
      embeddingModel?: string;
    }>;
  };
  type BrainDraft = { provider: string; model: string; baseUrl: string; embeddingMode: BrainEmbeddingMode; embeddingModel: string };
  const brainDrafts: Record<BrainMode, BrainDraft> = {
    local: { provider: 'local', model: '', baseUrl: '', embeddingMode: 'provider', embeddingModel: '' },
    private: { provider: 'remote_gateway', model: '', baseUrl: '', embeddingMode: 'provider', embeddingModel: '' },
    frontier: { provider: 'openai', model: 'gpt-6-astra', baseUrl: '', embeddingMode: 'provider', embeddingModel: 'text-embedding-3-small' },
  };
  let brainMode: BrainMode = 'private';
  let brainProviders: BrainProvider[] = [];
  let brainConfig: BrainPublicConfig | null = null;
  let brainLoaded = false;

  function brainProviderName(id: string): string {
    return brainProviders.find((provider) => provider.id === id)?.name ?? id;
  }

  function rememberBrainDraft(): void {
    const draft = brainDrafts[brainMode];
    if (!draft) return;
    draft.model = brainModel?.value.trim() ?? '';
    draft.baseUrl = brainBaseUrl?.value.trim() ?? '';
    draft.embeddingMode = (brainEmbeddingMode?.value as BrainEmbeddingMode) || 'provider';
    draft.embeddingModel = brainEmbeddingModel?.value.trim() ?? '';
  }

  function setBrainFeedback(message: string, kind: 'success' | 'error' | '' = ''): void {
    if (!brainFeedback) return;
    brainFeedback.textContent = message;
    brainFeedback.className = `brain-feedback${message ? ' visible' : ''}${kind ? ` ${kind}` : ''}`;
  }

  function updateBrainActiveLabel(config: BrainPublicConfig | null): void {
    if (!brainActiveLabel) return;
    if (!config?.configured) {
      brainActiveLabel.textContent = 'Deployment brain';
      return;
    }
    if (config.mode === 'local') brainActiveLabel.textContent = config.model || 'Local';
    else if (config.mode === 'private') brainActiveLabel.textContent = config.model || 'Private GPU';
    else brainActiveLabel.textContent = config.model || brainProviderName(config.provider);
  }

  function renderBrainProviders(): void {
    if (!brainProviderGrid) return;
    brainProviderGrid.replaceChildren();
    const selected = brainDrafts.frontier.provider;
    for (const provider of brainProviders) {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = `brain-provider${provider.id === selected ? ' active' : ''}`;
      button.dataset.provider = provider.id;
      button.setAttribute('aria-pressed', provider.id === selected ? 'true' : 'false');
      const mark = document.createElement('span');
      mark.className = 'brain-provider-mark';
      const logo = document.createElement('img');
      logo.src = providerLogo(provider.id);
      logo.alt = '';
      logo.width = 24;
      logo.height = 24;
      logo.decoding = 'async';
      logo.addEventListener('error', () => { logo.src = providerLogo('custom'); }, { once: true });
      mark.append(logo);
      const name = document.createElement('span');
      name.className = 'brain-provider-name';
      name.textContent = provider.name;
      button.append(mark, name);
      button.addEventListener('click', () => selectBrainProvider(provider.id));
      brainProviderGrid.append(button);
    }
    if (brainProviderCount) brainProviderCount.textContent = `${brainProviders.filter(p => p.id !== 'custom').length} integrations + custom`;
  }

  function selectBrainProvider(providerId: string): void {
    const provider = brainProviders.find((item) => item.id === providerId);
    if (!provider) return;
    const changed = brainDrafts.frontier.provider !== providerId;
    brainDrafts.frontier.provider = providerId;
    if (changed) {
      const saved = brainConfig?.savedFrontierProfiles?.[providerId];
      brainDrafts.frontier.model = saved?.model ?? provider.defaultModel ?? '';
      brainDrafts.frontier.baseUrl = saved?.baseUrl ?? '';
      brainDrafts.frontier.embeddingMode = saved?.embeddingMode ?? 'provider';
      brainDrafts.frontier.embeddingModel = saved?.embeddingModel ?? provider.defaultEmbeddingModel ?? '';
      if (brainApiKey) brainApiKey.value = '';
      setBrainFeedback('');
    }
    renderBrainProviders();
    if (brainMode === 'frontier') applyBrainModeToForm();
  }

  function applyBrainModeToForm(): void {
    const frontier = brainMode === 'frontier';
    brainModeButtons.forEach((button) => {
      const active = button.dataset.brainMode === brainMode;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
      button.tabIndex = active ? 0 : -1;
    });
    brainProviderPicker?.classList.toggle('brain-hidden', !frontier);
    brainKeyField?.classList.toggle('brain-hidden', !frontier);
    brainMemoryFields?.classList.toggle('brain-hidden', !frontier);
    document.getElementById('brain-note-local')?.classList.toggle('visible', brainMode === 'local');
    document.getElementById('brain-note-private')?.classList.toggle('visible', brainMode === 'private');
    document.getElementById('brain-note-frontier')?.classList.toggle('visible', frontier);

    const draft = brainDrafts[brainMode];
    if (brainModel) brainModel.value = draft.model;
    if (brainBaseUrl) brainBaseUrl.value = draft.baseUrl;
    if (brainEmbeddingMode) brainEmbeddingMode.value = draft.embeddingMode;
    if (brainEmbeddingModel) brainEmbeddingModel.value = draft.embeddingModel;
    brainEmbeddingModelField?.classList.toggle('brain-hidden', !frontier || draft.embeddingMode === 'existing');
    const modelLabel = brainModelField?.querySelector('label');
    if (modelLabel) modelLabel.textContent = frontier ? 'Model ID' : 'Model ID (optional)';

    const provider = brainProviders.find((item) => item.id === brainDrafts.frontier.provider);
    if (frontier) {
      const name = provider?.name ?? 'provider';
      const providerTitle = document.getElementById('brain-provider-title');
      if (providerTitle) providerTitle.textContent = name;
      if (brainFrontierCopy) brainFrontierCopy.textContent = 'Configure the model for your entity.';
      if (brainApiKey) {
        const reusable = Boolean(brainConfig?.savedFrontierProfiles?.[provider?.id ?? '']?.hasApiKey
          || (brainConfig?.hasApiKey && brainConfig.mode === 'frontier' && brainConfig.provider === provider?.id));
        brainApiKey.placeholder = reusable ? 'Keep saved key' : 'Paste API key';
      }
      if (brainBaseUrl) brainBaseUrl.placeholder = provider?.id === 'custom' ? 'https://provider.example/v1' : 'Managed automatically';
      if (brainSave) brainSave.textContent = `Connect ${name}`;
    } else if (brainMode === 'local') {
      if (brainBaseUrl) brainBaseUrl.placeholder = 'http://localhost:11434';
      if (brainSave) brainSave.textContent = 'Use local brain';
    } else {
      if (brainBaseUrl) brainBaseUrl.placeholder = 'Use deployment gateway';
      if (brainSave) brainSave.textContent = 'Use private GPU';
    }
  }

  async function changeBrainMode(mode: BrainMode): Promise<void> {
    if (mode === brainMode) return;
    rememberBrainDraft();
    brainMode = mode;
    if (brainApiKey) brainApiKey.value = '';
    setBrainFeedback('');
    applyBrainModeToForm();
  }

  async function loadBrainSettings(force = false): Promise<void> {
    if (brainLoaded && !force) return;
    try {
      const response = await fetch('/api/brain', { cache: 'no-store' });
      if (!response.ok) throw new Error('Brain settings are unavailable.');
      const payload = await response.json();
      brainProviders = Array.isArray(payload.providers) ? payload.providers : [];
      brainConfig = payload.config ?? null;
      if (brainConfig?.configured) {
        brainMode = brainConfig.mode;
        brainDrafts[brainMode] = {
          provider: brainConfig.provider,
          model: brainConfig.model ?? '',
          baseUrl: brainConfig.baseUrl ?? '',
          embeddingMode: brainConfig.embeddingMode ?? 'provider',
          embeddingModel: brainConfig.embeddingModel ?? '',
        };
        const lastFrontierProvider = brainConfig.lastFrontierProvider ?? 'openai';
        const savedFrontier = brainConfig.savedFrontierProfiles?.[lastFrontierProvider];
        if (savedFrontier) {
          brainDrafts.frontier = {
            provider: savedFrontier.provider,
            model: savedFrontier.model ?? '',
            baseUrl: savedFrontier.baseUrl ?? '',
            embeddingMode: savedFrontier.embeddingMode ?? 'provider',
            embeddingModel: savedFrontier.embeddingModel ?? '',
          };
        }
      }
      if (!brainProviders.some((provider) => provider.id === brainDrafts.frontier.provider)) {
        brainDrafts.frontier.provider = brainProviders[0]?.id ?? 'openai';
      }
      renderBrainProviders();
      applyBrainModeToForm();
      updateBrainActiveLabel(brainConfig);
      brainLoaded = true;
    } catch (error) {
      updateBrainActiveLabel(null);
      setBrainFeedback(error instanceof Error ? error.message : 'Could not load brain settings.', 'error');
    }
  }

  async function saveBrainSettings(quickSwitch = false): Promise<void> {
    rememberBrainDraft();
    const draft = brainDrafts[brainMode];
    setBrainFeedback(quickSwitch ? 'Switching brain route…' : 'Connecting and checking the model…');
    if (brainSave) brainSave.disabled = true;
    brainModeButtons.forEach((button) => { button.disabled = true; });
    try {
      const response = await fetch('/api/brain', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          mode: brainMode,
          provider: draft.provider,
          model: draft.model,
          baseUrl: draft.baseUrl,
          apiKey: brainApiKey?.value ?? '',
          embeddingMode: draft.embeddingMode,
          embeddingModel: draft.embeddingModel,
        }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || 'The brain could not be connected.');
      brainConfig = payload.config;
      if (brainApiKey) brainApiKey.value = '';
      updateBrainActiveLabel(brainConfig);
      applyBrainModeToForm();
      const warning = payload.status?.warning;
      if (warning) setBrainFeedback(warning, 'success');
      else if (payload.synced) setBrainFeedback('Connected. The same Entity now uses this brain on every surface.', 'success');
      else setBrainFeedback('Saved on this device. It will activate when the Entity bridge connects.', 'success');
    } catch (error) {
      setBrainFeedback(error instanceof Error ? error.message : 'The brain could not be connected.', 'error');
    } finally {
      if (brainSave) brainSave.disabled = false;
      brainModeButtons.forEach((button) => { button.disabled = false; });
    }
  }

  brainModeButtons.forEach((button) => button.addEventListener('click', () => {
    void changeBrainMode(button.dataset.brainMode as BrainMode);
  }));
  brainModeButtons.forEach((button, index) => button.addEventListener('keydown', (event) => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? brainModeButtons.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + brainModeButtons.length) % brainModeButtons.length;
    brainModeButtons[next].focus();
    brainModeButtons[next].click();
  }));
  brainSave?.addEventListener('click', () => { void saveBrainSettings(); });
  brainEmbeddingMode?.addEventListener('change', () => {
    brainDrafts[brainMode].embeddingMode = (brainEmbeddingMode.value as BrainEmbeddingMode) || 'provider';
    applyBrainModeToForm();
  });
  brainKeyToggle?.addEventListener('click', () => {
    if (!brainApiKey) return;
    const reveal = brainApiKey.type === 'password';
    brainApiKey.type = reveal ? 'text' : 'password';
    brainKeyToggle.textContent = reveal ? 'Hide' : 'Show';
    brainKeyToggle.setAttribute('aria-label', reveal ? 'Hide API key' : 'Show API key');
  });

  const settingsEnvCallbacks: Array<(preset: string) => void> = [];
  const settingsClearObjCallbacks: Array<() => void> = [];
  const settingsClearDrawCallbacks: Array<() => void> = [];
  const settingsClearEnvCallbacks: Array<() => void> = [];
  const settingsClearAllCallbacks: Array<() => void> = [];

  const settingsTabs = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-settings-tab]'));
  const settingsThemeButtons = Array.from(document.querySelectorAll<HTMLButtonElement>('[data-settings-theme]'));
  let settingsReturnFocus: HTMLElement | null = null;
  let settingsInertSiblings: HTMLElement[] = [];

  function showSettingsTab(name: string): void {
    settingsTabs.forEach((tab) => {
      const active = tab.dataset.settingsTab === name;
      tab.setAttribute('aria-selected', String(active));
      tab.tabIndex = active ? 0 : -1;
      const panel = document.getElementById(tab.getAttribute('aria-controls') ?? '');
      if (panel) panel.hidden = !active;
    });
    const content = settingsModal.querySelector('.settings-content');
    if (content) content.scrollTop = 0;
  }

  settingsTabs.forEach((tab, index) => {
    tab.addEventListener('click', () => showSettingsTab(tab.dataset.settingsTab!));
    tab.addEventListener('keydown', (event) => {
      if (!['ArrowDown', 'ArrowUp', 'ArrowRight', 'ArrowLeft', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? settingsTabs.length - 1
        : (index + (['ArrowDown', 'ArrowRight'].includes(event.key) ? 1 : -1) + settingsTabs.length) % settingsTabs.length;
      settingsTabs[next].click();
      settingsTabs[next].focus();
    });
  });

  function syncSettingsTheme(): void {
    const theme = document.documentElement.getAttribute('data-theme') ?? 'light';
    if (settingsThemeSelect) settingsThemeSelect.value = theme;
    settingsThemeButtons.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.settingsTheme === theme)));
    const caption = document.getElementById('settings-theme-caption');
    if (caption) caption.textContent = theme === 'periwinkle' ? 'Periwinkle controls. A contrasting graphite scene.'
      : theme === 'dark' ? 'Muted surfaces for a darker workspace.' : 'A clean, light workspace.';
  }

  settingsThemeButtons.forEach(button => button.addEventListener('click', () => {
    const theme = button.dataset.settingsTheme;
    if (isThemeName(theme)) applyTheme(theme as UITheme);
    syncSettingsTheme();
  }));

  function openSettingsModal(): void {
    settingsReturnFocus = document.activeElement as HTMLElement;
    const currentTheme = document.documentElement.getAttribute('data-theme') ?? 'light';
    if (settingsThemeSelect) settingsThemeSelect.value = currentTheme;
    syncSettingsTheme();
    void loadBrainSettings(true);
    document.dispatchEvent(new Event('settings:opened'));
    settingsModal.hidden = false;
    settingsModal?.classList.add('open');
    settingsInertSiblings = Array.from(document.body.children).filter(
      (node): node is HTMLElement => node instanceof HTMLElement && node !== settingsModal && !node.inert,
    );
    settingsInertSiblings.forEach(node => { node.inert = true; });
    settingsTabs.find(tab => tab.getAttribute('aria-selected') === 'true')?.focus();
  }

  function closeSettingsModal(): void {
    document.dispatchEvent(new Event('settings:closed'));
    settingsModal?.classList.remove('open');
    settingsModal.hidden = true;
    settingsInertSiblings.forEach(node => { node.inert = false; });
    settingsInertSiblings = [];
    if (brainApiKey) { brainApiKey.value = ''; brainApiKey.type = 'password'; }
    if (brainKeyToggle) { brainKeyToggle.textContent = 'Show'; brainKeyToggle.setAttribute('aria-label', 'Show API key'); }
    settingsReturnFocus?.focus();
  }

  settingsBtn?.addEventListener('click', openSettingsModal);
  settingsClose?.addEventListener('click', closeSettingsModal);
  settingsModal?.addEventListener('click', (e) => {
    if (e.target === settingsModal) closeSettingsModal();
  });
  document.addEventListener('keydown', (e) => {
    if (!settingsModal?.classList.contains('open')) return;
    if (e.key === 'Escape') {
      e.preventDefault(); e.stopImmediatePropagation(); closeSettingsModal();
    } else if (e.key === 'Tab') {
      const focusable = Array.from(settingsModal.querySelectorAll<HTMLElement>('button, input, select, summary, [tabindex]'))
        .filter(node => node.tabIndex >= 0 && !node.hasAttribute('disabled') && node.getClientRects().length > 0);
      const target = e.shiftKey ? focusable.at(-1) : focusable[0];
      if (document.activeElement === (e.shiftKey ? focusable[0] : focusable.at(-1))) {
        e.preventDefault(); target?.focus();
      }
    }
  }, true);

  settingsThemeSelect?.addEventListener('change', () => {
    const val = settingsThemeSelect.value;
    if (isThemeName(val)) applyTheme(val as UITheme);
    syncSettingsTheme();
  });

  settingsEnvPreset?.addEventListener('change', () => {
    settingsEnvCallbacks.forEach((cb) => cb(settingsEnvPreset.value));
  });

  settingsClearObjects?.addEventListener('click', () => settingsClearObjCallbacks.forEach((cb) => cb()));
  settingsClearDrawings?.addEventListener('click', () => settingsClearDrawCallbacks.forEach((cb) => cb()));
  settingsClearEnv?.addEventListener('click', () => settingsClearEnvCallbacks.forEach((cb) => cb()));
  settingsClearAll?.addEventListener('click', () => {
    if (confirm('Clear all objects, drawings, and environment settings?')) {
      settingsClearAllCallbacks.forEach((cb) => cb());
    }
  });

  function updateSettingsObjectCount(n: number): void {
    if (settingsObjCount) settingsObjCount.textContent = String(n);
  }

  function updateSettingsEnvPreset(preset: string): void {
    if (settingsEnvPreset) settingsEnvPreset.value = preset;
  }

  // Wire up new-chat button
  const newChatBtn = document.getElementById('new-chat-btn');
  newChatBtn?.addEventListener('click', () => {
    newChatCallbacks.forEach((cb) => cb());
  });

  return {
    micBtn, sendBtn, textInput, arBtn, refocusBtn, transcriptEl, viewportContainer,
    setStatus, addTranscript, addToolTrace, showBusy, hideBusy,
    showARButton, setMicActive, setShellInfo,
    showSubtitle, hideSubtitle, toggleDrawer, isDrawerOpen, focusInput,
    getTheme, isDark, toggleTheme, onThemeChange, onCreateAgent, devtools, onTestAction,
    onShellSave, onAgentMenu, onCustomAction, setActiveShellSlug,
    addNotification, onTerminalToggle,
    clearTranscript, getMessages, loadMessages, onNewChat, onChatSelect,
    setChatList, setActiveChatId, setRecentChats,
    onSettingsEnvChange: (cb: (preset: string) => void) => { settingsEnvCallbacks.push(cb); },
    onSettingsClearObjects: (cb: () => void) => { settingsClearObjCallbacks.push(cb); },
    onSettingsClearDrawings: (cb: () => void) => { settingsClearDrawCallbacks.push(cb); },
    onSettingsClearEnv: (cb: () => void) => { settingsClearEnvCallbacks.push(cb); },
    onSettingsClearAll: (cb: () => void) => { settingsClearAllCallbacks.push(cb); },
    updateSettingsObjectCount,
    updateSettingsEnvPreset,
    onShellSelect: (cb: (slug: string) => void) => { shellSelectCallbacks.push(cb); },
  };
}
