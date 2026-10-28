"""Strict text-server adapter; the existing runner remains the tool executor."""
import json
import math
import re
from copy import deepcopy
from time import perf_counter
import httpx
from pydantic import ValidationError
from app.config import get_settings
from app.log import log, mask_payload, mask_text
from app.prepass import MOBILE, ACCOUNT
from app.usage import meter, session_context


class LocalLLMError(Exception):
    def __init__(self, message, *, http_status=None, server_text=None):
        super().__init__(message)
        self.http_status = http_status
        self.server_text = message if server_text is None else server_text


class LocalClient:
    def __init__(self, registry, *, model=None, transport=None, usage=None):
        self.registry, self.transport = registry, transport
        self.settings = get_settings()
        self.model = model or self.settings.models.agent
        self.usage = usage if usage is not None else []
        self.last_response = {}

    def tokens(self, text):
        return math.ceil(len(text) / self.settings.chunking['chars_per_token'])

    def fits(self, prompt, image_tokens=0):
        cfg = self.settings.local_llm
        return self.tokens(prompt) + image_tokens <= cfg['context_limit_tokens'] and len(prompt) <= cfg['max_prompt_chars']

    def prompt(self, messages, plain=False, no_tools=False, writer=False):
        cfg = self.settings.local_llm
        history = deepcopy(messages)
        tools = [] if writer or plain or no_tools or self.registry is None else [{'name': tool.name, 'description': tool.description, 'arguments': tool.args_schema.model_json_schema()} for tool in self.registry.list()]
        envelopes, sections = [], []
        for item in history:
            if item['role'] != 'tool':
                continue
            try:
                value = json.loads(item['content'])
            except ValueError:
                continue
            if isinstance(value, dict) and isinstance(value.get('data'), dict) and isinstance(value['data'].get('items'), list):
                envelopes.append((item, value))
                for row in value['data']['items']:
                    score = row.get('score', 0)
                    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score):
                        log.step('AGENT', 'app.local_llm.LocalClient.prompt', error='ValueError', reason=f'non-numeric retrieval score {score!r}; using zero for ranking', level='WARNING')
                        score = 0
                    row['score'] = score
                    sections.append((score, len(sections), row, value['data']['items']))
        trimmed = False
        def drop_history():
            for index, item in enumerate(history):
                if item.get('history'):
                    history.pop(index)
                    return True
            return False
        def drop_section():
            if not sections:
                return False
            selected = min(sections, key=lambda entry: (entry[0], entry[1]))
            selected[3].remove(selected[2])
            sections.remove(selected)
            return True
        def render():
            for item, value in envelopes:
                item['content'] = '\n\n'.join(row['text'] for row in value['data']['items']) if writer else json.dumps(value, ensure_ascii=False)
            transcript = '\n\n'.join(f"{item['role'].title()}: {item['content']}" for item in history)
            if writer:
                return mask_text(transcript + '\n\n' + self.settings.prompts['local_writer'].strip())
            text = transcript if plain or no_tools else self.settings.prompts['local_tool_protocol'].replace('{{tools}}', json.dumps(tools, separators=(',', ':'))).replace('{{conversation}}', transcript)
            return mask_text(text) + ('\n\nAssistant:' if plain or no_tools else '')
        while sum(self.tokens(item['content']) for item in history if item.get('history')) > cfg['history_token_budget']:
            trimmed = drop_history() or trimmed
        # Optional history is always removed before retrieval context when the complete prompt is too large.
        while not self.fits(render()) and drop_history():
            trimmed = True
        rules = cfg['retrieval']
        while sections and (len(sections) > rules['parents_to_model'] or sum(self.tokens(str(entry[2].get('text', ''))) for entry in sections) > rules['context_token_budget'] or not self.fits(render())):
            trimmed = drop_section() or trimmed
        prompt = render()
        if trimmed:
            log.step('AGENT', 'local prompt trimmed', model=self.model, prompt_tokens_estimated=self.tokens(prompt), why='history first, then lowest-scored retrieved sections; local context budgets', level='WARNING')
        if not self.fits(prompt):
            raise LocalLLMError('Local prompt exceeds configured context or server character limit')
        if MOBILE.search(prompt) or ACCOUNT.search(prompt):
            raise LocalLLMError('Unmasked identity in provider prompt')
        return prompt

    def write(self, messages):
        text = self.request(self.prompt(messages, writer=True))
        cut = min((text.find(marker) for marker in self.settings.local_llm['stop_markers'] if marker in text), default=len(text))
        reply = text[:cut].strip()
        if not reply:
            raise LocalLLMError('Local writer returned an empty answer', http_status=self.last_response.get('http_status'))
        return reply

    def request(self, prompt, *, image=None, kind='llm', tool_selection=False):
        cfg, url = self.settings.local_llm, self.settings.secrets.local_llm_url
        if not url:
            raise LocalLLMError('LOCAL_LLM_URL is not configured')
        image_tokens = cfg['image_token_cost'] if image is not None else 0
        if not self.fits(prompt, image_tokens):
            raise LocalLLMError('Local prompt exceeds configured context or server character limit')
        estimated_prompt = self.tokens(prompt) + image_tokens
        payload = {'server': cfg['server_id'], 'prompt': prompt, 'max_tokens': cfg['max_tokens_tool'] if tool_selection else cfg['max_tokens']}
        if image is not None:
            payload['image'] = image
        started = perf_counter()
        self.last_response = {}
        area = 'VLM' if kind == 'vlm' else 'LLM'
        try:
            with log.span(area, 'local server request', model=self.model, prompt_tokens_estimated=estimated_prompt, image_tokens=image_tokens) as span:
                with httpx.Client(transport=self.transport, timeout=cfg['timeout_seconds']) as client:
                    response = client.post(url, json=payload)
                self.last_response = {'http_status': response.status_code, 'latency_seconds': perf_counter() - started}
                response.raise_for_status()
                body = response.json()
                text = body[cfg['text_field']]
                if not isinstance(text, str):
                    raise ValueError('Configured text field is not a string')
                estimated = body.get('prompt_tokens') is None or body.get('completion_tokens') is None
                incoming = body.get('prompt_tokens') if body.get('prompt_tokens') is not None else estimated_prompt
                outgoing = body.get('completion_tokens') if body.get('completion_tokens') is not None else math.ceil(len(text) / self.settings.chunking['chars_per_token'])
                counted = meter.add(session_context.get(), self.model, kind, incoming, outgoing, estimated=estimated)
                self.last_response.update(response=text, prompt_tokens=body.get('prompt_tokens'), completion_tokens=body.get('completion_tokens'))
                self.usage.append({'model': self.model, 'prompt_tokens': incoming, 'completion_tokens': outgoing, 'estimated': estimated, 'cached': False})
                span['prompt_tokens'] = body.get('prompt_tokens')
                log.step(area, 'local token usage', model=self.model, prompt_tokens_estimated=estimated_prompt, image_tokens=image_tokens, prompt_tokens=body.get('prompt_tokens'), http_status=response.status_code, **counted)
                return text
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            response = getattr(exc, 'response', None)
            raise LocalLLMError(f'Local request failed: {type(exc).__name__}', http_status=response.status_code if response is not None else self.last_response.get('http_status'), server_text=response.text if response is not None else str(exc)) from exc

    def parse(self, text, *, allow_tools=True):
        cut = min((text.find(marker) for marker in self.settings.local_llm['stop_markers'] if marker in text), default=len(text))
        cleaned = text[:cut].strip()
        cleaned = re.sub(r'^```(?:json)?\s*|\s*```$', '', cleaned, flags=re.I).strip()
        start = cleaned.find('{')
        try:
            if start < 0:
                raise ValueError('missing object')
            value, _ = json.JSONDecoder().raw_decode(cleaned, start)
        except ValueError:
            self.reject(text, 'not a single valid JSON object')
        if not isinstance(value, dict) or set(value) - {'tool_calls', 'reply'}:
            self.reject(text, 'not a single valid JSON object')
        if 'tool_calls' not in value:
            if isinstance(value.get('reply'), str) and value['reply'].strip():
                return value['reply']
            self.reject(text, 'reply must be a non-empty string')
        if not isinstance(value['tool_calls'], list) or not value['tool_calls']:
            self.reject(text, 'empty tool call')
        if any(not isinstance(call, dict) or not call or not call.get('name') for call in value['tool_calls']):
            self.reject(text, 'empty tool call')
        if not allow_tools or self.registry is None:
            self.reject(text, 'tool calls are not allowed for this final reply')
        def strip_identity(item):
            if isinstance(item, dict):
                return {key: strip_identity(child) for key, child in item.items() if key not in {'user_id', 'account_id', 'account_number'}}
            return [strip_identity(child) for child in item] if isinstance(item, list) else item
        for call in value['tool_calls']:
            if not isinstance(call['name'], str) or call['name'] not in self.registry.visible():
                self.reject(text, f"unknown tool {call['name']}")
            if set(call) != {'name', 'arguments'} or not isinstance(call.get('arguments'), dict):
                self.reject(text, 'arguments failed validation: arguments')
            arguments = strip_identity(call['arguments'])
            if self.model.startswith('local/') and call['name'].startswith('search_'):
                customer = getattr(self.registry, 'customer_message', '')
                query_words = set(re.findall(r'\w+', str(arguments.get('query', '')).casefold()))
                if customer and not query_words.intersection(re.findall(r'\w+', customer.casefold())):
                    arguments['query'] = customer
                    log.step('AGENT', 'app.local_llm.LocalClient.parse', tool=call['name'], why="model query did not match the customer's words")
            try:
                call['arguments'] = self.registry.tools[call['name']].args_schema.model_validate(arguments).model_dump(mode='json', exclude_none=True)
            except ValidationError as exc:
                field = '.'.join(map(str, exc.errors()[0]['loc'])) or 'arguments'
                self.reject(text, f'arguments failed validation: {field}')
        return {'tool_calls': value['tool_calls']}

    def reject(self, text, reason):
        log.step('AGENT', 'local protocol rejected', model=self.model, error='ValueError', reason=reason, raw_output=mask_text(text)[:self.settings.local_llm['raw_output_chars']], why='only validated replies or visible tool calls may reach the runner')
        raise ValueError(reason)

    def chat(self, messages, *, plain=False, no_tools=False):
        if plain:
            return self.request(self.prompt(messages, plain=True))
        if no_tools:
            final = [*messages, {'role': 'system', 'content': self.settings.local_llm['final_reply_instruction']}]
            text = self.request(self.prompt(final, no_tools=True))
            try:
                return self.parse(text, allow_tools=False)
            except (ValueError, TypeError) as exc:
                raise LocalLLMError('Final reply failed JSON validation', http_status=self.last_response.get('http_status'), server_text=str(exc)) from exc
        history = list(messages)
        for attempt in range(self.settings.local_llm['protocol_retries'] + 1):
            text = self.request(self.prompt(history), tool_selection=not any(item['role'] == 'tool' for item in history))
            try:
                return self.parse(text)
            except (ValueError, TypeError) as exc:
                if attempt == self.settings.local_llm['protocol_retries']:
                    raise LocalLLMError('Local JSON/tool protocol failed', http_status=self.last_response.get('http_status'), server_text=str(exc)) from exc
                history.append({'role': 'assistant', 'content': text})
