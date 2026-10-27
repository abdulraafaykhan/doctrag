import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import ui from '../config/ui.json' with { type: 'json' };
export default defineConfig({ plugins: [react()], server: { port: ui.frontend_port, strictPort: true, proxy: { '/api': `http://${ui.backend_host}:${ui.backend_port}` } } });
