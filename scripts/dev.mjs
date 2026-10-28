import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
const ui = JSON.parse(readFileSync(new URL('../config/ui.json', import.meta.url)));
const [python, npm, application = 'app.main:app', port = String(ui.backend_port)] = process.argv.slice(2);
const options = { stdio: 'inherit', windowsHide: true };
const backend = spawn(python, ['scripts/dev_backend.py', application, port], { ...options, stdio: ['pipe', 'inherit', 'inherit'] });
backend.stdin.on('error', () => {});
const frontend = npm.endsWith('.js') ? spawn(process.execPath, [npm, '--prefix', 'frontend', 'run', 'dev'], options) : spawn(npm, ['--prefix', 'frontend', 'run', 'dev'], { ...options, shell: true });
let closing = false;
function stop() {
  if (!closing) {
    closing = true;
    if (process.platform === 'win32') { if (backend.exitCode === null && !backend.stdin.destroyed) backend.stdin.end('SIGINT\n'); }
    else backend.kill('SIGINT');
    frontend.kill('SIGTERM');
  }
}
process.on('SIGINT', stop); process.on('SIGTERM', stop);
for (const child of [backend, frontend]) { child.on('exit', stop); child.on('error', error => { console.error(error.message); stop(); process.exitCode = 1; }); }
