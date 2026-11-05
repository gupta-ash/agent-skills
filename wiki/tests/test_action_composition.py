"""Source-only Controller/Store/loopback consumer definitions, never native proof.

The small local checkpoint is explicitly invented non-native accepted test data.
It uses the real PublicationManager before the real optional-action callback.
No production gate is bypassed or registered; no account/credentials are used.
"""
import os
import unittest
from unittest.mock import patch

import test_workflows as fixtures


class ActionCompositionTests(unittest.TestCase):
    def run_case(self, code):
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC", "PSMODULEPATH", "TEMP", "TMP", "TMPDIR"}}
        with patch.dict(os.environ, environment, clear=True):
            fixtures.WorkflowTests.run_case(self, "GRAPH_SOURCE = " + repr(GRAPH_SOURCE) + "\n" + ACTION_SETUP + code)

    def test_disabled_graph_reports_disabled_without_helpers_credentials_or_effects(self):
        self.run_case(r'''
configure(False)
with patch.object(actions,'_helpers',side_effect=AssertionError('Disabled action imported helpers')):
    outcome=controller().run(req)
assert outcome.status=='done',outcome.error
report=action_report(outcome)
assert report['status']=='disabled' and report['providers'][0]['status']=='disabled'
assert report['providers'][0]['results']==[]
assert knowledge.read_bytes()==knowledge_body
assert not events(outcome.run_id,'external_effect_authorized')
assert not events(outcome.run_id,'clickup_batch')
''')

    def test_postpublication_prerequisite_failure_is_public_and_keeps_knowledge(self):
        self.run_case(r'''
configure(True)
# The real helper refuses absent routing configuration before inspecting auth or
# making an API request. Knowledge/audit publication has already completed.
assert not os.environ.get('CLICKUP_API_TOKEN')
outcome=controller().run(req)
assert outcome.status=='done' and outcome.error is None,(outcome.status,outcome.error)
report=action_report(outcome)
provider=report['providers'][0]
assert report['status']=='failed' and report['knowledge_accepted']
assert provider['provider']=='clickup' and provider['status']=='failed'
assert provider['error']['code']=='config_error' and provider['knowledge_accepted']
assert provider['operation_id']=='clickup:'+outcome.run_id
assert knowledge.read_bytes()==knowledge_body
assert publication_precedes_action(outcome.run_id)
assert len(events(outcome.run_id,'external_effect_observed'))==1
with patch.object(actions,'_helpers',side_effect=AssertionError('Settled failed action was replayed')):
    collected=controller().resume(ROOT/'project',outcome.run_id)
assert collected.status=='done' and action_report(collected)==report
assert len(events(outcome.run_id,'external_effect_authorized'))==1
assert not events(outcome.run_id,'clickup_intent')
''')

    def test_public_provider_success_and_dedup_follow_observed_publication(self):
        self.run_case(r'''
configure(True,tasks=TASKS+[dict(TASKS[0],name='Second action',url='https://source.invalid/second')])
server=Loopback()
server.tasks.append({'id':'existing','name':'Existing synthetic task','description':'Source: https://source.invalid/second','list':{'id':ROUTES['ai']}})
try:
    with loopback_client(server):
        outcome=controller().run(req)
    assert outcome.status=='done',outcome.error
    provider=action_report(outcome)['providers'][0]
    assert provider['status']=='completed' and provider['knowledge_accepted']
    assert [result['status'] for result in provider['results']]==['created','dedup']
    assert [result['task_id'] for result in provider['results']]==['task-1','existing']
    assert len(server.posts)==1 and server.witnesses==[True]
    assert server.posts[0]['body']['priority']==4
    assert server.posts[0]['body']['tags']==['wiki','tech']
    assert knowledge.read_bytes()==knowledge_body and publication_precedes_action(outcome.run_id)
    with patch.object(actions,'_helpers',side_effect=AssertionError('Settled action inspected helpers')):
        collected=controller().resume(ROOT/'project',outcome.run_id)
    assert action_report(collected)==action_report(outcome) and len(server.posts)==1
finally:
    server.close()
''')

    def test_partial_batch_failure_publicly_preserves_first_provider_receipt(self):
        self.run_case(r'''
configure(True,tasks=TASKS+[dict(TASKS[0],name='Ambiguous second',url='https://source.invalid/second')])
server=Loopback()
for task_id in ('duplicate-one','duplicate-two'):
    server.tasks.append({'id':task_id,'name':task_id,'description':'Source: https://source.invalid/second','list':{'id':ROUTES['ai']}})
try:
    with loopback_client(server):
        outcome=controller().run(req)
    assert outcome.status=='done',outcome.error
    report=action_report(outcome);provider=report['providers'][0]
    assert report['status']=='failed' and provider['status']=='failed'
    assert provider['error']['code']=='ambiguous_tasks'
    assert len(provider['results'])==1 and provider['results'][0]['task_id']=='task-1'
    assert len(server.posts)==1 and server.witnesses==[True]
    assert len(events(outcome.run_id,'clickup_receipt'))==1
    assert knowledge.read_bytes()==knowledge_body
finally:
    server.close()
''')

    def test_unknown_response_is_publicly_blocked_then_reconciled_without_second_post(self):
        self.run_case(r'''
configure(True)
server=Loopback();server.drop_response=True
try:
    with loopback_client(server):
        first=controller().run(req)
        assert first.status=='blocked' and first.error['code']=='EXTERNAL_EFFECT_UNRESOLVED',first.error
        provider=first.error['details']['result']
        assert provider['provider']=='clickup' and provider['status']=='blocked' and provider['knowledge_accepted']
        assert provider['error']['code']=='ACTION_OUTCOME_UNRESOLVED'
        assert not any(ref['name']=='action-report.json' for ref in first.outputs)
        assert knowledge.read_bytes()==knowledge_body and len(server.posts)==1
        resumed=controller().resume(ROOT/'project',first.run_id)
    assert resumed.status=='done',resumed.error
    provider=action_report(resumed)['providers'][0]
    assert provider['status']=='completed' and provider['results'][0]['status']=='reconciled'
    assert provider['results'][0]['task_id']=='task-1'
    assert len(server.posts)==1 and knowledge.read_bytes()==knowledge_body
    assert len(events(first.run_id,'clickup_intent'))==1
    assert len(events(first.run_id,'external_effect_authorized'))==1
finally:
    server.close()
''')

    def test_lost_second_response_retains_first_receipt_and_reconciles_only_second(self):
        self.run_case(r'''
configure(True,tasks=TASKS+[dict(TASKS[0],name='Second wire-loss action',url='https://source.invalid/second')])
server=Loopback()
class DropOnlySecondResponse:
    def __bool__(self): return len(server.posts)==2
server.drop_response=DropOnlySecondResponse()
try:
    with loopback_client(server):
        first=controller().run(req)
        assert first.status=='blocked' and first.error['code']=='EXTERNAL_EFFECT_UNRESOLVED',first.error
        blocked=first.error['details']['result']
        assert [item['task_id'] for item in blocked['results']]==['task-1'],blocked
        assert knowledge.read_bytes()==knowledge_body and len(server.posts)==2
        resumed=controller().resume(ROOT/'project',first.run_id)
    assert resumed.status=='done',resumed.error
    report=action_report(resumed)
    assert report['status']=='completed' and report['knowledge_accepted']
    assert [(item['status'],item['task_id']) for item in report['providers'][0]['results']]==[('created','task-1'),('reconciled','task-2')]
    assert len(server.posts)==2 and knowledge.read_bytes()==knowledge_body
    assert len(events(first.run_id,'clickup_intent'))==2
    assert len(events(first.run_id,'external_effect_authorized'))==1
    assert publication_precedes_action(first.run_id)
finally:
    server.close()
''')

    def test_unknown_without_exact_match_remains_blocked_and_never_reposts(self):
        self.run_case(r'''
configure(True)
server=Loopback();server.post_status=503
try:
    with loopback_client(server):
        first=controller().run(req)
        assert first.status=='blocked' and len(server.posts)==1,first.error
        server.post_status=200
        resumed=controller().resume(ROOT/'project',first.run_id)
    assert resumed.status=='blocked' and resumed.error['code']=='EXTERNAL_EFFECT_UNRESOLVED',resumed.error
    assert resumed.error['details']['result']['error']['code']=='ACTION_OUTCOME_UNRESOLVED'
    assert len(server.posts)==1 and not server.tasks
    assert knowledge.read_bytes()==knowledge_body
finally:
    server.close()
''')

    def test_effect_callback_refuses_unpublished_or_changed_checkpoint_before_helpers(self):
        self.run_case(r'''
configure(True)
operation={'provider':'clickup','operation_id':'clickup:synthetic-run','tasks':TASKS}
class ForbiddenStore:
    def __getattribute__(self,name): raise AssertionError('Unpublished callback touched Store')
for present in (False,True):
    if present:
        knowledge.parent.mkdir(parents=True,exist_ok=True);knowledge.write_bytes(b'Conflicting unaccepted bytes')
    c=context('L7.actions','actions',data={'accepted_checkpoint':[{'path':str(knowledge),'sha256':sha256(knowledge_body).hexdigest()}]})
    c=replace(c,request=req)
    with patch.object(actions,'_helpers',side_effect=AssertionError('Unpublished callback inspected helpers')):
        try: w.execute_effect(c,ForbiddenStore(),operation)
        except w.DomainError as exc: assert exc.code=='KNOWLEDGE_NOT_PUBLISHED'
        else: raise AssertionError('Provider submitted before real publication')
''')


GRAPH_SOURCE = r'''
"""Private non-native action graph; not an installed route or acceptance gate."""
from pathlib import Path
from hashlib import sha256
from _workflow.controller import WorkflowSpec,StepSpec
from wiki.runtime import workflows as w
CONFIG={}

def checkpoint(context):
    body=CONFIG['knowledge_body'].encode('utf-8')
    target=Path(CONFIG['knowledge'])
    effect=w._candidate_effect(context,target,body,'non-native-knowledge.md',acceptance={'kind':'non_native_test_checkpoint'})
    return w._local(context,{'publications':[effect],
        'accepted_checkpoint':[{'path':str(target),'sha256':sha256(body).hexdigest()}]}, {'non-native-knowledge.md':body})

def prepare_actions(context):
    if not context.data['options']['clickup']:
        return w._actions_prepare_handler(context)
    # A local fixture replaces only model-generated action extraction. Real
    # domain authorization, provider execution and result reporting are retained.
    for ref in context.data['accepted_checkpoint']:
        assert Path(ref['path']).is_file() and w._hash(ref['path'])==ref['sha256']
    return w._local(context,{}, {'actions.json':{'tasks':CONFIG['tasks']}})

def build(request):
    steps=(StepSpec('checkpoint',(),'publish',checkpoint),
        w._step('L7.audit',('checkpoint',),'audit',kind='publish'),
        StepSpec('L7.actions.prepare',('L7.audit',),'local',prepare_actions),
        w._step('L7.actions',('L7.actions.prepare',),'actions',kind='publish'),
        w._step('L7.actions.report',('L7.actions',),'actions_report'))
    data={'vault_root':CONFIG['vault_root'],'options':w.parse_arguments(request),
          'attempted_at':'2026-10-17T04:00:00Z','published_outputs':[]}
    return WorkflowSpec('wiki-ingest',steps,metadata={'data':data},
        effect_handler=w.execute_effect,publication_guard=w.publication_guard)
'''


ACTION_SETUP = r'''
import os
import sqlite3
import socket
from contextlib import closing,contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit,parse_qs
from unittest.mock import patch
from _workflow.controller import Controller
from wiki.runtime import actions
fixture_path=ROOT/'non_native_action_graph.py';fixture_path.write_text(GRAPH_SOURCE,encoding='utf-8')
graph=w._module(fixture_path,'_non_native_action_graph')
knowledge=VAULT/'wiki/accepted-fixture.md'
knowledge_body=b'Invented accepted checkpoint for optional-action composition only.\n'
TASKS=[{'topic':'tech/ai/models','name':'Compare the synthetic method','url':'https://source.invalid/article',
        'description':'Only after condition Q; retain exact cost20.'}]
ROUTES={key:'synthetic-'+key for key in ('openclaw','claude','ai','tech','trading','health','default')}
routes_path=ROOT/'clickup-routes.json'
routes_path.write_text(json.dumps(ROUTES),encoding='utf-8')

def configure(enabled,tasks=None):
    global req
    arguments=['invented source','--title','Synthetic','--topic','alpha','--domain','science','--vault',str(VAULT)]
    if enabled: arguments.append('--clickup')
    req=request('wiki-ingest',*arguments)
    graph.CONFIG={'vault_root':str(VAULT),'knowledge':str(knowledge),
                  'knowledge_body':knowledge_body.decode(),'tasks':tasks if tasks is not None else TASKS}

def controller():
    def forbidden_native(**kwargs): raise AssertionError('Composition fixture must never dispatch native work')
    return Controller(graph.build,skills_root=SKILLS,adapter_factory=forbidden_native)

def action_report(outcome):
    refs=[ref for ref in outcome.outputs if ref['name']=='action-report.json']
    assert len(refs)==1,[(ref['step_id'],ref['name']) for ref in outcome.outputs]
    return json.loads(Path(refs[0]['path']).read_text(encoding='utf-8'))

def events(run_id,kind):
    with closing(sqlite3.connect(ROOT/'project/state.db')) as connection:
        return [json.loads(row[0]) for row in connection.execute('SELECT payload_json FROM wf_events WHERE run_id=? AND event_type=? ORDER BY event_id',(run_id,kind))]

def publication_precedes_action(run_id):
    with closing(sqlite3.connect(ROOT/'project/state.db')) as connection:
        action=connection.execute("SELECT MIN(event_id) FROM wf_events WHERE run_id=? AND event_type='external_effect_authorized'",(run_id,)).fetchone()[0]
        audit=connection.execute("SELECT MAX(event_id) FROM wf_events WHERE run_id=? AND event_type='item_completed' AND json_extract(payload_json,'$.step_id')='L7.audit'",(run_id,)).fetchone()[0]
        rows=connection.execute('SELECT status FROM wf_publications WHERE run_id=? AND target=?',(run_id,str(knowledge.resolve()))).fetchall()
    return bool(action and audit and audit<action and rows and knowledge.read_bytes()==knowledge_body)

class Loopback:
    def __init__(self):
        self.posts=[];self.gets=[];self.tasks=[];self.witnesses=[]
        self.drop_response=False;self.post_status=200
        state=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args): pass
            def send_json(self,status,value):
                body=json.dumps(value).encode();self.send_response(status)
                self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(body)))
                self.end_headers();self.wfile.write(body)
            def do_GET(self):
                parsed=urlsplit(self.path);state.gets.append(self.path)
                list_id=parsed.path.strip('/').split('/')[-2]
                tasks=[task for task in state.tasks if task['list']['id']==list_id]
                page=int(parse_qs(parsed.query).get('page',[0])[0])
                self.send_json(200,{'tasks':tasks[page*100:(page+1)*100],'last_page':(page+1)*100>=len(tasks)})
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                list_id=self.path.strip('/').split('/')[-2]
                with closing(sqlite3.connect(ROOT/'project/state.db')) as connection:
                    intents=connection.execute("SELECT COUNT(*) FROM wf_events WHERE event_type='clickup_intent'").fetchone()[0]
                    published=connection.execute('SELECT COUNT(*) FROM wf_publications WHERE target=?',(str(knowledge),)).fetchone()[0]
                state.witnesses.append(bool(intents and published and knowledge.read_bytes()==knowledge_body))
                state.posts.append({'path':self.path,'body':body})
                if state.post_status!=200:
                    self.send_json(state.post_status,{'err':'Synthetic provider refusal'});return
                task={**body,'id':'task-'+str(len(state.posts)),'url':'https://tasks.invalid/created',
                      'list':{'id':list_id},'status':{'status':'open'}}
                state.tasks.append(task)
                if state.drop_response:
                    self.close_connection=True;self.connection.shutdown(socket.SHUT_RDWR);self.connection.close();return
                self.send_json(200,task)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)+'/api/v2'
    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join()

@contextmanager
def loopback_client(server):
    helper=actions._helpers();original=helper.ClickUpClient
    factory=lambda: original(api_token='synthetic-loopback-token',team_id='synthetic-team',timeout=2,api_base=server.base)
    with patch.dict(os.environ,{'CLICKUP_ROUTES_FILE':str(routes_path)}),patch.object(actions,'_helpers',return_value=helper),patch.object(helper,'ClickUpClient',side_effect=factory):
        yield
'''


if __name__ == '__main__':
    unittest.main()
