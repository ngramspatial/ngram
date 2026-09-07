export const SLASH_COMMANDS = [
  { name: 'compact', description: 'Summarize older context; keep your history' },
  { name: 'context', description: 'Show context usage and compaction status' },
  { name: 'stop', description: 'Stop the response and its audio' },
  { name: 'pause', description: 'Pause chat and background model use' },
  { name: 'resume', description: 'Resume model use' },
  { name: 'voice', description: 'Open voice settings' },
  { name: 'help', description: 'Show available commands' },
] as const;

export function parseSlashCommand(text: string) {
  const value = text.trim();
  if (!value.startsWith('/')) return null;
  const name = value.slice(1).toLowerCase();
  return { name, valid: SLASH_COMMANDS.some(command => command.name === name) };
}

export function matchSlashCommands(text: string) {
  const command = parseSlashCommand(text);
  return command ? SLASH_COMMANDS.filter(item => item.name.startsWith(command.name)) : [];
}

/** Suggestions keep focus in the composer; commands never need a model turn. */
export function attachSlashCommands(input: HTMLTextAreaElement, submit: () => void) {
  const menu = document.createElement('div');
  menu.id = 'slash-commands';
  menu.className = 'slash-commands';
  menu.setAttribute('role', 'listbox');
  menu.setAttribute('aria-label', 'Slash commands');
  menu.hidden = true;
  // The input wrapper clips its animated placeholder; anchor outside that clip.
  input.parentElement!.parentElement!.appendChild(menu);
  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-label', 'Message or slash command');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-controls', menu.id);
  input.setAttribute('aria-expanded', 'false');
  let selected = 0;
  let dismissed = false;
  const close = () => {
    menu.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  };
  const render = () => {
    const matches = matchSlashCommands(input.value);
    input.classList.toggle('slash-active', !!parseSlashCommand(input.value)?.valid);
    if (dismissed || !matches.length || document.activeElement !== input) { close(); return; }
    selected = Math.min(selected, matches.length - 1);
    menu.replaceChildren();
    matches.forEach((command, index) => {
      const option = document.createElement('div');
      option.id = `slash-${command.name}`;
      option.className = 'slash-option';
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', String(selected === index));
      const name = document.createElement('strong');
      name.textContent = `/${command.name}`;
      const description = document.createElement('span');
      description.textContent = command.description;
      option.append(name, description);
      option.addEventListener('pointerdown', event => event.preventDefault());
      option.addEventListener('click', () => { input.value = `/${command.name}`; close(); submit(); });
      menu.appendChild(option);
    });
    menu.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-activedescendant', `slash-${matches[selected].name}`);
    menu.children[selected]?.scrollIntoView({ block: 'nearest' });
  };
  input.addEventListener('input', () => { selected = 0; dismissed = false; render(); });
  input.addEventListener('focus', render);
  input.addEventListener('blur', close);
  input.addEventListener('keydown', event => {
    if (event.isComposing || menu.hidden) return;
    const matches = matchSlashCommands(input.value);
    if (event.key === 'Escape') {
      event.preventDefault(); event.stopPropagation(); dismissed = true; close();
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      selected = (selected + (event.key === 'ArrowDown' ? 1 : -1) + matches.length) % matches.length;
      render();
    } else if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
      event.preventDefault();
      input.value = `/${matches[selected].name}`;
      dismissed = true;
      render();
      if (event.key === 'Enter') submit();
    }
  });
  return { close };
}

let noticeTimer: ReturnType<typeof setTimeout> | undefined;
export function showCommandNotice(text: string): void {
  let notice = document.getElementById('command-notice');
  if (!notice) {
    notice = document.createElement('div'); notice.id = 'command-notice'; notice.className = 'command-notice';
    notice.setAttribute('role', 'status');
    const content = document.createElement('span');
    const close = document.createElement('button'); close.textContent = '×';
    close.setAttribute('aria-label', 'Dismiss command notice');
    close.addEventListener('click', () => { notice!.hidden = true; });
    notice.append(content, close); document.body.appendChild(notice);
  }
  notice.querySelector('span')!.textContent = text;
  notice.hidden = false;
  clearTimeout(noticeTimer);
  noticeTimer = setTimeout(() => { notice!.hidden = true; }, 12000);
}
