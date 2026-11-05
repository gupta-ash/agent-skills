"""Real same-project Controller/Store/publication with actual wiki _local preparation.

The local graph is synthetic, not registered knowledge acceptance or native proof.
"""
import unittest
import test_workflows as fixtures


GRAPH_SOURCE = r'''
import json
from pathlib import Path
from wiki.runtime import workflows as w
from _workflow.controller import StepSpec, WorkflowSpec

PREPARATIONS = []

def prepare(context):
    origin, mode = context.data['origin'], context.data['mode']
    root = Path(context.data['vault_root'])
    PREPARATIONS.append((context.run_id, origin, mode))
    target = root/'wiki'/('one.md' if mode == 'protected' else origin+'.md')
    protected = mode != 'legacy-pending'
    content = ('---\nhuman_edited: true\n---\n' if protected else '') + 'Synthetic original '+origin+'\n'
    effects = [{'kind':'replace','candidate_path':str(context.candidate_dir/'candidate.md'),
        'target':str(target),'expected_sha256':w._hash(target) if target.exists() else None}]
    documents = {'candidate.md':content.encode('utf-8')}
    if mode not in ('protected','ambiguous'):
        manifest = root/'raw/_manifest.json'
        value = json.loads(manifest.read_bytes())
        value.setdefault('wiki_workflow',{}).setdefault('publication_identity_smoke',[]).append({
            'run_id':context.run_id,'source':w._plain(context.data['source_input']),'target':str(target)})
        documents['manifest.json'] = (json.dumps(value,sort_keys=True)+'\n').encode('utf-8')
        effects.append({'kind':'replace','candidate_path':str(context.candidate_dir/'manifest.json'),
            'target':str(manifest),'expected_sha256':w._hash(manifest)})
        explicit = root/'raw'/('explicit-'+origin+'.txt')
        documents['explicit.txt'] = ('Explicit source-owned '+origin+'\n').encode('utf-8')
        effects.append({'kind':'replace','candidate_path':str(context.candidate_dir/'explicit.txt'),
            'target':str(explicit),'expected_sha256':None,'operation_id':'controls:'+context.run_id+':identity-smoke'})
    if mode.startswith('legacy'):
        # Reproduce durable predecessor identities in a private fixture only.
        # Production never invents an alias for these previously committed IDs.
        for ordinal,effect in enumerate(effects[:2]):
            effect['operation_id']='checkpoint:main:'+str(ordinal)
    elif mode == 'ambiguous':
        effects[0]['operation_id']='checkpoint:main:0'
    return w._local(context,{'publications':effects},documents)

def build_workflow(request):
    origin = request.arguments[0]
    mode = request.arguments[1] if len(request.arguments)>1 else 'fresh'
    source = request.project_root.parent/('original-'+origin+'.txt')
    source_ref = w._ref(source)
    metadata = {'inputs':[source_ref], 'data':{'vault_root':str(request.project_root.parent/'vault'),
        'origin':origin,'mode':mode,'source_input':source_ref}}
    return declare(request,metadata)

def declare(request,metadata):
    return WorkflowSpec(request.workflow,(StepSpec('checkpoint',(),'publish',prepare),),
        metadata=metadata,publication_guard=w.publication_guard)

def restore_workflow(request,*,snapshot):
    return declare(request,w._plain(snapshot['metadata']))
'''


CASE_SETUP = r'''
import importlib.util
import sqlite3
import sys
from contextlib import closing
from _workflow.controller import Controller
from _workflow.publication import PublicationManager
from _workflow.store import Store
from unittest.mock import patch

source=ROOT/'publication_identity_graph.py'
source.write_text(GRAPH_SOURCE,encoding='utf-8')
spec=importlib.util.spec_from_file_location('publication_identity_graph',source)
graph=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=graph
spec.loader.exec_module(graph)
project=ROOT/'project'
for name in ('one','two','protected','ambiguous'):
    (ROOT/('original-'+name+'.txt')).write_text('Distinct immutable original '+name+'\n',encoding='utf-8')
controller=Controller(graph.build_workflow,workflow_restorer=graph.restore_workflow,skills_root=SKILLS)
def run(origin,mode='fresh'):
    return controller.run(request('wiki-ingest',origin,mode))
def rows(table,run_id=None):
    with closing(sqlite3.connect(project/'state.db')) as connection:
        connection.row_factory=sqlite3.Row
        where=' WHERE run_id=?' if run_id else ''
        return [dict(row) for row in connection.execute('SELECT * FROM '+table+where, (run_id,) if run_id else ())]
def durable(run_id):
    root=project/'docs/tmp/pipeline-runs'/run_id
    return ({str(path):path.read_bytes() for path in root.rglob('*') if path.is_file()},
        rows('wf_events',run_id),rows('wf_publications',run_id))
def witness(path):
    value=path.stat()
    return (path.read_bytes(),value.st_mtime_ns,value.st_size,value.st_dev,value.st_ino)
def assert_first_preserved(first,history,protected):
    assert durable(first.run_id)==history,'An earlier original ledger/snapshot/publication was rewritten'
    for path,before in protected.items(): assert witness(path)==before,'An independent first-run target/control changed'
def assert_receipts(outcome):
    publications=rows('wf_publications',outcome.run_id)
    validated=[json.loads(row['payload_json']) for row in rows('wf_events',outcome.run_id) if row['event_type']=='local_output_validated']
    assert len(validated)==1
    assert {row['operation_id'] for row in publications}=={effect['operation_id'] for effect in validated[0]['publications']}
    assert all(row['status']=='applied' and json.loads(row['receipt_json'])['run_id']==outcome.run_id for row in publications)
    explicit=[row for row in publications if Path(row['target']).name.startswith('explicit-')]
    assert len(explicit)==1 and explicit[0]['operation_id']=='controls:'+outcome.run_id+':identity-smoke'
    return publications
'''


FRESH_CASE = r'''
first=run('one')
assert first.status=='done',first.error
first_publications=assert_receipts(first)
first_target=VAULT/'wiki/one.md'
assert w.vault_controls.is_protected(first_target,resolved())
protected={path:witness(path) for path in (first_target,VAULT/'raw/explicit-one.txt',VAULT/'.wiki-schema.md',VAULT/'_hub.md')}
history=durable(first.run_id)
first_manifest=json.loads((VAULT/'raw/_manifest.json').read_bytes())
second=run('two')
assert second.status=='done',second.error
second_publications=assert_receipts(second)
assert first.run_id!=second.run_id
assert len(first_publications)==len(second_publications)==3
assert {row['operation_id'] for row in first_publications}.isdisjoint(row['operation_id'] for row in second_publications)
assert len(rows('wf_publications'))==6
assert_first_preserved(first,history,protected)
manifest=json.loads((VAULT/'raw/_manifest.json').read_bytes())
entries=manifest['wiki_workflow']['publication_identity_smoke']
assert len(entries)==2 and entries[0]==first_manifest['wiki_workflow']['publication_identity_smoke'][0]
assert [row['run_id'] for row in entries]==[first.run_id,second.run_id]
assert (VAULT/'wiki/two.md').is_file()
second_manifest=next(row for row in second_publications if row['target']==str(VAULT/'raw/_manifest.json'))
first_manifest_row=next(row for row in first_publications if row['target']==str(VAULT/'raw/_manifest.json'))
assert second_manifest['expected_sha256']==first_manifest_row['desired_sha256']
assert second_manifest['desired_sha256']==w._hash(VAULT/'raw/_manifest.json')
refused=run('protected','protected')
assert refused.status=='blocked' and refused.error['code']=='PROTECTED_TARGET',refused.error
assert not rows('wf_publications',refused.run_id)
assert_first_preserved(first,history,protected)
for original in (first,second):
    collected=controller.resume(project,original.run_id)
    assert collected.status=='done',collected.error
assert len(rows('wf_publications'))==6 and not rows('wf_attempts') and not rows('wf_reviews')
assert_first_preserved(first,history,protected)
SMOKE_EVIDENCE={'status':'passed','original_run_ids':[first.run_id,second.run_id],
    'publication_count':6,'publication_ids':[row['operation_id'] for row in rows('wf_publications')],
    'first_run_preserved':True,'first_run_protected_target_refused':refused.error['code'],
    'real_native_calls':0,'attempt_count':0,'review_count':0}
'''


LEGACY_CASE = r'''
first=run('one','legacy')
assert first.status=='done',first.error
assert_receipts(first)
assert {row['operation_id'] for row in rows('wf_publications',first.run_id)} >= {'checkpoint:main:0','checkpoint:main:1'}
protected={path:witness(path) for path in (VAULT/'wiki/one.md',VAULT/'raw/explicit-one.txt')}
history=durable(first.run_id)
second=run('two')
assert second.status=='done',second.error
assert_receipts(second)
assert_first_preserved(first,history,protected)
collision=run('ambiguous','ambiguous')
assert collision.status=='blocked' and collision.error['code']=='OPERATION_CONFLICT',collision.error
assert not (VAULT/'wiki/ambiguous.md').exists() and not rows('wf_publications',collision.run_id)
assert_first_preserved(first,history,protected)
assert len(rows('wf_publications'))==6 and not rows('wf_attempts') and not rows('wf_reviews')
'''


PENDING_CASE = r'''
perform=PublicationManager._perform
crashed=[]
def crash_after_first(manager,row,root,protected_check,recovering):
    receipt=perform(manager,row,root,protected_check,recovering=recovering)
    if not crashed:
        crashed.append(row['operation_id'])
        raise OSError(28,'Synthetic crash after original legacy publication receipt')
    return receipt
with patch.object(PublicationManager,'_perform',crash_after_first):
    first=run('one','legacy-pending')
assert first.status=='blocked' and first.error['code']=='OSError',first.error
assert len(rows('wf_publications',first.run_id))==1
old_events=rows('wf_events',first.run_id)
local=[row for row in old_events if row['event_type'] in ('local_result_prepared','local_output_validated')]
assert len(local)==2
retained=json.loads(next(row['payload_json'] for row in local if row['event_type']=='local_result_prepared'))
local_path=Path(retained['local_result_path'])
local_bytes=local_path.read_bytes()
first_target=VAULT/'wiki/one.md'
before=witness(first_target)
assert len(graph.PREPARATIONS)==1
collected=controller.resume(project,first.run_id)
assert collected.status=='done',collected.error
assert len(graph.PREPARATIONS)==1,'The stored original LOCAL authority was regenerated'
assert witness(first_target)==before and local_path.read_bytes()==local_bytes
current_events={row['event_id']:row for row in rows('wf_events',first.run_id)}
assert all(current_events[row['event_id']]==row for row in old_events)
assert_receipts(collected)
assert {row['operation_id'] for row in rows('wf_publications',first.run_id)} >= {'checkpoint:main:0','checkpoint:main:1'}
second=run('two')
assert second.status=='done',second.error
assert len(rows('wf_publications'))==6 and not rows('wf_attempts') and not rows('wf_reviews')
'''


class PublicationIdentityTests(unittest.TestCase):
    def run_case(self, code):
        fixtures.WorkflowTests.run_case(self,'GRAPH_SOURCE='+repr(GRAPH_SOURCE)+'\n'+CASE_SETUP+code)

    def test_two_originals_publish_actual_wiki_local_defaults_without_touching_protected_first_run(self):
        self.run_case(FRESH_CASE)

    def test_legacy_receipts_remain_exact_and_ambiguous_explicit_ids_refuse(self):
        self.run_case(LEGACY_CASE)

    def test_pending_legacy_local_authority_collects_without_regeneration_or_renaming(self):
        self.run_case(PENDING_CASE)


if __name__=='__main__':
    unittest.main()
