/* Installed Edge + Playwright only; all API responses are offline fixtures. */
const { chromium } = require(process.argv[2] || 'playwright');
const http = require('node:http'), fs = require('node:fs'), path = require('node:path');
const assert = require('node:assert/strict'), ui = require('../config/ui.json');
const root = path.resolve(__dirname, '../frontend/dist');
const server = http.createServer((request, response) => {
  const name = path.resolve(root, '.' + new URL(request.url, 'http://localhost').pathname);
  if (!name.startsWith(root + path.sep) && name !== root) { response.writeHead(403).end(); return; }
  const file = fs.existsSync(name) && fs.statSync(name).isFile() ? name : path.join(root, 'index.html');
  response.setHeader('Content-Type', ({ '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html' })[path.extname(file)] || 'application/octet-stream');
  fs.createReadStream(file).pipe(response);
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const browser = await chromium.launch({ channel: 'msedge', headless: true });
  const results = [];
  try {
    const page = await browser.newPage(), errors = [], received = [], payloads = [];
    await page.addInitScript(({ heartbeat }) => { const original = window.setInterval; window.setInterval = (callback, milliseconds, ...args) => original(callback, milliseconds === heartbeat ? 100 : milliseconds, ...args); }, { heartbeat: ui.heartbeat_ms });
    page.on('pageerror', error => errors.push(error.message));
    let sessionId = 'queue-fixture', release, delay = new Promise(resolve => { release = resolve; }), uploadCount = 0, expire = false, heartbeatTokens = null;
    const response = message => ({ reply_markdown: 'Answer: ' + message, options: ['40 pages'], citations: [], attachments: [], auth_state: 'ANONYMOUS', tokens: { request: { in: 0, out: 0, total: 0 }, session: { in: 0, out: 0, total: 0, calls: 0 } } });
    await page.route('**/api/**', async route => {
      const request = route.request(), pathname = new URL(request.url()).pathname;
      let body = { ok: true };
      if (pathname === '/api/session') {
        if (request.method() === 'GET' && expire) { expire = false; await route.fulfill({ status: 410, contentType: 'application/json', body: JSON.stringify({ code: 'SESSION_EXPIRED', message: ui.session_expired.replace('{minutes}', '15') }) }); return; }
        if (request.method() === 'POST' && request.postDataJSON().new_chat) sessionId += '-new';
        body = { ...response(''), session_id: sessionId, history: sessionId.endsWith('-new') ? [] : [{ id: 'initial', message: 'initial', attachments: [], response: response('initial') }], attachments: [], upload_limits: { max_files: 5, max_mb: 10, allowed_extensions: ['txt'] } };
        if (heartbeatTokens) body.tokens.session = heartbeatTokens;
      }
      if (pathname === '/api/upload') { uploadCount++; body = { id: 'fixture-upload', name: 'during-thinking.txt', kind: 'document', size: 10, status: 'Ready' }; }
      if (pathname === '/api/chat') {
        const payload = request.postDataJSON(); received.push(payload.message); payloads.push(payload);
        if (received.length === 1 || payload.message === 'old slow') await delay;
        body = response(payload.message);
      }
      try { await route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) }); } catch (error) { if (!/closed|canceled|aborted/i.test(error.message)) throw error; }
    });
    await page.goto(`http://127.0.0.1:${server.address().port}`);
    await page.locator('.reply').waitFor();
    const send = async text => { await page.locator('textarea').fill(text); await page.getByRole('button', { name: ui.send, exact: true }).click(); };
    await send('slow first'); await page.getByText(ui.thinking, { exact: true }).waitFor();
    assert.equal(await page.locator('textarea').isEnabled(), true);
    assert.equal(await page.getByRole('button', { name: ui.attach, exact: true }).isEnabled(), true);
    await page.locator('input[type=file]').setInputFiles({ name: 'during-thinking.txt', mimeType: 'text/plain', buffer: Buffer.from('Offline fixture') });
    await page.waitForFunction(() => document.querySelector('footer .card small')?.textContent.includes('Ready'));
    assert.equal(uploadCount, 1);
    await send('typed second'); await page.getByRole('button', { name: '40 pages', exact: true }).click();
    assert.equal(await page.getByText(ui.queued, { exact: true }).count(), 2);
    assert.deepEqual(received, ['slow first']);
    release(); await page.waitForFunction(() => document.querySelectorAll('.reply').length === 4);
    assert.deepEqual(received, ['slow first', 'typed second', '40 pages']);
    assert.deepEqual(payloads[1].attachment_ids, ['fixture-upload']);
    results.push('typing and option during slow reply queued immediately; three replies arrived FIFO');
    delay = new Promise(resolve => { release = resolve; });
    await send('old slow'); await page.getByText(ui.thinking, { exact: true }).waitFor();
    await send('discard queued'); await page.getByRole('button', { name: ui.new_chat, exact: true }).click();
    await page.locator('.welcome').waitFor(); release();
    await send('new message'); await page.locator('.reply').waitFor();
    assert.equal(await page.locator('.sent').count(), 1);
    assert.equal(await page.locator('.reply > p').textContent(), 'Answer: new message');
    assert.ok(!received.includes('discard queued'));
    results.push('New chat aborts old response and discards queue');
    for (const width of [360, 1280]) {
      await page.setViewportSize({ width, height: 900 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      const boxes = await page.locator('form button, form textarea').evaluateAll(nodes => nodes.map(node => { const box = node.getBoundingClientRect(); return { left: box.left, right: box.right, top: box.top, bottom: box.bottom }; }));
      assert.ok(boxes.every(box => box.left >= 0 && box.right <= width));
      assert.ok(boxes[0].right <= boxes[1].left && boxes[1].right <= boxes[2].left);
      results.push(`${width}px: composer in viewport, no overlap or horizontal overflow`);
    }
    heartbeatTokens = { in: 11, out: 3, total: 14, calls: 2 };
    await page.getByText('Tokens: in 11 · out 3 · total 14', { exact: true }).waitFor();
    results.push('heartbeat refreshes session token counter');
    heartbeatTokens = null; expire = true;
    await page.getByText(ui.session_expired.replace('{minutes}', '15'), { exact: true }).waitFor();
    assert.equal(await page.locator('.sent').count(), 0);
    await page.getByText('Tokens: in 0 · out 0 · total 0', { exact: true }).waitFor();
    results.push('SESSION_EXPIRED starts a clean chat and clears token counter');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(root, '../../data/queue_check.json'), JSON.stringify({ passed: results, live_calls: 0 }, null, 2));
    console.log(results.join('\n'));
  } finally { await browser.close(); server.closeAllConnections(); server.close(); }
})().catch(error => { console.error(error); server.close(); process.exitCode = 1; });
