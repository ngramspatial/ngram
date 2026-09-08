// @ts-nocheck
// Isolated browser fixture using the production composer, theme, and chat renderer.
import { setupUI } from '../packages/surface-webxr/src/ui.js';
import { setupAttachments } from '../packages/surface-webxr/src/attachment-composer.js';
const ui = setupUI();
ui.textInput.setAttribute('aria-label', 'Message');
ui.setStatus('Attachment review · no agent connected', true);
const result = document.createElement('pre'); result.id = 'attachment-results';
Object.assign(result.style, {position:'absolute',left:'300px',top:'95px',maxWidth:'460px',whiteSpace:'pre-wrap',color:'var(--text)',font:'12px var(--font-body)',zIndex:5});
document.body.append(result);
const composer = setupAttachments(ui.textInput, () => 'fixture', text => { result.textContent = text; });
const png = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aVe8AAAAASUVORK5CYII='), c => c.charCodeAt(0));
let sent = 0, busy = false;
function button(label, action) {
  const b = document.createElement('button'); b.className='topbar-btn'; b.textContent=label;
  Object.assign(b.style,{width:'auto',padding:'8px'}); b.onclick=action;
  document.querySelector('.topbar-actions').append(b);
}
button('Paste image', () => {
  const data = new DataTransfer(); data.items.add(new File([png],'pasted.png',{type:'image/png'}));
  ui.textInput.dispatchEvent(new ClipboardEvent('paste',{clipboardData:data,bubbles:true,cancelable:true}));
});
button('Drop files', () => {
  const data = new DataTransfer();
  data.items.add(new File(['Fixture notes: blue triangle'],'notes.txt',{type:'text/plain'}));
  data.items.add(new File(['PK\x03\x04fixture'],'project.zip',{type:'application/zip'}));
  document.body.dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true}));
});
button('Oversize file', () => {
  const data = new DataTransfer(); data.items.add(new File([new Uint8Array(101 * 1024 * 1024)],'oversize.bin'));
  document.body.dispatchEvent(new DragEvent('drop',{dataTransfer:data,bubbles:true,cancelable:true}));
});
button('Fail next upload', async () => { await fetch('/fixture/fail',{method:'POST'}); result.textContent='Next upload will fail'; });
button('Slow next upload', async () => { await fetch('/fixture/slow',{method:'POST'}); result.textContent='Next upload will wait 8 seconds'; });
button('Restore chat', () => { const messages=ui.getMessages(); ui.loadMessages(messages); result.textContent=`Restored ${messages.length} messages, no binary data: ${!JSON.stringify(messages).includes('base64')}`; });
button('Clear draft', () => { composer.clear(); });
button('Switch theme', () => { const current=document.documentElement.dataset.theme; document.documentElement.dataset.theme=current==='light'?'dark':current==='dark'?'periwinkle':'light'; });
ui.sendBtn.onclick = async () => {
  if (busy || (!ui.textInput.value && !composer.hasFiles())) return;
  busy=true;
  try {
    const attachments=await composer.prepare();
    ui.addTranscript('user',ui.textInput.value,attachments); ui.textInput.value=''; composer.clear();
    result.textContent=`Sent ${++sent} message(s) with ${attachments.length} attachments. IDs only. No model called.`;
  } catch(error) { result.textContent=error.name==='AbortError'?'Upload cancelled; message not sent.':error.message; }
  finally { busy=false; }
};
ui.toggleDrawer();
