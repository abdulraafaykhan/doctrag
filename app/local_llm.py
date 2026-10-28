"""Strict text-server adapter; the existing runner remains the tool executor."""
import json
import math
import httpx
from app.config import get_settings
from app.log import log, mask_payload, mask_text
from app.prepass import MOBILE, ACCOUNT
from app.usage import meter, session_context


class LocalLLMError(Exception):
    pass


class LocalClient:
    def __init__(self, registry, *, model=None, transport=None, usage=None):
        self.registry, self.transport = registry, transport
        self.settings = get_settings()
        self.model = model or self.settings.models.agent
        self.usage = usage if usage is not None else []

    def prompt(self, messages, plain=False):
        tools = [{'name': tool.name, 'description': tool.description, 'arguments': tool.args_schema.model_json_schema()} for tool in self.registry.list()]
        transcript = '\n\n'.join(f"{item['role'].title()}: {item['content']}" for item in messages)
        prompt = transcript if plain else self.settings.prompts['local_tool_protocol'].replace('{{tools}}', json.dumps(tools, separators=(',', ':'))).replace('{{conversation}}', transcript)
        prompt = mask_text(prompt) + '\n\nAssistant:'
        if MOBILE.search(prompt) or ACCOUNT.search(prompt):
            raise LocalLLMError('Unmasked identity in provider prompt')
        return prompt

    def request(self, prompt):
        cfg, url = self.settings.local_llm, self.settings.secrets.local_llm_url
        if not url:
            raise LocalLLMError('LOCAL_LLM_URL is not configured')
        try:
            with log.span('LLM', 'local text request', model=self.model):
                with httpx.Client(transport=self.transport, timeout=cfg['timeout_seconds']) as client:
                    response = client.post(url, json={'server': cfg['server_id'], 'prompt': prompt, 'max_tokens': cfg['max_tokens']})
                response.raise_for_status()
                body = response.json()
                text = body[cfg['text_field']]
                if not isinstance(text, str):
                    raise ValueError('Configured text field is not a string')
                estimated = body.get('prompt_tokens') is None or body.get('completion_tokens') is None
                incoming = body.get('prompt_tokens') if body.get('prompt_tokens') is not None else math.ceil(len(prompt) / self.settings.chunking['chars_per_token'])
                outgoing = body.get('completion_tokens') if body.get('completion_tokens') is not None else math.ceil(len(text) / self.settings.chunking['chars_per_token'])
                counted = meter.add(session_context.get(), self.model, 'llm', incoming, outgoing, estimated=estimated)
                self.usage.append({'model': self.model, 'prompt_tokens': incoming, 'completion_tokens': outgoing, 'estimated': estimated, 'cached': False})
                log.step('LLM', 'local token usage', model=self.model, **counted)
                return text
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise LocalLLMError(f'Local request failed: {type(exc).__name__}') from exc

    def parse(self, text):
        value = json.loads(text)
        if isinstance(value, dict) and set(value) == {'reply'} and isinstance(value['reply'], str):
            return value['reply']
        if not isinstance(value, dict) or set(value) != {'tool_calls'} or not isinstance(value['tool_calls'], list) or not value['tool_calls']:
            raise ValueError('Expected a reply or nonempty tool_calls object')
        def strip_identity(item):
            if isinstance(item, dict):
                return {key: strip_identity(child) for key, child in item.items() if key not in {'user_id', 'account_id', 'account_number'}}
            return [strip_identity(child) for child in item] if isinstance(item, list) else item
        for call in value['tool_calls']:
            if not isinstance(call, dict) or set(call) != {'name', 'arguments'} or not isinstance(call['name'], str) or call['name'] not in self.registry.visible() or not isinstance(call['arguments'], dict):
                raise ValueError('Unknown, hidden or malformed tool')
            arguments = strip_identity(call['arguments'])
            call['arguments'] = self.registry.tools[call['name']].args_schema.model_validate(arguments).model_dump(mode='json', exclude_none=True)
        return value

    def chat(self, messages, *, plain=False):
        if plain:
            return self.request(self.prompt(messages, plain=True))
        history = list(messages)
        for attempt in range(self.settings.local_llm['protocol_retries'] + 1):
            text = self.request(self.prompt(history))
            try:
                return self.parse(text)
            except (ValueError, TypeError) as exc:
                log.step('AGENT', 'local protocol rejected', error=type(exc).__name__, reason='JSON or visible tool arguments failed validation', why='retry once before moving to fallback')
                if attempt == self.settings.local_llm['protocol_retries']:
                    raise LocalLLMError('Local JSON/tool protocol failed') from exc
                history.append({'role': 'assistant', 'content': text})
