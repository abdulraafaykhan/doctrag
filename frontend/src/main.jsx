import { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { api, post, clientId, fileURL, safeText } from './api.js';
import { Cards, Reply } from './components.jsx';
import ui from '../../config/ui.json';
import './style.css';


function App() {
  const [session, setSession] = useState(null), [turns, setTurns] = useState([]);
  const [text, setText] = useState(''), [pending, setPending] = useState([]);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [preview, setPreview] = useState(null);
  const input = useRef(), bottom = useRef(), requests = useRef(new Map());
  const limits = session?.upload_limits;
  const event = (name, detail = {}) => { if (session) post('/ui-event', { session_id: session.session_id, event: name, detail }).catch(() => {}); };

  async function start(newChat = false) {
    setError('');
    try {
      const state = await post('/session', { client_id: clientId, new_chat: newChat });
      const restored = await api('/session');
      setSession({ ...state, ...restored }); setTurns(restored.history);
      if (newChat) requests.current.clear();
      setPending(restored.attachments.filter(card => !restored.history.some(turn => turn.attachments.some(sent => sent.id === card.id))));
    } catch (err) { setError(err.message); }
  }
  useEffect(() => { start(); }, []);
  useEffect(() => { bottom.current?.scrollIntoView({ behavior: 'smooth' }); }, [turns, busy, pending]);
  useEffect(() => {
    if (!session) return;
    const timer = setInterval(() => api('/session').catch(() => {}), ui.heartbeat_ms);
    return () => clearInterval(timer);
  }, [session?.session_id]);

  useEffect(() => {
    const cards = [...pending, ...turns.flatMap(turn => turn.attachments || [])];
    const ids = [...new Set(cards.filter(card => ['Uploading', 'Processing'].includes(card.status)).map(card => card.id))];
    if (!ids.length || !session) return;
    const timer = setInterval(async () => {
      for (const id of ids) {
        let card;
        try { card = await api(`/upload/${id}`); }
        catch (err) { if (err.status !== 404) { setError(err.message); continue; } card = { id, status: ui.expired }; }
        const update = old => old.map(item => item.id === id ? { ...item, ...card } : item);
        setPending(update); setTurns(old => old.map(turn => ({ ...turn, attachments: update(turn.attachments || []) })));
        setSession(old => ({ ...old, attachments: update(old.attachments || []) }));
      }
    }, ui.poll_ms);
    return () => clearInterval(timer);
  }, [pending, turns, session?.session_id]);

  async function attach(files) {
    event('attach', { count: files.length }); setError('');
    if (files.length + pending.length > limits.max_files) { setError(ui.too_many.replace('{count}', limits.max_files)); return; }
    for (const file of files) {
      if (!limits.allowed_extensions.includes(file.name.split('.').pop().toLowerCase())) { setError(ui.bad_type); continue; }
      if (file.size > limits.max_mb * 1024 * 1024) { setError(ui.too_large.replace('{size}', limits.max_mb)); continue; }
      const localId = crypto.randomUUID(), local = { id: localId, name: file.name, size: file.size, kind: file.type.startsWith('image/') ? 'image' : 'document', status: ui.uploading, local: true };
      setPending(old => [...old, local]);
      const data = new FormData(); data.append('file', file); data.append('session_id', session.session_id); data.append('client_id', clientId);
      try { const card = await api('/upload', { method: 'POST', body: data }); setPending(old => old.map(item => item.id === localId ? card : item)); setSession(old => ({ ...old, attachments: [...old.attachments.filter(item => item.id !== card.id), card] })); }
      catch (err) { setPending(old => old.filter(item => item.id !== localId)); setError(err.message); }
    }
  }
  async function remove(card) {
    event('remove', { attachment_id: card.id });
    try {
      await api(`/upload/${card.id}`, { method: 'DELETE' }); setPending(old => old.filter(item => item.id !== card.id));
      setSession(old => ({ ...old, attachments: old.attachments.filter(item => item.id !== card.id) }));
      setTurns(old => old.map(turn => ({ ...turn, attachments: turn.attachments.map(item => item.id === card.id ? { ...item, status: ui.deleted } : item) })));
    }
    catch (err) { setError(err.message); }
  }
  function open(card) {
    event('attachment_open', { attachment_id: card.id });
    if ([ui.expired, ui.failed, ui.deleted].includes(card.status) || card.local) return;
    if (card.kind === 'image') setPreview(card); else window.open(fileURL(card), '_blank', 'noopener,noreferrer');
  }
  async function send(message = text, cards = pending, retryTurn = null) {
    if (busy || !session || (!message.trim() && !cards.length)) return;
    const id = retryTurn?.id || crypto.randomUUID();
    message = requests.current.get(id) ?? message;
    requests.current.set(id, message);
    const turn = { id, request_id: id, message: safeText(message), attachments: cards, sending: true };
    // Insert first, before fetch, so slow providers cannot hide a sent message.
    setTurns(old => retryTurn ? old.map(item => item.id === id ? turn : item) : [...old, turn]);
    setText(''); setPending([]); setBusy(true); setError(''); event('send', { attachments: cards.length });
    try {
      const response = await post('/chat', { session_id: session.session_id, client_id: clientId, message, attachment_ids: cards.map(card => card.id), request_id: id });
      setTurns(old => old.map(item => item.id === id ? { ...turn, response, sending: false } : item));
      setSession(old => ({ ...old, ...response }));
      requests.current.delete(id);
    } catch (err) { setTurns(old => old.map(item => item.id === id ? { ...turn, failed: err.message, sending: false } : item)); }
    finally { setBusy(false); }
  }

  const waiting = pending.some(card => !['Ready', 'Partial'].includes(card.status));
  return <main style={{ '--column-width': `${ui.column_width}px` }}>
    <header><h1>{ui.title}</h1><span className="login">{session?.auth_state === 'VERIFIED' ? ui.verified.replace('{name}', session.auth_name) : ui.not_verified}</span><button disabled={busy || !session} onClick={() => { event('new_chat'); start(true); }}>{ui.new_chat}</button></header>
    <section className="messages" aria-live="polite">
      {!turns.length && <p className="welcome">{ui.welcome}</p>}
      {turns.map((turn, index) => <article key={turn.id}>
        <div className="sent">{turn.message && <p>{turn.message}</p>}<Cards cards={turn.attachments || []} onOpen={open} onRemove={remove} /></div>
        {turn.failed && <div role="alert">{turn.failed} <button disabled={busy} onClick={() => { event('retry'); send(turn.message, turn.attachments, turn); }}>{ui.retry}</button></div>}
        <Reply turn={turn} latest={index === turns.length - 1} busy={busy} onEvent={event} onSend={send} />
      </article>)}
      {busy && <p className="status" role="status">{ui.thinking}</p>}<div ref={bottom} />
    </section>
    <footer onDragOver={e => e.preventDefault()} onDrop={e => { e.preventDefault(); event('drop'); if (session && !busy) attach(Array.from(e.dataTransfer.files)); }}>
      {error && <p role="alert">{error}</p>}
      {!!session?.attachments?.length && <details onToggle={e => event('files_toggle', { open: e.currentTarget.open })}><summary>{ui.files}</summary>{session.attachments.map(card => <div className="document" key={card.id}><button onClick={() => open(card)}>{card.name} · {card.status}</button><button disabled={busy} onClick={() => remove(card)} aria-label={`${ui.delete} ${card.name}`}>{ui.delete}</button></div>)}</details>}
      {session?.auth_state === 'AWAITING_OTP' && <p className="hint">{ui.otp_note}</p>}
      <Cards cards={pending} removable onOpen={open} onRemove={remove} />
      {waiting && <p role="status" className="hint">{pending.some(card => card.kind === 'image') ? ui.reading_image : ui.processing}</p>}
      <form onSubmit={e => { e.preventDefault(); send(); }}>
        <input ref={input} type="file" multiple accept={limits?.allowed_extensions.map(ext => `.${ext}`).join(',')} hidden onChange={e => { attach(Array.from(e.target.files)); e.target.value = ''; }} />
        <button type="button" disabled={busy || !session} onClick={() => { event('attach_click'); input.current.click(); }}>{ui.attach}</button>
        <textarea aria-label={ui.placeholder} placeholder={ui.placeholder} value={text} rows={2} disabled={busy || !session} onFocus={() => event('input_focus')} onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }} />
        <button className="primary" disabled={busy || waiting || !session || (!text.trim() && !pending.length)}>{ui.send}</button>
      </form>
    </footer>
    {preview && <dialog open aria-label={ui.preview}><button onClick={() => { event('preview_close'); setPreview(null); }}>{ui.close}</button><img src={fileURL(preview)} alt={preview.name} /></dialog>}
  </main>;
}
createRoot(document.getElementById('root')).render(<App />);
