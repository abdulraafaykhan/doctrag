import ui from '../../config/ui.json';

export const clientId = sessionStorage.getItem('client_id') || crypto.randomUUID();
sessionStorage.setItem('client_id', clientId);
// A new tab gets its own ID; the cookie identifies the shared browser session.
export async function api(path, options = {}) {
  const { headers, ...rest } = options;
  const response = await fetch(`/api${path}`, { ...rest, headers: { [ui.client_header]: clientId, ...headers } });
  const body = await response.json();
  if (!response.ok) { const error = new Error(body.message || ui.unavailable); error.status = response.status; error.code = body.code; throw error; }
  return body;
}
export const post = (path, body, options = {}) => api(path, { ...options, method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
export const fileURL = card => `/api/files/${card.id}`;
export const safeText = text => text.replace(/\b(?:pin|cvv|password)(?:\s+(?:is\s+)?|\s*[:=]\s*)\S+/gi, ui.secret);
document.title = ui.title;

