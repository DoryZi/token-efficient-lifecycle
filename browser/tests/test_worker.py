import json
import time
from pathlib import Path
import pytest
from browser import validate_action, origin, Browser
import browser_worker
from browser_worker import load_task, run_task, browse_task
from models import Blocked, Models, child_env, run_process


def task(tmp_path, **kwargs):
    data = dict(objective='Fill application and save draft',url='https://example.com/form',
                facts={'name':'Known Name'},checks=[{'name':'name','kind':'value','selector':'#name','expected':'Known Name'}])
    data.update(kwargs)
    path = tmp_path/'task.json'
    path.write_text(json.dumps(data))
    return load_task(path)


def action(command='fill', args=None, kind='action', **kw):
    return dict(kind=kind,command=command,args=['e1','Known Name'] if args is None else args, effect='edit',reason='Next step',memory='',**kw)


@pytest.mark.parametrize('command,args', [('eval',['document.cookie']),('run-code',['evil']),
    ('close-all',[]),('fill',['getByRole("textbox")','x']),('click',['--help']),
    ('upload',['/etc/passwd']),('goto',['https://elsewhere.com']),('goto',['javascript:alert(1)'])])
def test_reject_unsafe_commands(tmp_path,command,args):
    with pytest.raises(Blocked): validate_action(action(command,args),task(tmp_path))


def test_fill_shell_text_is_literal(tmp_path):
    validate_action(action(args=['e1','$(touch /tmp/unsafe); `id`']),task(tmp_path))


def test_read_only_cannot_mutate(tmp_path):
    with pytest.raises(Blocked): validate_action(action(),task(tmp_path,permissions=['read']))


def test_private_browser_uses_bundled_headless_chromium(tmp_path):
    b=Browser(task(tmp_path),tmp_path/'browser',time.monotonic()+30)
    calls=[]
    b.call=lambda args,**kw:calls.append(args) or ''
    b.observe=lambda:{'snapshot':'fixture'}
    b.start()
    assert '--browser=chromium' in calls[0]
    config=json.loads(Path(next(x.split('=',1)[1] for x in calls[0] if x.startswith('--config='))).read_text())
    assert config['browser']['launchOptions']['headless'] is True


def test_no_publish_permission(tmp_path):
    with pytest.raises(Blocked): task(tmp_path,permissions=['read','edit','publish'])


def test_attachment_relative_to_task(tmp_path):
    (tmp_path/'cv.pdf').write_text('fixture')
    t = task(tmp_path,attachments=['cv.pdf'])
    validate_action(action('upload',[str(tmp_path/'cv.pdf')]),t)


@pytest.mark.parametrize('url',['file:///tmp/a','https://user:pass@example.com','javascript:alert(1)'])
def test_url_validation(url):
    with pytest.raises(Blocked): origin(url)


def test_env_removes_api_and_parent_markers(monkeypatch):
    for k in ['ANTHROPIC_API_KEY','OPENAI_API_KEY','ANTHROPIC_BASE_URL','CLAUDECODE']:
        monkeypatch.setenv(k,'secret')
        assert k not in child_env()


def test_subprocess_timeout(tmp_path):
    with pytest.raises(Blocked,match='timed out'):
        run_process(['sleep','3'],cwd=tmp_path,timeout=.02)


def test_adapters_no_tools_or_api_bare_mode(tmp_path):
    config={'worker':'cheap','decision':'strong'}
    model=Models('claude',config,tmp_path,time.monotonic()+60)
    argv,_=model.command('cheap','prompt',None,0)
    assert '--safe-mode' in argv and '--bare' not in argv
    assert argv[argv.index('--tools')+1] == ''
    model.backend='codex'
    argv,_=model.command('cheap','prompt',None,0)
    assert 'features.shell_tool=false' in argv
    assert 'forced_login_method="chatgpt"' in argv
    assert '--ignore-user-config' in argv


class FakeBrowser:
    answers = []
    checked = False
    closed = False
    check_pass = True
    def __init__(self,task,directory,deadline):
        self.count=0; self.last_image=None
        type(self).closed=False
    def start(self): return {'snapshot':'textbox e1'}
    def observe(self): return {'snapshot':'textbox e1'}
    def act(self,a):
        self.count+=1
        if self.answers:
            value=self.answers.pop(0)
            if isinstance(value,Exception): raise value
        return {'snapshot':'changed'}
    def checks(self):
        type(self).checked=True
        return [{'name':'check','passed':self.check_pass}]
    def close(self): type(self).closed=True


class FakeModels:
    answers=[]
    roles=[]
    def __init__(self,*a): self.calls=[]
    def ask(self,packet,strong=False,image=None):
        type(self).roles.append(strong)
        self.calls.append({'role':'decision' if strong else 'worker'})
        if not self.answers: raise Blocked('No more answers')
        return self.answers.pop(0)


def run_fake(tmp_path,answers,**kwargs):
    FakeModels.answers=answers; FakeModels.roles=[]
    return run_task(task(tmp_path,**kwargs),'codex',{},tmp_path/'run',
                    model_factory=FakeModels,browser_factory=FakeBrowser,auth=False)


def test_done_independently_checks_and_cleans(tmp_path):
    FakeBrowser.check_pass=True
    r=run_fake(tmp_path,[action(kind='done')])
    assert r['status']=='completed' and FakeBrowser.checked and FakeBrowser.closed
    assert (tmp_path/'run/result.json').exists()


def test_false_success_blocked(tmp_path):
    FakeBrowser.check_pass=False
    r=run_fake(tmp_path,[action(kind='done')]*4)
    assert r['status']=='blocked'
    assert any(FakeModels.roles)
    FakeBrowser.check_pass=True


def test_consult_routes_to_stronger_model(tmp_path):
    r=run_fake(tmp_path,[action(kind='consult'),action(),action(kind='done')])
    assert r['status']=='completed'
    assert FakeModels.roles==[False,True,False]
    assert r['consultations']==1


def test_consultations_bounded(tmp_path):
    r=run_fake(tmp_path,[action(kind='consult'),action(),action(kind='consult')],max_consultations=1)
    assert r['status']=='blocked' and r['consultations']==1


def test_uncertain_submit_is_never_repeated(tmp_path):
    FakeBrowser.check_pass=False
    FakeBrowser.answers=[RuntimeError('timeout')]
    a=action(); a['effect']='submit'
    r=run_fake(tmp_path,[a],permissions=['read','edit','submit'])
    assert r['status']=='blocked' and r['actions']==1
    assert 'uncertain' in r['reason']
    FakeBrowser.check_pass=True


def test_final_control_guard(tmp_path):
    b=Browser(task(tmp_path),tmp_path,time.monotonic()+60)
    b.current_url=lambda:'https://example.com'
    b.call=lambda *args,**kw: '{"text":"Submit application","type":"submit"}'
    with pytest.raises(Blocked,match='Final-action'):
        b.act(action('click',['e1']))


def test_budget_counts_observations(tmp_path):
    b=Browser(task(tmp_path,max_actions=1),tmp_path,time.monotonic()+60)
    b.count=1
    with pytest.raises(Blocked,match='limit'): b.call(['snapshot'])


def test_button_outside_form_is_not_submission(tmp_path):
    b=Browser(task(tmp_path),tmp_path,time.monotonic()+60)
    b.current_url=lambda:'https://example.com'
    b.call=lambda *args,**kw: '{"text":"Show details","type":"submit","form":false}'
    b.observe=lambda: {'snapshot':'details visible'}
    assert b.act(action('click',['e1']))['snapshot']=='details visible'


def test_codex_uses_final_message_not_commentary(tmp_path,monkeypatch):
    import models
    final=action(kind='done')
    events=[{'type':'item.completed','item':{'type':'agent_message','text':'Commentary, not JSON'}},
            {'type':'item.completed','item':{'type':'agent_message','text':json.dumps(final)}},
            {'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':20}}]
    monkeypatch.setattr(models,'run_process',lambda *a,**k:(0,'\n'.join(map(json.dumps,events)),''))
    m=Models('codex',{'worker':'cheap','decision':'strong'},tmp_path,time.monotonic()+30)
    assert m.ask({})==final


def test_cli_timeout_is_an_uncertain_command_error(tmp_path,monkeypatch):
    import browser
    b=Browser(task(tmp_path),tmp_path,time.monotonic()+60)
    def timeout(*a,**kw): raise Blocked('Command timed out or was interrupted; inspect saved evidence before retrying')
    monkeypatch.setattr(browser,'run_process',timeout)
    with pytest.raises(RuntimeError) as exc: b.call(['click','e1'])
    assert not isinstance(exc.value,Blocked)


def test_hidden_text_does_not_pass_check(tmp_path):
    b=Browser(task(tmp_path,checks=[{'name':'visible','kind':'text','selector':'#hidden','expected':'secret'}]),tmp_path,time.monotonic()+60)
    b.current_url=lambda:'https://example.com'
    def call(args,**kw):
        # The fixed probe must test visibility before returning text.
        assert 'checkVisibility' in args[-1]
        return 'null'
    b.call=call
    assert not b.checks()[0]['passed']


def test_pointer_guard_inspects_actual_target(tmp_path):
    b=Browser(task(tmp_path),tmp_path,time.monotonic()+60)
    b.pointer=(100,200)
    b.current_url=lambda:'https://example.com'
    def call(args,**kw):
        assert 'elementFromPoint(100,200)' in args[-1]
        return '{"text":"Publish","form":false}'
    b.call=call
    with pytest.raises(Blocked,match='publishing API'): b.act(action('mousedown',[]))


def test_external_action_can_only_run_once(tmp_path):
    t=task(tmp_path,permissions=['read','edit','submit'])
    b=Browser(t,tmp_path,time.monotonic()+60)
    b.current_url=lambda:'https://example.com'
    commands=[]
    def call(args,**kw):
        commands.append(args)
        return '{"text":"Submit application","type":"submit","form":true}'
    b.call=call
    b.observe=lambda:{'snapshot':'receipt pending'}
    a=action('click',['e1']);a['effect']='submit'
    b.act(a)
    with pytest.raises(Blocked,match='already attempted'): b.act(a)
    assert sum(args[0]=='click' for args in commands)==1


def test_bad_cheap_json_gets_one_bounded_consultation(tmp_path):
    import models
    class BadOnce(FakeModels):
        def ask(self,packet,strong=False,image=None):
            if not strong:
                raise models.ModelResponseError('Malformed model response')
            self.calls.append({'role':'decision'})
            return action(kind='done')
    r=run_task(task(tmp_path),'codex',{},tmp_path/'run',model_factory=BadOnce,browser_factory=FakeBrowser,auth=False)
    assert r['status']=='completed' and r['consultations']==1


def test_claude_adapter_reads_structured_output_and_usage(tmp_path,monkeypatch):
    import models
    final=action(kind='done')
    response={'structured_output':final,'is_error':False,'usage':{'input_tokens':2,'cache_read_input_tokens':8,'output_tokens':3}}
    monkeypatch.setattr(models,'run_process',lambda *a,**k:(0,json.dumps(response),''))
    m=Models('claude',{'worker':'haiku','decision':'sonnet'},tmp_path,time.monotonic()+30)
    assert m.ask({})==final
    assert m.calls[0]['usage']['cache_read_input_tokens']==8


def test_claude_image_input_contains_actual_image(tmp_path):
    image=tmp_path/'image.png';image.write_bytes(b'fixture-image')
    m=Models('claude',{'worker':'haiku','decision':'sonnet'},tmp_path,time.monotonic()+30)
    argv,stdin=m.command('sonnet','Inspect image',image,0)
    assert '--input-format' in argv
    message=json.loads(stdin)
    import base64
    assert base64.b64decode(message['message']['content'][1]['source']['data'])==b'fixture-image'


def test_claude_image_reply_is_read_from_stream_result(tmp_path,monkeypatch):
    import models
    image=tmp_path/'image.png';image.write_bytes(b'x')
    final=action(kind='done')
    stream='\n'.join(json.dumps(e) for e in [{'type':'system'},{'type':'result','structured_output':final,'is_error':False,'usage':{'output_tokens':1}}])
    seen=[]
    monkeypatch.setattr(models,'run_process',lambda argv,**k:seen.append(argv) or (0,stream,''))
    m=Models('claude',{'worker':'haiku','decision':'sonnet'},tmp_path,time.monotonic()+30)
    assert m.ask({},strong=True,image=image)==final
    assert seen[0][seen[0].index('--output-format')+1]=='stream-json'


def test_evaluation_outcome_not_sent_to_model(tmp_path):
    t=task(tmp_path,expected_status='blocked')
    assert 'expected_status' not in t


def test_timeout_retains_output(tmp_path):
    import sys
    with pytest.raises(Blocked) as caught:
        run_process([sys.executable, '-c',
                     'import sys,time; print("startup evidence",flush=True); '
                     'print("diagnostic",file=sys.stderr,flush=True); time.sleep(3)'],
                    cwd=tmp_path,timeout=.2)
    assert 'startup evidence' in caught.value.stdout
    assert 'diagnostic' in caught.value.stderr


def test_browser_uses_private_writable_runtime_and_cleans_it(tmp_path,monkeypatch):
    import browser
    import os
    original = dict(os.environ)
    b=Browser(task(tmp_path),tmp_path/'evidence',time.monotonic()+60)
    environments=[]
    def fake_process(*args,**kw):
        environments.append(kw['env'])
        for key in ('XDG_CACHE_HOME','XDG_RUNTIME_DIR'):
            directory=Path(kw['env'][key])
            assert directory.is_dir()
            (directory/'writable').write_text('ok')
        return 0,'',''
    monkeypatch.setattr(browser,'run_process',fake_process)
    b.call(['open','https://example.com'])
    b.started=True
    b.close()
    assert environments[0]==environments[1]
    assert not Path(environments[0]['XDG_RUNTIME_DIR']).exists()
    assert dict(os.environ)==original


def test_browser_timeout_evidence_survives_cleanup(tmp_path,monkeypatch):
    import browser
    from models import ProcessTimeout
    b=Browser(task(tmp_path),tmp_path/'evidence',time.monotonic()+60)
    def fake_process(argv,**kw):
        if 'attach' in argv:
            raise ProcessTimeout('timed out','partial startup','read-only filesystem')
        return 0,'detached',''
    monkeypatch.setattr(browser,'run_process',fake_process)
    b.started=True
    with pytest.raises(RuntimeError,match='attach'):
        b.call(['attach','--cdp=chrome'])
    b.close()
    assert 'partial startup' in (b.directory/'000.txt').read_text()
    assert 'read-only filesystem' in (b.directory/'000.txt').read_text()
    assert (b.directory/'001.txt').read_text()=='detached'


def test_model_startup_failure_is_not_malformed_json(tmp_path,monkeypatch):
    import models
    monkeypatch.setattr(models,'run_process',lambda *a,**k:
                        (1,'','Error: failed to initialize client: Read-only file system (os error 30)'))
    m=Models('codex',{'worker':'cheap','decision':'strong'},tmp_path,time.monotonic()+30)
    try:
        with pytest.raises(Blocked,match='[Rr]ead-only') as caught:
            m.ask({})
        assert not isinstance(caught.value,models.ModelResponseError)
        assert 'Read-only file system' in (tmp_path/'000.stderr').read_text()
    finally:
        m.close()


def test_codex_runtime_does_not_copy_auth_to_evidence(tmp_path,monkeypatch):
    import models
    import os
    source=tmp_path/'source';source.mkdir()
    (source/'auth.json').write_text('fixture credential must never enter evidence')
    monkeypatch.setattr(models,'codex_home',lambda:source)
    original=dict(os.environ)
    captured=[]
    final=action(kind='done')
    def fake_process(argv,**kw):
        runtime=Path(kw['env']['CODEX_HOME'])
        captured.append(runtime)
        assert runtime!=source and not runtime.is_relative_to(tmp_path/'evidence')
        assert (runtime/'auth.json').is_symlink()
        assert (runtime/'auth.json').resolve()==source/'auth.json'
        (runtime/'installation_id').write_text('private runtime state')
        return 0,json.dumps({'type':'item.completed','item':{'type':'agent_message','text':json.dumps(final)}}),''
    monkeypatch.setattr(models,'run_process',fake_process)
    m=Models('codex',{'worker':'cheap','decision':'strong'},tmp_path/'evidence',time.monotonic()+30)
    try:
        assert m.ask({})==final
        assert m.ask({})==final
        assert captured[0]==captured[1]
    finally:
        m.close()
    assert not captured[0].exists()
    assert dict(os.environ)==original
    assert (source/'auth.json').read_text()=='fixture credential must never enter evidence'
    assert not list((tmp_path/'evidence').rglob('auth.json'))


def test_model_timeout_keeps_logs_and_releases_runtime(tmp_path,monkeypatch):
    import models
    runtime=[]
    def timeout(*args,**kw):
        runtime.append(Path(kw['env']['CODEX_HOME']))
        raise models.ProcessTimeout('timed out after 1s','partial model output','model startup diagnostic')
    monkeypatch.setattr(models,'run_process',timeout)
    m=Models('codex',{'worker':'cheap','decision':'strong'},tmp_path,time.monotonic()+30)
    try:
        with pytest.raises(Blocked,match='models/000.stderr'):
            m.ask({})
        assert (tmp_path/'000.stdout').read_text()=='partial model output'
        assert (tmp_path/'000.stderr').read_text()=='model startup diagnostic'
    finally:
        m.close()
    assert not runtime[0].exists()


def test_stalled_tab_title_is_bounded_without_changing_page(tmp_path):
    import subprocess
    helper=Path(__file__).parents[1]/'page_init.cjs'
    script=r'''
const assert = require('node:assert/strict');
const { default: initialize } = require(process.argv[1]);
(async () => {
  let calls=0, resolveTitle;
  const pending=new Promise(resolve => {resolveTitle=resolve;});
  const page={title(){calls++;return pending;},url(){return 'https://example.com';}};
  const originalUrl=page.url;
  await initialize({page});
  const start=Date.now();
  assert.equal(await page.title(), '(title unavailable)');
  assert.ok(Date.now()-start < 4000);
  assert.equal(await page.title(), '(title unavailable)');
  assert.equal(calls,1,'Repeated snapshots must share the outstanding lookup');
  resolveTitle('Real title');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(await page.title(),'Real title');
  assert.equal(page.url,originalUrl);
})().catch(error => {console.error(error);process.exitCode=1;});
'''
    result=subprocess.run(['node','-e',script,str(helper)],capture_output=True,text=True,timeout=8)
    assert result.returncode==0,result.stderr


def test_title_lookup_preserves_updates_and_handles_failure(tmp_path):
    import subprocess
    helper=Path(__file__).parents[1]/'page_init.cjs'
    script=r'''
const assert = require('node:assert/strict');
const { default: initialize } = require(process.argv[1]);
(async () => {
  let title='First';
  const page={async title(){if(title===null) throw new Error('Context gone');return title;}};
  await initialize({page});
  assert.equal(await page.title(),'First');
  title='Second';
  assert.equal(await page.title(),'Second');
  title=null;
  assert.equal(await page.title(),'(title unavailable)');
  title='Recovered';
  assert.equal(await page.title(),'Recovered');
})().catch(error => {console.error(error);process.exitCode=1;});
'''
    result=subprocess.run(['node','-e',script,str(helper)],capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr


def test_claude_nonzero_quota_error_is_actionable(tmp_path,monkeypatch):
    import models
    monkeypatch.setattr(models,'run_process',lambda *a,**k:(1,json.dumps({'is_error':True,'result':'Weekly limit reached; resets tomorrow'}),''))
    m=Models('claude',{'worker':'sonnet'},tmp_path,time.monotonic()+30)
    with pytest.raises(Blocked,match='Weekly limit reached; resets tomorrow'):
        m.ask({})
    assert len(m.calls)==1


@pytest.mark.parametrize('key,value',[('attach','chrome'),('session',{'name':'bw-x','directory':'/tmp'}),('profile_directory','Default')])
def test_attach_modes_are_rejected_clearly(tmp_path,key,value):
    with pytest.raises(Blocked,match='not supported'):
        task(tmp_path,**{key:value})


def test_browse_task_matches_task_file_shape(tmp_path):
    t=browse_task('https://example.com/a','Find the price',read_only=True,max_steps=7)
    assert t['objective']=='Find the price' and t['permissions']==['read']
    assert t['max_actions']==7 and t['allowed_origins']==['https://example.com']
    assert browse_task('https://example.com','x')['permissions']==['read','edit']
    with pytest.raises(Blocked): browse_task('https://example.com','x',max_steps=0)
    with pytest.raises(Blocked): browse_task('file:///etc/passwd','x')


def test_browse_read_only_blocks_edits(tmp_path):
    t=browse_task('https://example.com','Look',read_only=True)
    with pytest.raises(Blocked): validate_action(action(),t)


class UsageModels(FakeModels):
    def ask(self,*a,**k):
        r=super().ask(*a,**k)
        self.calls[-1].update(model='m',usage={'input_tokens':1,'output_tokens':1})
        return r


def run_main(tmp_path,monkeypatch,capsys,argv):
    import sys
    config=tmp_path/'config.json'
    config.write_text(json.dumps({'codex':{'worker':'w','decision':'d'}}))
    seen={}
    def fake_run(task,backend,cfg,out,mode='mixed',**kw):
        seen.update(task=task,backend=backend,out=out,mode=mode)
        return run_task(task,backend,cfg,out,mode,model_factory=UsageModels,browser_factory=FakeBrowser,auth=False)
    monkeypatch.setattr(browser_worker,'run_task',fake_run)
    monkeypatch.setattr(sys,'argv',['bw']+argv+['--config',str(config)])
    code=browser_worker.main()
    return code,json.loads(capsys.readouterr().out),seen


def test_browse_command_runs_with_fakes(tmp_path,monkeypatch,capsys):
    FakeModels.answers=[dict(kind='done',command='',args=[],effect='read',reason='seen',memory='')]
    FakeModels.roles=[]
    FakeBrowser.check_pass=True
    out=tmp_path/'out'
    code,result,seen=run_main(tmp_path,monkeypatch,capsys,
        ['browse','https://example.com','--goal','Read heading','--backend','codex','--read-only','--max-steps','5','--out',str(out)])
    assert seen['task']['permissions']==['read'] and seen['task']['max_actions']==5
    assert seen['out']==out and seen['backend']=='codex'
    assert code==0 and result['status']=='completed'
    assert (out/'result.json').is_file()


def test_browse_requires_setup(tmp_path,monkeypatch,capsys):
    import sys
    monkeypatch.setattr(sys,'argv',['bw','browse','https://example.com','--goal','x','--backend','claude','--config',str(tmp_path/'none.json')])
    assert browser_worker.main()==2
    assert 'setup' in json.loads(capsys.readouterr().out)['reason']


def test_env_overrides_models_over_config(tmp_path,monkeypatch):
    import models
    seen=[]
    monkeypatch.setattr(models,'run_process',lambda argv,**k:seen.append(argv) or (1,'',''))
    m=Models('claude',{'worker':'cfgw','decision':'cfgd'},tmp_path,time.monotonic()+30)
    monkeypatch.setenv('BROWSER_WORKER_MODEL','envw')
    monkeypatch.setenv('BROWSER_DECISION_MODEL','envd')
    for strong in (False,True):
        with pytest.raises(Blocked): m.ask({},strong=strong)
    assert [m_['model'] for m_ in m.calls]==['envw','envd']
    monkeypatch.delenv('BROWSER_WORKER_MODEL')
    with pytest.raises(Blocked): m.ask({})
    assert m.calls[-1]['model']=='cfgw'
