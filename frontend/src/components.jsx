import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import ui from '../../config/ui.json';
import { fileURL } from './api.js';

export function Cards({ cards, removable = false, onOpen, onRemove }) {
  return <div className="cards">{cards.map(card => <div className="card" key={card.id}>
    <button className="file" onClick={() => onOpen(card)} disabled={[ui.expired, ui.deleted].includes(card.status)}>
      {card.kind === 'image' && !card.local && ![ui.expired, ui.deleted].includes(card.status) ? <img src={fileURL(card)} alt="" /> : <span aria-hidden="true">▧</span>}
      <span><strong>{card.name}</strong><small>{Math.ceil(card.size / 1024)} {ui.bytes_unit} · {card.status}</small>{card.warnings?.map((warning, n) => <small key={n}>{warning}</small>)}</span>
    </button>
    {removable && <button className="remove" aria-label={`${ui.remove} ${card.name}`} onClick={() => onRemove(card)}>×</button>}
  </div>)}</div>;
}

export function Reply({ turn, latest, busy, onEvent, onSend }) {
  const response = turn.response;
  if (!response) return null;
  const citations = response.citations || [];
  const markdown = response.reply_markdown.replace(/\[(\d+)\]/g, (_, n) => `[${n}](#source-${turn.id}-${n})`);
  return <div className="reply">
    <Markdown remarkPlugins={[remarkGfm]} components={{ table: props => <div className="table-scroll"><table {...props} /></div>, a: ({ href, children }) => <a href={href} className={href?.startsWith('#source-') ? 'citation' : ''} onClick={click => { onEvent('citation_open', { href }); if (href?.startsWith('#source-')) { click.preventDefault(); const source = document.getElementById(href.slice(1)); if (source) { source.closest('details').open = true; source.scrollIntoView({ block: 'nearest' }); } } }} target={href?.startsWith('#') ? undefined : '_blank'} rel="noopener noreferrer">{children}</a> }}>{markdown}</Markdown>
    {!!citations.length && <details onToggle={e => onEvent('sources_toggle', { open: e.currentTarget.open })}><summary>{ui.sources}</summary><ol className="sources">{citations.map((source, index) => <li id={`source-${turn.id}-${source.n || index + 1}`} key={`${source.chunk_id}-${index}`}>{source.doc_title || source.title} · {ui.page} {source.page} · {ui.version} {source.version} · {source.effective_date}</li>)}</ol></details>}
    {latest && <div className="options">{response.options?.map(option => <button key={option} onClick={() => { onEvent('option_click', { option }); onSend(option); }}>{option}</button>)}</div>}
    {response.notices?.map((notice, i) => <p role="status" key={i}>{notice}</p>)}
  </div>;
}

