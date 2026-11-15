#!/usr/bin/env python
"""Bounded, subscription-only browser task runner."""
from __future__ import annotations
import argparse
import json
import os
import re
from pathlib import Path
import tempfile
import time
import uuid
from browser import Browser, origin
from models import Blocked, ModelResponseError, Models, check_auth

ROOT = Path(__file__).resolve().parent
DEFAULTS = {'claude': {'worker': 'haiku', 'decision': 'sonnet'},
            'codex': {'worker': 'gpt-6-luna', 'decision': 'gpt-6.1-sol'}}


def load_task(path):
    return validate_task(json.loads(Path(path).read_text()), Path(path).resolve().parent)


def browse_task(url, goal, read_only=False, max_steps=None):
    """Build the same task dict a task file would hold, for the browse command."""
    task = {'objective': goal, 'url': url,
            'permissions': ['read'] if read_only else ['read', 'edit'],
            'checks': [{'name': 'Goal reached', 'kind': 'visual', 'expected': goal}]}
    if max_steps is not None:
        task['max_actions'] = max_steps
    return validate_task(task, Path.cwd())


def validate_task(task, base_dir):
    task.pop('expected_status', None)  # Benchmark ground truth is never model context.
    for key in ('objective', 'url', 'checks'):
        if not task.get(key):
            raise Blocked(f'Task requires {key}')
    task.setdefault('facts', {})
    task.setdefault('permissions', ['read', 'edit'])
    if not isinstance(task['permissions'], list) or not set(task['permissions']) <= {'read','edit','submit','delete'}:
        raise Blocked('Permissions must be read/edit/submit/delete; publishing uses API tools')
    task.setdefault('allowed_origins', [origin(task['url'])])
    task['allowed_origins'] = [origin(x) for x in task['allowed_origins']]
    if origin(task['url']) not in task['allowed_origins']:
        raise Blocked('Starting URL must be allowed')
    task.setdefault('attachments', [])
    task['attachments'] = [str((Path(base_dir) / p).resolve()) for p in task['attachments']]
    if any(not Path(p).is_file() for p in task['attachments']):
        raise Blocked('Attachment does not exist')
    for key, default, maximum in [('max_actions',60,300), ('max_seconds',600,3600),
                                  ('max_consultations',3,10)]:
        task.setdefault(key, default)
        if type(task[key]) is not int or not 1 <= task[key] <= maximum:
            raise Blocked(f'Invalid {key}')
    for key in ('attach', 'session', 'profile_directory'):
        if key in task:
            raise Blocked(f'{key} is not supported: attaching to an existing browser is not available; tasks run in a private headless browser')
    if len(json.dumps(task)) > 10000:
        raise Blocked('Task exceeds 10,000 characters; provide only relevant facts')
    for check in task['checks']:
        if check.get('kind') not in ('text','value','checked','url','visual') or not check.get('name') or 'expected' not in check:
            raise Blocked('Each check needs name, kind and expected')
        if check['kind'] == 'checked' and type(check['expected']) is not bool:
            raise Blocked('checked expects a boolean')
        if check['kind'] != 'checked' and not isinstance(check['expected'], str):
            raise Blocked('Check expected value must be text')
    return task


def run_task(task, backend, config, out, mode='mixed', model_factory=Models, browser_factory=Browser, auth=True):
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    start = time.monotonic()
    deadline = start + task['max_seconds']
    result = {'status':'blocked','reason':'Incomplete','backend':backend,'mode':mode,
              'checks':[], 'consultations':0,'actions':0,'models':[], 'evidence':str(out),
              'questions':[], 'cleanup_error':None}
    browser = browser_factory(task, out / 'browser', deadline)
    # Outside the repository: no inherited project instructions or parent history.
    with tempfile.TemporaryDirectory(prefix='browser-model-') as temp:
        models = model_factory(backend, config, Path(temp), deadline)
        try:
            if auth:
                check_auth(backend, temp)
            observation = browser.start()
            memory, recent, failures, pending, recovery = '', [], 0, None, False
            last_signature, repeats = None, 0
            while time.monotonic() < deadline:
                strong = mode == 'strong' or pending is not None or failures >= 2
                if strong and mode != 'strong':
                    if mode == 'cheap' or result['consultations'] >= task['max_consultations']:
                        raise Blocked('Decision-model consultation limit reached')
                    result['consultations'] += 1
                packet = {'task':task,'observation':observation,'memory':memory,
                          'recent':recent[-4:], 'question':pending,
                          'role':'decision' if strong else 'worker'}
                try:
                    action = models.ask(packet, strong=strong, image=browser.last_image)
                except ModelResponseError as exc:
                    if strong:
                        raise
                    pending = str(exc) + '. Propose the next valid action using the current page evidence.'
                    continue
                memory = action['memory']
                pending = None
                if action['kind'] == 'blocked':
                    result['questions'] = [action['reason']]
                    raise Blocked(action['reason'])
                if action['kind'] == 'consult':
                    if strong:
                        raise Blocked('Decision model could not resolve the task: '+action['reason'])
                    pending = action['reason']
                    continue
                if action['kind'] == 'done':
                    checks = browser.checks()
                    visual = [c for c in checks if c.get('visual')]
                    if visual:
                        if not strong or browser.last_image is None:
                            observation = browser.act({'command':'screenshot','args':[], 'effect':'read'})
                            pending = 'Verify visual checks against attached current screenshot. Return done only if all pass.'
                            continue
                        for c in visual:
                            c.update(passed=True, actual='Decision model verified attached screenshot: '+action['reason'],
                                     evidence=str(browser.last_image))
                    result['checks'] = checks
                    if all(c['passed'] for c in checks):
                        result.update(status='completed', reason=action['reason'])
                        break
                    observation = {'failed_checks':[c for c in checks if not c['passed']],
                                   'page':browser.observe()}
                    failures += 1
                    recent.append({'check_failure':observation['failed_checks']})
                    if recovery:
                        raise Blocked('Verification still fails after recovery')
                    if strong:
                        recovery = True
                    continue
                try:
                    observation = browser.act(action)
                    signature = json.dumps({'command':action['command'],'args':action['args'], 'observation':observation},sort_keys=True)
                    repeats = repeats + 1 if signature == last_signature else 0
                    last_signature = signature
                    failures = 2 if repeats >= 2 else 0
                    if recovery and failures:
                        raise Blocked('No progress after recovery')
                    if strong and packet['question'] is None:
                        recovery = True
                    recent.append({'command':action['command'],'args':action['args'],'ok':True})
                except RuntimeError as exc:
                    if isinstance(exc, Blocked):
                        raise
                    # A command error may occur after an external mutation already happened.
                    if action['effect'] in ('submit','delete'):
                        observation = browser.observe()
                        result['checks'] = browser.checks()
                        if all(c['passed'] for c in result['checks']):
                            result.update(status='completed',reason='Receipt verified after uncertain command response')
                            break
                        raise Blocked('External action outcome uncertain; inspect receipt before any retry')
                    failures += 1
                    if recovery:
                        raise Blocked('Recovery failed: '+str(exc))
                    if strong:
                        recovery = True
                    observation = {'error':str(exc), 'page':browser.observe()}
                    recent.append({'command':action['command'],'error':str(exc)})
            else:
                raise Blocked('Task time limit reached')
        except (Blocked, OSError, ValueError, RuntimeError, KeyboardInterrupt) as exc:
            result['reason'] = str(exc)[:1000] or 'Interrupted'
        finally:
            try:
                browser.close()
            except (Blocked, OSError, RuntimeError) as exc:
                result['cleanup_error'] = str(exc)[:500]
            result['actions'] = browser.count
            result['models'] = models.calls
            if hasattr(models, 'close'):
                models.close()
            # Retain actual evidence, without copying auth or credential material.
            import shutil
            shutil.copytree(temp, out / 'models', dirs_exist_ok=True)
            result['seconds'] = round(time.monotonic()-start,2)
            (out / 'result.json').write_text(json.dumps(result, indent=2))
    return result


def setup(config_path, backend, worker=None, decision=None):
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    selected = dict(DEFAULTS[backend])
    if worker: selected['worker'] = worker
    if decision: selected['decision'] = decision
    with tempfile.TemporaryDirectory(prefix='browser-setup-') as tmp:
        check_auth(backend, tmp)
        if backend == 'codex':
            cache = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex'))) / 'models_cache.json'
            names = {m['slug'] for m in json.loads(cache.read_text()).get('models',[])} if cache.exists() else set()
            if names and not set(selected.values()) <= names:
                raise Blocked('Selected Codex model missing from local catalog; supply explicit model IDs')
        # Availability probes are small subscription calls, never paid API fallbacks.
        probe = Models(backend, selected, tmp, time.monotonic()+240)
        try:
            for strong in (False, True):
                answer = probe.ask({'instruction':'Return kind done, command empty, args [], effect read, reason ready, memory empty.'}, strong)
                if answer['kind'] != 'done':
                    raise Blocked('Model availability probe failed')
        finally:
            probe.close()
    config[backend] = selected
    config.setdefault('default_backend', None)  # only benchmark evidence should select it
    config_path.write_text(json.dumps(config,indent=2)+'\n')
    return {'backend':backend,'models':selected,'verified':True,'default_backend':config['default_backend']}


def public_result(result):
    """Keep the parent conversation small; full per-call evidence stays in result.json."""
    if 'status' not in result:
        return result
    groups = {}
    for call in result.get('models', []):
        key = call['model'] + '/' + call['role']
        group = groups.setdefault(key, {'calls':0, 'input_tokens':0, 'output_tokens':0, 'unknown_usage_calls':0})
        group['calls'] += 1
        usage = call['usage']
        if usage is None:
            group['unknown_usage_calls'] += 1
        else:
            group['input_tokens'] += sum(usage.get(k,0) for k in ('input_tokens','cache_creation_input_tokens','cache_read_input_tokens'))
            group['output_tokens'] += usage.get('output_tokens',0)
    return {key: result[key] for key in ('status','reason','backend','seconds','actions','consultations','cleanup_error','evidence')} | {
        'checks': [{'name':c['name'][:100], 'passed':c['passed']} for c in result['checks'][:12]],
        'models': groups, 'details':str(Path(result['evidence'])/'result.json')}


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    setup_p = sub.add_parser('setup')
    setup_p.add_argument('--backend',choices=['claude','codex'],required=True)
    setup_p.add_argument('--worker-model')
    setup_p.add_argument('--decision-model')
    setup_p.add_argument('--config',type=Path,default=ROOT/'.browser-worker.json')
    browse = sub.add_parser('browse', help='Open a URL and work toward a goal')
    browse.add_argument('url')
    browse.add_argument('--goal', required=True)
    browse.add_argument('--backend',choices=['claude','codex'],required=True)
    browse.add_argument('--read-only',action='store_true',help='Allow reading only, no edits')
    browse.add_argument('--max-steps',type=int,help='Maximum browser actions (1 to 300)')
    browse.add_argument('--mode',choices=['mixed','cheap','strong'],default='mixed')
    browse.add_argument('--config',type=Path,default=ROOT/'.browser-worker.json')
    browse.add_argument('--out',type=Path,help='Evidence directory (must not exist)')
    run = sub.add_parser('run')
    run.add_argument('--task-file',type=Path,required=True)
    run.add_argument('--backend',choices=['claude','codex'],required=True)
    run.add_argument('--mode',choices=['mixed','cheap','strong'],default='mixed')
    run.add_argument('--config',type=Path,default=ROOT/'.browser-worker.json')
    run.add_argument('--out',type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'setup':
            result = setup(args.config,args.backend,args.worker_model,args.decision_model)
        else:
            if not args.config.exists():
                raise Blocked('Run setup for this backend first')
            config = json.loads(args.config.read_text())
            if args.backend not in config:
                raise Blocked('Run setup for this backend first')
            if args.command == 'browse':
                task = browse_task(args.url, args.goal, args.read_only, args.max_steps)
            else:
                task = load_task(args.task_file)
            out = args.out or ROOT/'runs'/uuid.uuid4().hex
            result = run_task(task,args.backend,config[args.backend],out,args.mode)
        print(json.dumps(public_result(result),indent=2))
        return 0 if result.get('status','completed') == 'completed' else 2
    except (Blocked, OSError, ValueError) as exc:
        print(json.dumps({'status':'blocked','reason':str(exc)}))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
