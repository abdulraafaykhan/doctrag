"""Forward the launcher's Ctrl+C command to uvicorn's graceful signal handler."""
import signal
import sys
from pathlib import Path
from threading import Thread
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from app.config import get_settings

cfg = get_settings().ui
server = uvicorn.Server(uvicorn.Config(sys.argv[1] if len(sys.argv) > 1 else 'app.main:app', host=cfg['backend_host'], port=int(sys.argv[2]) if len(sys.argv) > 2 else cfg['backend_port']))

def signals():
    for line in sys.stdin:
        if line.strip() == 'SIGINT':
            if not server.should_exit:
                server.handle_exit(signal.SIGINT, None)
            return

# Windows stdin inspection during imports must finish before a pipe reader owns it.
server.config.load()
Thread(target=signals, daemon=True).start()
try:
    server.run()
except KeyboardInterrupt:
    # Uvicorn re-raises SIGINT after its lifespan has completed.
    pass
