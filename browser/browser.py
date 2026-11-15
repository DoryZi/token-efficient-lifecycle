"""Allowlisted CLI commands and fixed, read-only observations/checks."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.parse import urlsplit
import uuid
from models import Blocked, ProcessTimeout, run_process

ARITY = {'find': (1, 1), 'snapshot': (0, 1), 'screenshot': (0, 1),
         'goto': (1, 1), 'click': (1, 1), 'dblclick': (1, 1), 'hover': (1, 1),
         'fill': (2, 2), 'select': (2, 2), 'drag': (2, 2), 'check': (1, 1),
         'uncheck': (1, 1), 'press': (1, 1), 'resize': (2, 2),
         'mousemove': (2, 2), 'mousewheel': (2, 2), 'mousedown': (0, 0),
         'mouseup': (0, 0), 'upload': (1, 1), 'tab-list': (0, 0),
         'tab-new': (1, 1), 'tab-select': (1, 1), 'go-back': (0, 0),
         'reload': (0, 0), 'dialog-dismiss': (0, 0)}
READ = {'find', 'snapshot', 'screenshot', 'hover', 'resize', 'mousemove',
        'mousewheel', 'tab-list', 'tab-select', 'goto', 'tab-new', 'go-back',
        'reload', 'dialog-dismiss'}
REFS = {'snapshot', 'screenshot', 'click', 'dblclick', 'hover', 'fill', 'select',
        'check', 'uncheck', 'drag'}
FINAL = re.compile(r'\b(submit|publish|send|purchase|buy|delete|remove|apply now)\b', re.I)


def origin(url):
    parsed = urlsplit(url)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise Blocked('Only http(s) URLs without credentials are supported')
    return f'{parsed.scheme}://{parsed.netloc.lower()}'


def validate_action(action, task):
    command, args = action['command'], action['args']
    if command not in ARITY or not ARITY[command][0] <= len(args) <= ARITY[command][1]:
        raise Blocked('Unknown command or invalid arguments')
    if any(not isinstance(x, str) or '\x00' in x or len(x) > 8000 or x.startswith('-') for x in args):
        # Signed numeric coordinates are handled below, never passed as options.
        if command != 'mousewheel' or not all(re.fullmatch(r'-?\d{1,5}', x) for x in args):
            raise Blocked('Invalid command argument')
    if command in REFS and args:
        targets = args[:2] if command == 'drag' else args[:1]
        if not all(re.fullmatch(r'e\d+', x) for x in targets):
            raise Blocked('Use current snapshot refs; selectors and code are not accepted')
    if command in ('goto', 'tab-new') and origin(args[0]) not in task['allowed_origins']:
        raise Blocked('Navigation outside allowed origins')
    if command == 'upload' and str(Path(args[0]).resolve()) not in task['attachments']:
        raise Blocked('Attachment was not supplied by the task')
    if command in ('resize', 'mousemove', 'tab-select') and not all(re.fullmatch(r'\d{1,5}', x) for x in args):
        raise Blocked('Expected nonnegative numeric arguments')
    if command == 'press' and not re.fullmatch(r'(Control\+|Meta\+|Shift\+|Alt\+)*[A-Za-z0-9]+', args[0]):
        raise Blocked('Invalid keyboard key')
    if action['effect'] not in task['permissions']:
        raise Blocked(f"Task does not authorize {action['effect']}")
    if command not in READ and 'edit' not in task['permissions']:
        raise Blocked('Task does not authorize editing')
    if action['effect'] == 'publish':
        raise Blocked('Use the existing publishing API tool')


class Browser:
    def __init__(self, task, directory, deadline):
        self.task, self.directory, self.deadline = task, Path(directory), deadline
        self.directory.mkdir(parents=True, exist_ok=True)
        self.session = 'bw-' + uuid.uuid4().hex[:12]
        self.cli_directory = self.directory
        self.count, self.started, self.last_image = 0, False, None
        self.last_snapshot, self.url = '', ''
        self.pointer = None
        self.external_attempted = False
        self.events = []
        self.runtime = None
        self.env = dict(os.environ)

    def environment(self):
        if self.runtime is None:
            self.runtime = tempfile.TemporaryDirectory(prefix='bw-')
            runtime = Path(self.runtime.name)
            # Preserve installed browsers when moving CLI cache and socket files.
            if sys.platform == 'linux':
                original_cache = Path(self.env.get('XDG_CACHE_HOME', str(Path.home()/'.cache')))
                self.env.setdefault('PLAYWRIGHT_BROWSERS_PATH', str(original_cache/'ms-playwright'))
            for key, name in (('XDG_CACHE_HOME','cache'), ('XDG_RUNTIME_DIR','run')):
                directory = runtime/name
                directory.mkdir(mode=0o700)
                self.env[key] = str(directory)
        return self.env

    def call(self, args, *, cleanup=False):
        if not cleanup:
            if self.count >= self.task['max_actions']:
                raise Blocked('Browser action limit reached')
            self.count += 1
        timeout = 15 if cleanup else min(30, self.deadline-time.monotonic())
        try:
            code, out, err = run_process(['playwright-cli', '-s='+self.session, *args],
                                        cwd=self.cli_directory, timeout=timeout, env=self.environment())
        except ProcessTimeout as exc:
            path = self.directory / f'{len(self.events):03d}.txt'
            diagnostic = exc.stderr
            if self.runtime:
                for log in Path(self.runtime.name).rglob(self.session+'.err'):
                    diagnostic += log.read_text(errors='replace')
            path.write_text(exc.stdout + diagnostic)
            self.events.append({'command':args[0], 'evidence':str(path), 'code':None})
            raise RuntimeError(f'{args[0]}: {exc}; see {path}') from exc
        except Blocked as exc:
            if timeout > 0:
                raise RuntimeError(str(exc)) from exc
            raise
        path = self.directory / f'{len(self.events):03d}.txt'
        path.write_text(out + err)
        self.events.append({'command': args[0], 'evidence': str(path), 'code': code})
        if code or '### Error' in out:
            raise RuntimeError((out+err)[-1500:])
        return out

    def start(self):
        self.started = True  # cleanup even if open only partially succeeds
        config = self.directory/'private-config.json'
        config.write_text(json.dumps({'browser': {'launchOptions': {'headless': True}}}))
        self.call(['open', self.task['url'], '--browser=chromium', '--config='+str(config.resolve())])
        return self.observe()

    def current_url(self):
        self.url = json.loads(self.call(['--raw', 'eval', '() => location.href']))
        if origin(self.url) not in self.task['allowed_origins']:
            raise Blocked('Page navigated outside allowed origins; no further action taken')
        return self.url

    def observe(self, target=None):
        self.current_url()
        out = self.call(['snapshot'] + ([target] if target else ['--depth=5']))
        matches = re.findall(r'\[Snapshot\]\(([^)]+)\)', out)
        if matches:
            path = (self.cli_directory / matches[-1]).resolve()
            if not path.is_relative_to(self.cli_directory.resolve()):
                raise Blocked('Unexpected snapshot path')
            out = path.read_text()
        self.last_snapshot = out
        return {'url': self.url, 'snapshot': out[:10000], 'truncated': len(out) > 10000}

    def act(self, action):
        validate_action(action, self.task)
        self.current_url()
        command, args = action['command'], action['args']
        if self.external_attempted and command not in {'find','snapshot','screenshot','tab-list'}:
            raise Blocked('External action already attempted; inspect receipt before a new task')
        if command in ('click', 'dblclick', 'press', 'check', 'mousedown', 'mouseup'):
            target = args[0] if args and re.fullmatch(r'e\d+', args[0]) else None
            if command in ('mousedown', 'mouseup'):
                if self.pointer is None:
                    raise Blocked('Move the pointer to an inspected target first')
                x, y = self.pointer
                label = self.call(['--raw', 'eval',
                    f"() => {{const hit=document.elementFromPoint({x},{y}); const el=hit?.closest('button,a,input,[role=button]') || hit; "
                    "return {text:el?.textContent,label:el?.getAttribute('aria-label'),type:el?.type,form:!!el?.form,tag:el?.tagName}}"])
            elif target:
                label = self.call(['--raw', 'eval',
                    "el => ({text:el.innerText,label:el.getAttribute('aria-label'),type:el.type,form:!!el.form})", target])
            else:
                label = self.call(['--raw', 'eval',
                    "() => ({text:document.activeElement?.textContent,type:document.activeElement?.type,form:!!document.activeElement?.form})"])
            info = json.loads(label)
            if info.get('tag') == 'IFRAME':
                raise Blocked('Cannot inspect pointer target inside iframe; use a snapshot ref')
            text = (info.get('text') or '') + ' ' + (info.get('label') or '')
            if re.search(r'\bpublish\b', text, re.I):
                raise Blocked('Use the existing publishing API tool')
            required = 'delete' if re.search(r'\b(delete|remove)\b', text, re.I) else 'submit'
            final = FINAL.search(text) or (info.get('type') == 'submit' and info.get('form'))
            if final and action['effect'] != required:
                raise Blocked('Final-action control requires explicit submit/delete authorization')
            if command == 'press' and args[0] == 'Enter' and action['effect'] != 'submit':
                raise Blocked('Enter may submit; use an explicit control or authorize submission')
        if command == 'snapshot':
            return self.observe(args[0] if args else None)
        if command == 'screenshot':
            path = self.directory / f'image-{self.count:03d}.png'
            self.call(['screenshot', *args, '--filename='+str(path)])
            self.last_image = path
            return {'url': self.url, 'image': str(path), 'snapshot': self.last_snapshot[:6000]}
        if action['effect'] in ('submit', 'delete'):
            self.external_attempted = True
        out = self.call([command, *args])
        if command == 'mousemove':
            self.pointer = tuple(map(int, args))
        if command in ('find', 'tab-list'):
            return {'url': self.url, 'result': out[:10000]}
        self.last_image = None
        return self.observe()

    def checks(self):
        self.current_url()
        results = []
        for check in self.task['checks']:
            kind = check['kind']
            if kind == 'url':
                actual = self.url
                passed = actual == check['expected']
            elif kind == 'visual':
                results.append({'name': check['name'], 'passed': False,
                                'actual': 'Requires decision-model visual verification', 'visual': True})
                continue
            else:
                selector = json.dumps(check.get('selector', 'body'))
                # Code is a fixed template; selectors and expected values are JSON encoded.
                expr = "() => {const e=document.querySelector("+selector+"); return e && e.checkVisibility({checkOpacity:true,checkVisibilityCSS:true}) ? "
                expr += {'text': 'e.innerText', 'value': 'e.value', 'checked': 'e.checked'}[kind]
                expr += ' : null}'
                actual = json.loads(self.call(['--raw', 'eval', expr]))
                passed = (isinstance(actual, str) and check['expected'] in actual) if kind == 'text' else actual == check['expected']
            results.append({'name': check['name'], 'passed': passed, 'actual': str(actual)[:1000]})
        return results

    def close(self):
        try:
            if self.started:
                self.call(['close'], cleanup=True)
        finally:
            self.started = False
            if self.runtime is not None:
                self.runtime.cleanup()
                self.runtime = None
