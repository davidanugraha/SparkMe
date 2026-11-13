"""Codex App Server adapter with transcript tools supplied by the host."""
import asyncio
import contextlib
import json
import os
from pathlib import Path
import shutil
import tempfile

from ..judging.prompts import system_prompt, user_prompt


PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
STREAM_LIMIT_BYTES = 8 * 1024 * 1024
STDERR_READ_BYTES = 4_096
STDERR_CHUNKS_TO_KEEP = 8
ERROR_DETAIL_CHARACTERS = 2_000
PROCESS_STOP_TIMEOUT_SECONDS = 5

INITIALIZE_REQUEST_ID = 1
START_THREAD_REQUEST_ID = 2
START_TURN_REQUEST_ID = 3
JSON_RPC_METHOD_NOT_FOUND = -32601

# Adapted from continual-learning's isolated App Server pattern.
DISABLED = ('apps', 'browser_use', 'browser_use_external', 'browser_use_full_cdp_access',
            'computer_use', 'deferred_executor', 'goals',
            'hooks', 'image_generation', 'multi_agent', 'plugins', 'remote_plugin',
            'request_permissions_tool', 'shell_tool', 'skill_search', 'unified_exec',
            'workspace_dependencies')


async def run_async(session, options):
    with tempfile.TemporaryDirectory(prefix='sparkme-codex-') as temporary:
        root = Path(temporary)
        home = root / 'codex-home'
        home.mkdir(mode=PRIVATE_DIRECTORY_MODE)
        source = Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))) / 'auth.json'
        if source.is_file():
            shutil.copyfile(source, home / 'auth.json')
            (home / 'auth.json').chmod(PRIVATE_FILE_MODE)
        config = {f'features.{name}': False for name in DISABLED}
        config.update({'web_search': 'disabled', 'mcp_servers': {}, 'analytics.enabled': False,
                       'features.code_mode': True, 'features.code_mode_host': True,
                       'tools.update_plan.enabled': False, 'orchestrator.skills.enabled': False,
                       'orchestrator.mcp.enabled': False, 'skills.include_instructions': False,
                       'include_apps_instructions': False, 'include_environment_context': False})
        command = [options.codex_executable, 'app-server', '--stdio']
        for key, value in config.items():
            command.extend(['-c', f'{key}={json.dumps(value)}'])
        process = await asyncio.create_subprocess_exec(
            *command, cwd=root, env={**os.environ, 'CODEX_HOME': str(home)},
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=STREAM_LIMIT_BYTES)
        stderr = []

        async def drain():
            while data := await process.stderr.read(STDERR_READ_BYTES):
                stderr.append(data.decode(errors='replace'))
                del stderr[:-STDERR_CHUNKS_TO_KEEP]

        draining = asyncio.create_task(drain())

        async def send(message):
            process.stdin.write((json.dumps(message) + '\n').encode())
            await process.stdin.drain()

        async def receive():
            line = await process.stdout.readline()
            if not line:
                raise RuntimeError('Codex App Server exited: ' + ''.join(stderr)[-ERROR_DETAIL_CHARACTERS:])
            return json.loads(line)

        async def request(rid, method, params):
            await send({'id': rid, 'method': method, 'params': params})
            while True:
                message = await receive()
                if message.get('id') == rid:
                    if 'error' in message:
                        raise RuntimeError(f'Codex {method}: {message["error"]}')
                    return message['result']
                await event(message)

        async def event(message):
            method, params = message.get('method'), message.get('params', {})
            if method in ('turn/completed', 'error', 'item/completed'):
                session.trace({'backend_event': method, 'params': params})
            if method == 'item/tool/call' and 'id' in message:
                if params.get('namespace') is not None:
                    raise RuntimeError('Unexpected tool namespace')
                result = session.dispatch(params.get('tool'), params.get('arguments'))
                await send({'id': message['id'], 'result': {
                    'success': result['ok'], 'contentItems': [{'type': 'inputText', 'text': json.dumps(result)}]}})
            elif 'id' in message and method:
                await send({'id': message['id'], 'error': {'code': JSON_RPC_METHOD_NOT_FOUND, 'message': 'Unsupported host request'}})
                raise RuntimeError(f'Unexpected Codex host request: {method}')
            elif method == 'error':
                if not params.get('willRetry', False):
                    raise RuntimeError(f'Codex provider error: {params.get("error")}')

        try:
            await request(INITIALIZE_REQUEST_ID, 'initialize', {'clientInfo': {'name': 'sparkme_judge', 'version': '1'},
                                          'capabilities': {'experimentalApi': True}})
            await send({'method': 'initialized', 'params': {}})
            params = {'ephemeral': True, 'environments': [], 'approvalPolicy': 'never',
                      'developerInstructions': system_prompt(session),
                      'dynamicTools': [{'type': 'function', 'name': tool.name, 'description': tool.description,
                                        'inputSchema': tool.parameters}
                                       for tool in session.tools.values()]}
            if options.model:
                params['model'] = options.model
            thread = await request(START_THREAD_REQUEST_ID, 'thread/start', params)
            session.state['model'] = thread.get('model', options.model)
            session.persist()
            thread_id = thread['thread']['id']
            await request(START_TURN_REQUEST_ID, 'turn/start', {'threadId': thread_id,
                                          'input': [{'type': 'text', 'text': user_prompt(session)}]})
            while not session.done:
                message = await receive()
                await event(message)
                if message.get('method') == 'turn/completed' and not session.done:
                    detail = message.get('params', {}).get('turn', {}).get('error')
                    raise RuntimeError(f'Codex ended without a validated submit: {detail}')
        finally:
            if process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), PROCESS_STOP_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    process.kill()
                    await process.wait()
            draining.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await draining


def run(session, options):
    asyncio.run(run_async(session, options))
