import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
const ui = JSON.parse(readFileSync(new URL('../config/ui.json', import.meta.url)));
const [python, npm] = process.argv.slice(2);
const options = { stdio: 'inherit', windowsHide: true };
const backend = spawn(python, ['-m', 'uvicorn', 'app.main:app', '--host', ui.backend_host, '--port', String(ui.backend_port)], options);
const frontend = npm.endsWith('.js') ? spawn(process.execPath, [npm, '--prefix', 'frontend', 'run', 'dev'], options) : spawn(npm, ['--prefix', 'frontend', 'run', 'dev'], { ...options, shell: true });
let closing = false;
function stop() { if (!closing) { closing = true; backend.kill(); frontend.kill(); } }
process.on('SIGINT', stop); process.on('SIGTERM', stop);
for (const child of [backend, frontend]) { child.on('exit', stop); child.on('error', error => { console.error(error.message); stop(); process.exitCode = 1; }); }
