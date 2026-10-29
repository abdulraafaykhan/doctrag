"""Live HTTP smoke: health and one tiny upload, with no chat generation."""
import json
import sys
from pathlib import Path
from time import monotonic, sleep
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx
from app.config import ROOT, get_settings


def main():
    cfg = get_settings().ui
    base = f"http://{cfg['backend_host']}:{cfg['backend_port']}"
    results = []
    with httpx.Client(base_url=base, timeout=get_settings().llm.timeout_seconds, headers={cfg['client_header']: 'http-smoke'}) as client:
        health = client.get('/api/health')
        health.raise_for_status()
        results.append({'health': health.json()})
        state = client.post('/api/session', json={'client_id': 'http-smoke'}).json()
        data = {'session_id': state['session_id'], 'client_id': 'http-smoke'}
        content = b'Fictional Harbourline browser smoke note. Test reference phrase: silver lighthouse.'
        uploaded = client.post('/api/upload', data=data, files={'file': ('smoke-note.txt', content, 'text/plain')})
        uploaded.raise_for_status()
        card = uploaded.json()
        duplicate = client.post('/api/upload', data=data, files={'file': ('smoke-note.txt', content, 'text/plain')})
        assert duplicate.json()['id'] == card['id']
        deadline = monotonic() + get_settings().database.index_wait_seconds
        while monotonic() < deadline:
            current = client.get('/api/upload/' + card['id'])
            current.raise_for_status()
            card = current.json()
            if card['status'] not in {'Uploading', 'Processing'}:
                break
            sleep(cfg['poll_ms'] / 1000)
        assert card['status'] in {'Ready', 'Partial'}, card
        assert client.get('/api/files/' + card['id']).content == content
        client.post('/api/ui-event', json={'session_id': state['session_id'], 'event': 'smoke file open', 'detail': {'attachment_id': card['id']}}).raise_for_status()
        client.delete('/api/upload/' + card['id']).raise_for_status()
        assert client.get('/api/files/' + card['id']).status_code == 404
        results.append({'upload_status': card['status'], 'same_hash_reused': True, 'original_served': True, 'deleted_file_404': True})
    (ROOT / 'eval/http_checks.json').write_text(json.dumps({'passed': True, 'results': results}, indent=2), encoding='utf-8')
    print(json.dumps(results, default=str), flush=True)


if __name__ == '__main__':
    main()
