"""One bounded CrewAI agent per turn, provider fallback and grounded output."""

import json
import os
from datetime import date

# Crew telemetry is unnecessary for banking data; disable it before importing the framework.
for _key in ('CREWAI_TRACING_ENABLED', 'CREWAI_TELEMETRY_ENABLED', 'LITELLM_TELEMETRY'):
    os.environ[_key] = 'false'
os.environ['OTEL_SDK_DISABLED'] = 'true'
os.environ['DO_NOT_TRACK'] = '1'

import httpx
from crewai import Agent, Crew, LLM, Task
from openai import OpenAI

from app.config import ROOT, get_settings
from app.ingest.models import digest
from app.llm import QuotaGuard, _vision_fallback_error, record_usage
from app.usage import meter, session_context
from app.log import log, mask_text, mask_payload, traced
from app.verify import check, canonical_citations


class LoggedTransport(httpx.BaseTransport):
    def __init__(self, quota, usage, *, cache=False, sample='', schemas=None, validators=None):
        self.inner = httpx.HTTPTransport()
        self.quota, self.usage = quota, usage
        self.cache, self.sample = cache, sample
        self.schemas = schemas or {}
        self.validators = validators or {}

    def proposals(self, payload):
        result = []
        for choice in payload.get('choices', []):
            for call in choice.get('message', {}).get('tool_calls', []):
                function = call.get('function', {})
                name = function.get('name')
                try:
                    self.validators[name].model_validate(json.loads(function.get('arguments', '{}')))
                    valid = True
                except (KeyError, ValueError):
                    valid = False
                result.append({'name': name, 'schema_valid': valid})
        return result

    def prepare(self, request):
        body = json.loads(request.content)
        body['messages'] = mask_payload(body.get('messages', []))
        # Groq requires an explicit empty properties map on argument-free native tools.
        for tool in body.get('tools', []):
            name = tool['function']['name']
            if name in self.schemas:
                schema = self.schemas[name]
                def expand(value):
                    if isinstance(value, list):
                        return [expand(child) for child in value]
                    if not isinstance(value, dict):
                        return value
                    if '$ref' in value:
                        return expand(schema['$defs'][value['$ref'].split('/')[-1]])
                    return {key: expand(child) for key, child in value.items() if key not in {'$defs', 'title'}}
                tool['function']['parameters'] = expand(schema)
                tool['function']['strict'] = False
            parameters = tool['function'].setdefault('parameters', {})
            parameters.setdefault('properties', {})
            if not parameters['properties']:
                parameters.pop('required', None)
        payload = json.dumps(body, ensure_ascii=False).encode()
        from app.prepass import MOBILE, ACCOUNT
        # Independent scan catches any future prompt builder that bypasses the pre-pass.
        for message in body['messages']:
            content = str(message.get('content', ''))
            if MOBILE.search(content) or ACCOUNT.search(content):
                raise ValueError('Unmasked identity in provider prompt')
        return body, payload

    @traced('LLM', 'provider request')
    def handle_request(self, request):
        cfg = get_settings()
        body, payload = self.prepare(request)
        key = digest(payload + self.sample.encode() + str(cfg.conversation['agent']['cache_version']).encode())
        path = ROOT / 'data/test_cache' / f'agent_{digest(session_context.get() or "bank")}_{key}.json'
        if self.cache and path.exists():
            saved = json.loads(path.read_text(encoding='utf-8'))
            self.usage.append({**saved['usage'], 'cached': True, 'tool_proposals': self.proposals(saved['payload'])})
            record_usage(model=body['model'], cached=True)
            return httpx.Response(200, json=saved['payload'], headers=saved.get('headers', {}))
        self.quota.reserve('llm')
        outbound = httpx.Request(request.method, request.url, headers=request.headers, content=payload, extensions=request.extensions)
        outbound.headers['content-length'] = str(len(payload))
        with log.span('LLM', 'CrewAI native request', model=body['model']):
            response = self.inner.handle_request(outbound)
            response.read()
        log.step('LLM', 'provider status', status=response.status_code)
        if response.status_code >= 400:
            log.step('LLM', 'provider error', detail=response.text)
        if response.status_code == 200:
            result = response.json()
            usage = result.get('usage', {})
            self.usage.append({**usage, 'cached': False, 'model': body['model'], 'tpm': response.headers.get('x-ratelimit-limit-tokens'), 'tool_proposals': self.proposals(result)})
            counted = meter.add(session_context.get(), body['model'], 'llm', usage.get('prompt_tokens', 0), usage.get('completion_tokens', 0))
            log.step('LLM', 'token usage', model=body['model'], **counted)
            if self.cache:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps({'payload': result, 'usage': self.usage[-1], 'headers': {key: value for key, value in response.headers.items() if key.startswith('x-ratelimit-')}}), encoding='utf-8')
        return response

    def close(self):
        self.inner.close()


def system_prompt(session, registry):
    cfg, blocks = get_settings(), get_settings().prompts['agent_blocks']
    auth = session['auth']
    auth_block = blocks['auth_block']['anonymous']
    if auth['state'] == 'AWAITING_OTP':
        auth_block = blocks['auth_block']['awaiting']
    if auth['state'] == 'VERIFIED':
        user = registry.store.one('users', {'user_id': auth['user_id']})
        auth_block = blocks['auth_block']['verified'].format(first_name=user['full_name'].split()[0])
    active = session.get('active_flow')
    flow_block = blocks['flow_block']['none']
    if active:
        definition = cfg.flows[active['flow']]
        if active['status'] == 'AWAITING_CONFIRM':
            flow_block = blocks['flow_block']['confirming'].format(flow_title=definition['title'])
        else:
            step = registry.flows.next_step(session)
            flow_block = blocks['flow_block']['open'].format(flow_title=definition['title'], filled=mask_text(json.dumps(active['slots'])), missing=[step['slot']] if step else [], next_question=step['question'] if step else '')
    docs = registry.files()['data']['files']
    docs_block = blocks['docs_block']['some'].format(files=docs) if docs else blocks['docs_block']['none']
    prompt = cfg.prompts['agent_system']
    for key, value in {'bank_name': cfg.bank['name'], 'today': date.today().isoformat(), 'auth_block': auth_block, 'flow_block': flow_block, 'docs_block': docs_block}.items():
        prompt = prompt.replace('{{' + key + '}}', str(value))
    if cfg.models.agent.startswith('local/'):
        prompt += '\n' + cfg.local_llm['scope_instruction']
    return mask_text(prompt)


class Runner:
    def __init__(self, llm=None, *, model=None, cache=False, sample='', quota=None):
        self.llm, self.model, self.cache, self.sample, self.quota = llm, model, cache, sample, quota
        self.usage = []

    def _fake(self, messages, registry, *, llm=None):
        client = llm or self.llm
        seen = set()
        for _ in range(get_settings().agent['max_steps']):
            with log.span('LLM', 'scripted request' if llm is None else 'local tool turn', model=getattr(client, 'model', 'scripted')):
                result = client.chat(messages)
            if isinstance(result, str):
                return result
            outputs = []
            if llm is not None:
                messages.append({'role': 'assistant', 'content': json.dumps(result)})
            for call in result.get('tool_calls', []):
                if llm is not None:
                    key = (call['name'], json.dumps(call.get('arguments', {}), sort_keys=True))
                    if key in seen:
                        log.step('AGENT', 'repeated tool stopped', model=client.model, why='same tool and arguments already executed; final reply without tools')
                        return client.chat(messages, no_tools=True)
                    seen.add(key)
                tool = registry.tools.get(call['name'])
                output = tool.invoke(call.get('arguments', {})) if tool and call['name'] in registry.visible() else {'ok': False, 'code': 'NOT_VERIFIED'}
                outputs.append((call['name'], output))
                messages.append({'role': 'tool', 'content': json.dumps(output)})
                if llm is not None:
                    messages.append({'role': 'system', 'content': get_settings().local_llm['tool_result_instruction']})
                    if call['name'] in get_settings().local_llm['direct_display_tools'] and output.get('ok') and output.get('display'):
                        return output['display']
            display = registry.direct_display(outputs)
            if display:
                return display
            if not result.get('tool_calls'):
                return result.get('text', '')
        return get_settings().conversation['safe']['tool_limit']

    @traced('AGENT', 'native agent')
    def _native(self, model, messages, registry, *, repair=False):
        cfg = get_settings()
        if model.startswith('local/'):
            from app.local_llm import LocalClient
            local = LocalClient(registry, model=model, usage=self.usage)
            with log.span('AGENT', 'local agent', model=model):
                return local.chat(messages, plain=True) if repair else self._fake(list(messages), registry, llm=local)
        client = None
        if model.startswith('groq/'):
            transport = LoggedTransport(self.quota or QuotaGuard(), self.usage, cache=self.cache, sample=self.sample, schemas={tool.name: tool.args_schema.model_json_schema() for tool in registry.list()}, validators={tool.name: tool.args_schema for tool in registry.list()})
            client = OpenAI(api_key=cfg.secrets.groq_api_key.get_secret_value(), base_url=cfg.conversation['agent']['endpoint'], timeout=cfg.llm.timeout_seconds, max_retries=0, http_client=httpx.Client(transport=transport, timeout=cfg.llm.timeout_seconds))
            llm = LLM(model=model.removeprefix('groq/'), custom_openai=True, base_url=cfg.conversation['agent']['endpoint'], api_key=cfg.secrets.groq_api_key.get_secret_value(), timeout=cfg.llm.timeout_seconds, max_retries=0, temperature=cfg.models.temperature, max_tokens=cfg.conversation['agent']['max_output_tokens'])
            llm.model = model.removeprefix('groq/')
            llm._get_sync_client = lambda: client
            assert llm._get_sync_client() is client
        else:
            # Native Gemini is available without LiteLLM; keys remain inside the SDK.
            llm = LLM(model=model, api_key=cfg.secrets.gemini_api_key.get_secret_value(), timeout=cfg.llm.timeout_seconds, temperature=cfg.models.temperature)
            gemini_call = llm.call
            def guarded_call(*args, **kwargs):
                (self.quota or QuotaGuard()).reserve('llm')
                with log.span('LLM', 'CrewAI native Gemini request', model=model):
                    from types import SimpleNamespace
                    before = llm.get_token_usage_summary() or {}
                    result = gemini_call(*args, **kwargs)
                    after = llm.get_token_usage_summary() or {}
                    delta = {key: after.get(key, 0) - before.get(key, 0) for key in ('prompt_tokens', 'completion_tokens')}
                    record_usage(SimpleNamespace(usage=SimpleNamespace(**delta)), model)
                    return result
            llm.call = guarded_call
        try:
            if repair:
                with log.span('VERIFY', 'one repair call', model=model):
                    return llm.call(messages)
            original_call = llm.call
            def step_call(*args, **kwargs):
                registry.display_start = len(registry.results)
                result = original_call(*args, **kwargs)
                # Decide from the entire batch so search plus display keeps its write-up.
                registry.display_step = []
                if isinstance(result, list):
                    for call in result:
                        function = call.get('function', call) if isinstance(call, dict) else call.function
                        name = function.get('name') if isinstance(function, dict) else function.name
                        registry.display_step.append(name)
                return result
            llm.call = step_call
            agent = Agent(role=cfg.conversation['agent']['role'], goal=cfg.conversation['agent']['goal'], backstory=messages[0]['content'], llm=llm, tools=registry.list(), allow_delegation=False, verbose=False, max_iter=cfg.agent['max_steps'], max_retry_limit=0)
            task = Task(description=messages[-1]['content'], expected_output=cfg.conversation['agent']['expected_output'], agent=agent)
            with log.span('AGENT', 'turn', model=model):
                return str(Crew(agents=[agent], tasks=[task], verbose=False, memory=False, cache=False, tracing=False).kickoff())
        finally:
            if client:
                client.close()

    @traced('AGENT', 'agent turn')
    def run(self, session, message, registry, *, code_route=None):
        cfg = get_settings()
        local_mode = cfg.models.agent.startswith('local/')
        log.step('AGENT', 'turn start', state=session['auth']['state'], tools=registry.visible(), why='tool visibility follows authentication and active flow state')
        history, used = [], 0
        for item in reversed(session.get('history', [])[-cfg.session['history_turns'] * 2:]):
            cost = len(item['content']) // cfg.chunking['chars_per_token']
            if used + cost > (cfg.local_llm['history_token_budget'] if local_mode else cfg.conversation['agent']['history_token_budget']):
                break
            history.insert(0, item)
            used += cost
        task = cfg.conversation['agent']['task'].format(history=history, message=message)
        messages = [{'role': 'system', 'content': system_prompt(session, registry)}, {'role': 'user', 'content': mask_text(task)}]
        if local_mode:
            task = cfg.conversation['agent']['task'].format(history=[], message=message)
            messages = [messages[0], *[{**item, 'history': True} for item in history], {'role': 'user', 'content': mask_text(task)}]
        reply = None
        writer_sources = None
        writer_grounded = True
        if local_mode and code_route:
            output = registry.tools[code_route].invoke({} if code_route == 'get_balance' else {'query': message})
            log.path('rag-code-routed', reason='code selected ' + code_route + '; local model writes only; visibility and tool validation still apply')
            if output.get('data', {}).get('no_good_match'):
                log.step('AGENT', 'writer skipped', path='rag-code-routed', why='search returned no good match; no model call')
                return {'reply': cfg.messages['answers']['not_found'], 'citations': [], 'usage': self.usage}
            if not output.get('ok'):
                return {'reply': output.get('display') or cfg.messages['answers']['retry_later'], 'citations': [], 'usage': self.usage}
            if code_route == 'get_balance':
                reply = output['display']
            elif not output.get('data', {}).get('items'):
                log.step('AGENT', 'writer skipped', path='rag-code-routed', why='search returned no good match; no model call')
                return {'reply': cfg.messages['answers']['not_found'], 'citations': [], 'usage': self.usage}
            else:
                from app.local_llm import LocalClient
                # Keep source envelopes intact until the shared local budget trimmer renders them.
                messages.insert(-1, {'role': 'tool', 'content': json.dumps(output, default=str)})
                try:
                    client = LocalClient(registry, model=self.model or cfg.models.agent, usage=self.usage)
                    reply = client.write(messages)
                    writer_sources = client.writer_sources
                    writer_grounded = bool(reply.strip()) and client.writer_grounded_ratio >= cfg.local_llm['min_grounded_ratio']
                    log.step('LLM', 'model answered', model=self.model or cfg.models.agent, path='rag-code-routed', why='one plain-text answer from retrieved sources')
                except Exception as exc:
                    self._provider_error(self.model or cfg.models.agent, exc)
                    return {'reply': cfg.messages['answers']['retry_later'], 'citations': [], 'usage': self.usage}
        elif self.llm is not None:
            reply = self._fake(messages, registry)
            log.step('LLM', 'model answered', model='scripted')
        else:
            chain = [self.model] if self.model else [cfg.models.agent, *cfg.models.agent_fallbacks]
            for position, model in enumerate(chain):
                try:
                    reply = self._native(model, messages, registry)
                    log.step('LLM', 'model answered', model=model)
                    break
                except Exception as exc:
                    self._provider_error(model, exc)
                    from app.local_llm import LocalLLMError
                    if not isinstance(exc, LocalLLMError) and not _vision_fallback_error(exc):
                        break
                    if position + 1 < len(chain):
                        log.step('AGENT', 'fallback switch', model=model, next_model=chain[position + 1])
        if reply is None:
            return {'reply': cfg.messages['answers']['retry_later'], 'citations': [], 'usage': self.usage}
        raw_answer = client.writer_raw_output if writer_sources is not None else reply
        reply = canonical_citations(reply)
        if writer_sources is not None:
            reply += '\n\n' + ' '.join(f"[{item['n']}]" for item in writer_sources)
        displays = list(dict.fromkeys(result['display'] for _, result in registry.results if result.get('display')))
        for display in displays:
            if display not in reply:
                reply += '\n' + display
        verification_results = registry.results if writer_sources is None else [(code_route, {'data': {'items': writer_sources}})]
        with log.span('VERIFY', 'number check', module=check.__module__, function=check.__qualname__):
            valid, errors = check(reply, verification_results)
        log.step('VERIFY', 'number and citation check', passed=valid, errors=errors, why='every number and citation must match tool evidence')
        if writer_sources is not None:
            if not valid or not writer_grounded:
                log.step('VERIFY', 'local writer rejected', raw_answer=mask_text(raw_answer)[:cfg.local_llm['writer_raw_answer_chars']], grounded_ratio=client.writer_grounded_ratio, failing_numbers=errors['numbers'], errors=errors, why='writer answer failed source verification' if not valid else 'writer answer is empty or content-word overlap is below the configured threshold')
                reply, writer_sources = self._writer_extract(writer_sources)
                log.step('VERIFY', 'local writer fallback', returned='extractive' if writer_sources else 'safe_message', why='verbatim source passage available' if writer_sources else 'no usable source passage')
            log.step('AGENT', 'final response', chars=len(reply), calls=len(registry.results))
            return {'reply': mask_text(reply), 'citations': writer_sources, 'usage': self.usage}
        if not valid:
            repair = cfg.prompts['repair'].replace('{{bad_numbers}}', json.dumps(errors)).replace('{{sources}}', json.dumps(registry.results, default=str))
            log.step('VERIFY', 'repair required', errors=errors, why='answer failed number or citation validation')
            if self.llm is not None:
                reply = self.llm.chat([{'role': 'system', 'content': mask_text(repair)}])
            else:
                # Reuse the guarded native adapter, never an unmetered repair call.
                try:
                    reply = self._native(self.model or cfg.models.agent, [{'role': 'system', 'content': messages[0]['content']}, {'role': 'user', 'content': mask_text(repair)}], registry, repair=True)
                except Exception as exc:
                    self._provider_error(self.model or cfg.models.agent, exc)
                    if local_mode and not cfg.models.agent_fallbacks:
                        return {'reply': cfg.messages['answers']['retry_later'], 'citations': [], 'usage': self.usage}
                    reply = ''
            if not isinstance(reply, str) or not check(reply, registry.results)[0] or not reply:
                reply = cfg.messages['answers']['unverified_number']
        reply = canonical_citations(reply)
        citations = [item for _, result in registry.results for item in result.get('data', {}).get('items', [])]
        log.step('AGENT', 'final response', chars=len(reply), calls=len(registry.results))
        return {'reply': mask_text(reply), 'citations': citations, 'usage': self.usage}

    @staticmethod
    def _writer_extract(sources):
        cfg = get_settings()
        head, _, tail = cfg.conversation['agent']['source_wrapper'].partition('{text}')
        for source in sorted(sources, key=lambda item: item.get('score', 0), reverse=True):
            # Remove the wrapper before quoting; never truncate inside a numeric token.
            passage = source.get('text', '').removeprefix(head.format(**source)).removesuffix(tail).strip()
            limit = cfg.local_llm['writer_extract_chars']
            if len(passage) > limit:
                clipped = passage[:limit]
                passage = clipped.rsplit(None, 1)[0] if any(char.isspace() for char in clipped) else ''
            if not passage:
                continue
            label = cfg.local_llm['writer_source_label'].format(title=source.get('doc_title', ''), page=source.get('page', ''))
            answer = f"{cfg.local_llm['writer_extract_prefix']}\n\n{passage}\n\n{label} [{source['n']}]"
            return answer, [source]
        return cfg.messages['answers']['unverified_number'], []

    @staticmethod
    def _provider_error(model, exc):
        log.step('AGENT', 'error', model=model, error=type(exc.__cause__ or exc).__name__, http_status=getattr(exc, 'http_status', None), reason=getattr(exc, 'server_text', str(exc))[:get_settings().local_llm['error_text_chars']], level='ERROR')
