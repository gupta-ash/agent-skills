"""Source-only failing-before consumer definitions; no native/model execution.

The review-shaped inputs below are explicitly non-native unit data, not receipts
or semantic qualification. Fixtures use real handlers and publication/store APIs.
"""
import os
import unittest
from unittest.mock import patch

import test_workflows as fixtures

# A finite protocol peer is local to this ordinary consumer test. It is not a
# native qualification receipt, campaign driver or installed runtime extension.
PROTOCOL_SETUP = r'''
import sqlite3

class _ProtocolFixture:
    def __init__(self, owner, *, context, task, backend, owner_epoch, recovery=False):
        self.owner,self.context,self.task=owner,context,task
        self.epoch=owner_epoch
        self.binding={'backend':backend,'child_id':'non-native-'+task.attempt_id,'job_id':'fixture-'+task.attempt_id}
        self.queue=[];self.sequence=0
        owner.peers.append(self)

    def frame(self, request_id, status, payload, error=None):
        return {'request_id':request_id,'run_id':self.task.run_id,'attempt_id':self.task.attempt_id,
                'owner_epoch':self.epoch,'status':status,'payload':payload,'error':error}

    def send(self, operation, payload, *, request_id=None):
        self.sequence+=1;request_id=request_id or str(self.sequence)
        self.owner.operations.append((self.task.attempt_id,operation))
        if operation=='prepare':
            self.queue.append(self.frame(request_id,'prepared',{}))
        elif operation=='launch_with_binding_barrier':
            self.queue.append(self.frame(request_id,'binding_required',{'binding':self.binding}))
        elif operation=='binding_ack':
            with sqlite3.connect((self.context.request.project_root/'state.db').as_uri()+'?mode=ro',uri=True) as db:
                binding,epoch=db.execute('SELECT a.binding_json,r.owner_epoch FROM wf_attempts a JOIN wf_runs r USING(run_id) WHERE attempt_id=?',(self.task.attempt_id,)).fetchone()
            assert json.loads(binding)==self.binding and epoch==self.epoch
            assert payload=={'committed':True,'binding':self.binding}
            self.queue.append(self.terminal(request_id))
        elif operation=='cancel':
            self.queue.append(self.frame(request_id,'cancelled',{'locally_settled':True,'binding':self.binding,'non_native_fixture':True}))
        else:
            raise AssertionError('Unexpected fixture protocol operation: '+operation)
        return request_id

    def terminal(self, request_id, *, failed=False):
        if self.task.duty=='review':
            return self.frame(request_id,'completed',{'locally_settled':True,'binding':self.binding,
                'native':{'process_exit_code':2},'result':{'status':'reviewed','verdict':'needs-attention',
                'loop_should_continue':False,'review_conforming':True,'non_native_fixture':True,'native_qualification':False}})
        artifacts,verification=self.owner.outputs(self.context,self.task)
        value={'schema_version':1,**{key:getattr(self.task,key) for key in ('run_id','step_id','item_id','attempt_id')},
               'status':'completed','verification':verification,'artifacts':artifacts,'task_sha256':self.task.semantic_sha256,
               'evidence':[],'decision':None,'error':None}
        return self.frame(request_id,'completed',{'locally_settled':True,'binding':self.binding,
            'native_result':{'source':'caller','mode':'strict','status':'valid','data':value},
            'native_settlement':{'status':'completed','end_time':1},'non_native_fixture':True})

    def poll(self, timeout=0):
        return self.queue.pop(0) if self.queue else None

    def close(self):
        return {'returncode':0,'process_settled':True,'graceful_eof_exit':True,
                'shutdown_actions':['non-native-fixture-closed'],'non_native_fixture':True}
'''


class MaintenanceCompositionTests(unittest.TestCase):
    def run_case(self, code):
        # The existing subprocess fixture assigns synthetic homes before imports.
        environment = {key: value for key, value in os.environ.items()
                       if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "PATHEXT", "COMSPEC", "PSMODULEPATH", "TEMP", "TMP", "TMPDIR"}}
        with patch.dict(os.environ, environment, clear=True):
            fixtures.WorkflowTests.run_case(self, PUBLICATION_SETUP + code)

    def test_delivered_fail_on_subthreshold_weakened_claim_blocks_page_and_audit_refresh(self):
        self.run_case(r'''
path=note('wiki/alpha.md','Five fixed synthetic claims.\n')
p=page(5);p.update(path=str(path),sha256=w._hash(path))
rows={}
for index,claim in enumerate(p['claims']):
    prefix='CC2.'+claim['claim_id']
    evaluation={'claim_id':claim['claim_id'],'claim_sha256':'b'*64,'raw_ref':'unit-raw',
                'raw_sha256':'c'*64,'claim_verdict':'WEAKENED' if index==4 else 'VERIFIED',
                'claim_severity':'MINOR' if index==4 else None,'evidence':'Synthetic Q span',
                'reason':'Retained typed qualification'}
    rows[prefix+'.evaluate']=[{'item_id':'root','data':{'claim_evaluation':evaluation}}]
    rows[prefix+'.review']=[{'item_id':'root','data':{
        'review_gate':{'accepted':index!=4,'tooling_failure':False},
        'native_review':{'non_native_unit_input':True,'status':'completed',
                         'verdict':'fail' if index==4 else 'approve',
                         'exitCode':4 if index==4 else 0,'loop_should_continue':False}}}]
c=context('CC2.p.report','page_report',policy={'page':p},data={'step_data':rows})
prepared=w.prepare_step(c);assert prepared.status=='local_result'
report=w._plain(prepared.local_result['data']['page_report'])
assert report['status']=='NEEDS_ATTENTION' and report['score']==.8,report
assert report['summary']['claims_weakened']==1
assert report['summary']['claims_review_integrity_escalated']==1
assert report['findings'][-1]['claim_verdict']=='WEAKENED'
assert report['findings'][-1]['reason']=='Retained typed qualification'
# A clean sibling must not hide the rejected review when the vault-wide audit runs.
clean=note('wiki/clean.md','A fixed clean synthetic claim.\n')
q={'item_id':'q','slug':'clean','path':str(clean),'sha256':w._hash(clean),'claims':[{'claim_id':'q-c001'}]}
clean_report=w.content_page_report(q,[{'claim_verdict':'VERIFIED'}])
rows={'CC2.p.report':[{'item_id':'root','data':{'page_report':report}}],
      'CC2.q.report':[{'item_id':'root','data':{'page_report':clean_report}}]}
options=w.parse_arguments(request('wiki-content-check','--vault',str(VAULT),'--all'))
aggregate=w.prepare_step(context('CC3.report','content_report',data={'pages':[p,q],'options':options,'step_data':rows}))
combined=w._plain(aggregate.local_result['data']['report'])
assert combined['verified']==1 and combined['review_integrity_escalations']==['p']
before={str(value):value.read_bytes() for value in (path,clean)}
audit_context=context('CC3.audit','audit',data={'report':combined,'attempted_at':'2026-10-17T04:00:00Z'})
audit_result=w.prepare_step(audit_context);assert audit_result.status=='local_result'
apply_prepared(audit_context,audit_result)
manifest=json.loads((VAULT/'raw/_manifest.json').read_text(encoding='utf-8'))
assert manifest['last_verified_at']=='2020-01-01T00:00:00Z'
record=next(iter(manifest['wiki_workflow']['audit_operations'].values()))
assert record['verified']==1 and not record['semantic_verification_refreshed']
assert record['review_integrity_escalations']==['p']
assert before=={str(value):value.read_bytes() for value in (path,clean)}
''')

    def test_drift_uses_declared_bytes_for_web_and_text_lf_for_document(self):
        self.run_case(r'''
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
original=b'<html>Alpha only after Q.\r\nCost20.</html>\r\n'
state={'body':original}
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        body=state['body'];self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
    def log_message(self,*args): pass
server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
thread=Thread(target=server.serve_forever,daemon=True);thread.start()
try:
    url='http://127.0.0.1:'+str(server.server_port)+'/source'
    local=ROOT/'source.txt';local.write_bytes(original)
    for origin,physical,kind,reference in [('web','articles','bytes',{'source_url':url}),
                                          ('document','documents','text-lf',{'source_document_local':str(local)})]:
        state['body']=original;local.write_bytes(original)
        source=note('raw/'+physical+'/'+origin+'.md','Archived wrapper differs from transport.\n',
                    {'type':'source','source_class':physical,'provenance_class':origin,
                     'content_hash':provenance.content_hash('Archived wrapper differs from transport.\n'),
                     **reference,**provenance.source_hash(original,kind)})
        c=context('L1.refetch','drift',data={'item':{**w._ref(source),'item_id':origin},'options':{'backfill_mode':False}})
        def compare():
            prepared=w.prepare_step(c);assert prepared.status=='local_result'
            value=prepared.local_result['data']['diff']
            assert value['source_available'],value
            assert value['source_hash']['source_hash_kind']==kind,value
            assert 'baseline_status' not in value,value
            return value['hash_match']
        assert compare() is True,(origin,'unchanged')
        state['body']=original.replace(b'\r\n',b'\n');local.write_bytes(state['body'])
        assert compare() is (kind=='text-lf'),(origin,'line endings')
        state['body']=original.replace(b'Cost20',b'Cost30');local.write_bytes(state['body'])
        assert compare() is False,(origin,'changed')
finally:
    server.shutdown();server.server_close();thread.join()
''')

    def test_transcript_v2_rechecks_its_new_body_not_v1_local_archive(self):
        self.run_case(r'''
a='Original transcript A requires condition Q and exact cost20.\n'
b='Replacement transcript B retains contradictory constraint R and exactly3 units.\n'
transcript=ROOT/'transcript.txt';transcript.write_text(b,encoding='utf-8')
archive=note('raw/videos/original.txt',a)
sid=provenance.source_id({'kind':'canonical-reference','id':'synthetic:transcript'})
logical='local:archive/course/'+sid+'/'+w._hash(archive)+'.txt'
manifest_path=VAULT/'raw/_manifest.json'
manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
manifest['wiki_workflow']['snapshots']={logical:{'source_id':sid,'path':archive.relative_to(VAULT).as_posix(),'sha256':w._hash(archive),'media_type':'text/plain'}}
manifest_path.write_text(json.dumps(manifest),encoding='utf-8')
v1=note('raw/videos/lesson.md',a,{'type':'source','source_class':'videos','provenance_class':'course',
    'lesson_url':'https://source.invalid/lesson','transcript_method':'supplied-file','transcript_source':str(transcript),
    'local_archive_ref':logical,'content_hash':provenance.content_hash(a),**provenance.source_hash(a.encode(),'text-lf')})
before=v1.read_bytes();item={**w._ref(v1),'item_id':'lesson'}
c=context('L1.refetch','drift',data={'item':item,'options':{'backfill_mode':False}})
diff=w._plain(w.prepare_step(c).local_result['data']['diff'])
assert diff['hash_match'] is False and diff['source_available'],diff
snapshot_context=context('L3.snapshots','snapshots',data={'item':{'diff':diff},'items':[item],'options':{'snapshot_on_drift':True}})
snapshot=w.prepare_step(snapshot_context);assert snapshot.status=='local_result'
apply_prepared(snapshot_context,snapshot)
v2=Path(snapshot.local_result['data']['snapshot_receipt']['path'])
fields,text=w._metadata(v2)
body=provenance.normalized_body(text)
assert body==b and fields['source_hash']==provenance.source_hash(b.encode(),'text-lf')['source_hash']
assert 'local_archive_ref' not in fields
again=context('L1.refetch-v2','drift',data={'item':{**w._ref(v2),'item_id':'lesson-v2'},'options':{'backfill_mode':False}})
record=w.prepare_step(again).local_result['data']['diff']
assert record['hash_match'] is True and record['levenshtein_delta']==0,record
assert v1.read_bytes()==before and archive.read_text(encoding='utf-8')==a
''')

    def test_returned_content_report_contains_missing_selector_and_unenrolled_audit_reason(self):
        self.run_case(r'''
from _workflow.controller import Controller
(VAULT/'.wiki-schema.md').write_text('---\nwiki_name: SyntheticWiki\nextends: canonical-schema.md\nextends_version: "3.0"\ndomains: [science]\ntopics: {science: [alpha]}\n---\n',encoding='utf-8')
before={p.relative_to(VAULT).as_posix():p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
def forbidden_native(**kwargs): raise AssertionError('Empty maintenance must not dispatch native work')
outcome=Controller(w.build_workflow,skills_root=SKILLS,adapter_factory=forbidden_native).run(request('wiki-content-check','--vault',str(VAULT),'--slugs','missing'))
assert outcome.status=='done',outcome.error
reports={ref['name']:json.loads(Path(ref['path']).read_text(encoding='utf-8')) for ref in outcome.outputs if ref['name'] in ('content-check-report.json','audit-result.json')}
assert reports['content-check-report.json']['selector_warnings']==[{'code':'SLUG_NOT_FOUND','slug':'missing'}]
assert reports['content-check-report.json']['audit_skipped']=='VAULT_CONTROLS_NOT_ENROLLED'
assert reports['audit-result.json']=={'audit_skipped':'VAULT_CONTROLS_NOT_ENROLLED','audit_receipts':[]}
assert before=={p.relative_to(VAULT).as_posix():p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
''')

    def test_returned_drift_report_contains_ambiguous_provenance_diagnostic(self):
        self.run_case(r'''
from _workflow.controller import Controller
source=note('raw/documents/ambiguous.md','Invented unknown subtype.\n',{'type':'source','source_class':'documents','content_hash':provenance.content_hash('Invented unknown subtype.\n')})
before=source.read_bytes()
def forbidden_native(**kwargs): raise AssertionError('Drift reporting is local-only')
outcome=Controller(w.build_workflow,skills_root=SKILLS,adapter_factory=forbidden_native).run(request('wiki-reverify','--vault',str(VAULT),'--freshness','all'))
assert outcome.status=='done',outcome.error
report=json.loads(next(Path(ref['path']).read_text(encoding='utf-8') for ref in outcome.outputs if ref['name']=='drift-report.json'))
assert report['selected']==0 and report['selection_diagnostics']==[{'code':'SOURCE_CLASS_UNKNOWN','path':str(source),'source_class':'documents'}]
assert source.read_bytes()==before
''')

    def test_full_wiki_no_review_aborts_remaining_claims_and_retains_exact_charges(self):
        self.run_case(PROTOCOL_SETUP + r'''
import sqlite3
from contextlib import closing
from _workflow.controller import Controller
facts=['Alpha starts only after Q.','The exact cost is20.','Constraint R forbids Alpha.']
raw=note('raw/documents/origin.md','\n'.join(facts)+'\n',{'type':'source','source_class':'documents','provenance_class':'document'})
source=note('wiki/alpha.md','\n'.join('[PARAPHRASE] '+fact+' ^[[[origin]]]' for fact in facts)+'\n',{'type':'compilation','domain':'science'})
before={str(path):(path.read_bytes(),path.stat().st_mtime_ns) for path in (source,raw)}
raw_ref=w._ref(raw)
class Peer(_ProtocolFixture):
    def terminal(self,request_id,*,failed=False):
        if self.task.duty=='review' and self.task.step_id.endswith('-c002.review'):
            return self.frame(request_id,'completed',{'locally_settled':True,'binding':self.binding,
                'native':{'process_exit_code':1},'result':{'status':'tooling-failure','verdict':None,
                'loop_should_continue':True,'review_conforming':False,'non_native_fixture':True,'native_qualification':False}})
        return super().terminal(request_id,failed=failed)
class Owner:
    variant='wiki-claim-no-review'
    def __init__(self): self.peers=[];self.operations=[]
    def __call__(self,**kwargs): return Peer(self,**kwargs)
    def outputs(self,c,task):
        claim=c.step.policy['claim']
        index=int(claim['claim_id'].rsplit('c',1)[1])-1
        value={'claim_id':claim['claim_id'],'claim_sha256':claim['claim_sha256'],
               'raw_ref':raw_ref['path'],'raw_sha256':raw_ref['sha256'],'claim_verdict':'VERIFIED',
               'claim_severity':None,'evidence':facts[index],'reason':'Explicitly synthetic exact-span evaluation'}
        output=task.outputs[0];path=Path(task.candidate_dir)/output.relative_path
        path.write_text(json.dumps(value),encoding='utf-8')
        return [{'name':output.name,'relative_path':output.relative_path,'sha256':w._hash(path),'media_type':output.media_type}],'PASS'
owner=Owner()
controller=Controller(w.build_workflow,skills_root=SKILLS,artifact_validator=w.validate_artifact,adapter_factory=owner)
outcome=controller.run(request('wiki-content-check','--vault',str(VAULT),'--slugs','alpha'))
assert outcome.status=='failed',outcome.error
report=json.loads(next(Path(ref['path']).read_text(encoding='utf-8') for ref in outcome.outputs if ref['name']=='content-check-report.json'))
assert report['fan_in_complete'] and report['summary']['tooling_failure']==1
page_report=report['pages'][0]
assert page_report['score'] is None and page_report['summary']['claims_verified'] is None
assert page_report['summary']['claims_not_reviewed']==2
assert len(page_report['findings'])==1 and page_report['findings'][0]['claim_verdict']=='VERIFIED'
with closing(sqlite3.connect(ROOT/'project/state.db')) as connection:
    charges=connection.execute('SELECT artifact_id,full_attempts,repair_rounds FROM wf_reviews ORDER BY artifact_id').fetchall()
assert [(full,repair) for _,full,repair in charges]==[(1,0),(1,0),(0,0)],charges
assert len(owner.peers)==4 and not any('-c003.' in peer.task.step_id for peer in owner.peers)
assert all(peer.task.duty in ('verify','review') for peer in owner.peers)
manifest=json.loads((VAULT/'raw/_manifest.json').read_text(encoding='utf-8'))
assert manifest['last_verified_at']=='2020-01-01T00:00:00Z'
audit=next(iter(manifest['wiki_workflow']['audit_operations'].values()))
assert not audit['semantic_verification_refreshed'] and audit['tooling_failure_count']==1
assert before=={str(path):(path.read_bytes(),path.stat().st_mtime_ns) for path in (source,raw)}
collected=controller.resume(ROOT/'project',outcome.run_id)
assert collected.status=='failed' and len(owner.peers)==4
with closing(sqlite3.connect(ROOT/'project/state.db')) as connection:
    assert connection.execute('SELECT artifact_id,full_attempts,repair_rounds FROM wf_reviews ORDER BY artifact_id').fetchall()==charges
''')


PUBLICATION_SETUP = r'''
from _workflow.store import Store
from _workflow.publication import PublicationManager
from _workflow.controller import Controller

def apply_prepared(c,prepared):
    store=Store(ROOT/'project');store.initialize()
    try:
        if store.read_run(c.run_id) is None:
            store.create_run(c.run_id,'non-native-maintenance-fixture','a'*64,{}, {},run_kind='inline')
        with store.acquire_owner(c.run_id):
            manager=PublicationManager(store)
            for effect in Controller(lambda request: None)._publication_intents(c,prepared.local_result['data']):
                w.publication_guard(c,store,effect)
                receipt=manager.publish_file(c.run_id,None,Path(effect['candidate']),Path(effect['target']),effect['expected_sha256'],effect['operation_id'],root=Path(effect['root']))
                assert receipt['status'] in ('applied','reconciled'),receipt
    finally:
        store.close()
'''


if __name__ == '__main__':
    unittest.main()
