"""Exact extraction review subjects; simulated gates are not native evidence."""
from textwrap import dedent
import unittest
import test_workflows as support


FIXTURE = r'''
artifact_id='ingest:synthetic-run:extraction'
raws=[]
for name,body in [('ch01.md','Cover'),('ch02.md','Preface: one source only.'),
                  ('ch04.md','Alpha only after Q; R prevents it.'),('full-text.md','Cover\nPreface\nAlpha only after Q; R prevents it.')]:
    path=note('native/'+name,body+'\r\n',{'type':'source','status':'draft','last_verified':None})
    raws.append({**w._ref(path,name),'attempt_id':'original-extraction'})
members=list(raws)
for name in ('raw-note-map.json','capture-fidelity.json'):
    path=ROOT/name; path.write_text(json.dumps({'companion':name,'units':['ch01','ch02','ch04']}),encoding='utf-8')
    members.append(w._ref(path,name))
rows={'L1.extract':[{'item_id':'root','status':'done','data':{},'artifacts':members}],
      'capture.validate':[{'item_id':'root','status':'done','data':{'completed_at':1000},'artifacts':[]}],
      'capture.fidelity':[{'item_id':'root','status':'done','data':{'overall_verdict':'PASS','completed_at':2000},'artifacts':[]}]}
policy={'source_step':'L1.extract','artifact_id':artifact_id,'artifact_tier':'A','review_contract':'vault-raw'}
c=context('L1.review','review',policy=policy,data={'step_data':rows})
c=replace(c,request=request('wiki-ingest','synthetic-book','--vault',str(VAULT)))
originals={ref['path']:Path(ref['path']).read_bytes() for ref in members}

def accept(review):
    # Deliberately synthetic gate input, never a native receipt or qualification.
    rows['L1.review']=[{'item_id':'root','status':'done','artifacts':[],
                      'data':{'artifact_id':artifact_id,'review_gate':{'accepted':True},
                              'review':review,'completed_at':3000}}]
    lifecycle=context('capture.lifecycle','raw_lifecycle',data={'step_data':rows,'capture_structural_valid':True})
    return replace(lifecycle,request=c.request)
'''


class ExtractionReviewBundleTests(unittest.TestCase):
    run_case = support.WorkflowTests.run_case

    def test_first_only_approval_with_all_inputs_and_full_scope_prose_cannot_project_siblings(self):
        self.run_case(FIXTURE + dedent(r'''
            review=w._prepare_review(c,raws[0],members,w._EXTRACTION_REVIEW_SCOPE,
                                     artifact_id=artifact_id,tier='A',contract='vault-raw')
            lifecycle=accept(review)
            assert all(any(value['path']==ref['path'] for value in review['input_refs']) for ref in members)
            try: w._raw_lifecycle_handler(lifecycle)
            except w.DomainError as exc: assert exc.code=='EXTRACTION_REVIEW_NOT_ACCEPTED'
            else: raise AssertionError('A first-only native source accepted its unreviewed siblings')
            assert not lifecycle.candidate_dir.exists()
            assert all(Path(path).read_bytes()==body for path,body in originals.items())
        '''))

    def test_complete_bundle_projects_every_raw_with_exact_source_gate_evidence(self):
        self.run_case(FIXTURE + dedent(r'''
            review=w._plain(w._review_handler(c).local_result)['data']['review']
            lifecycle=accept(review)
            prepared=w._plain(w._raw_lifecycle_handler(lifecycle).local_result)
            assert {ref['name'] for ref in prepared['data']['projected_raws']}=={ref['name'] for ref in raws}
            source=w._ref(review['source'])
            for projection in prepared['data']['candidate_projections']:
                proof=json.loads(Path(projection['proof_ref']['path']).read_bytes())
                assert source in proof['gate_refs']
                assert proof['accepted_at']=='1970-01-01T00:00:03.000Z'
                assert proof['source']['sha256']==next(ref['sha256'] for ref in raws if ref['name']==proof['source']['name'])
            assert source in w._knowledge_evidence_refs(lifecycle)
            assert all(Path(path).read_bytes()==body for path,body in originals.items())
        '''))

    def test_omitted_or_substituted_member_cannot_inherit_complete_bundle_approval(self):
        self.run_case(FIXTURE + dedent(r'''
            review=w._plain(w._review_handler(c).local_result)['data']['review']
            lifecycle=accept(review)
            # Equal bytes at a different producer member path are not the reviewed identity.
            alternate=ROOT/'other-ch04.md'; alternate.write_bytes(Path(raws[2]['path']).read_bytes())
            members[2]={**w._ref(alternate,raws[2]['name']),'attempt_id':'other-producer'}
            try: w._accepted_extraction_review(accept(review))
            except w.DomainError as exc: assert exc.code=='EXTRACTION_REVIEW_NOT_ACCEPTED'
            else: raise AssertionError('An unbound producer member inherited the bundle gate')
            members[2]=raws[2]
            partial=ROOT/'partial-bundle.md'; partial.write_bytes(w._extraction_review_bundle(artifact_id,members[:-1]))
            narrow=w._prepare_review(c,w._ref(partial),members,w._EXTRACTION_REVIEW_SCOPE,
                                    artifact_id=artifact_id,tier='A',contract='vault-raw')
            try: w._accepted_extraction_review(accept(narrow))
            except w.DomainError as exc: assert exc.code=='EXTRACTION_REVIEW_NOT_ACCEPTED'
            else: raise AssertionError('Full input_refs concealed a missing source-bundle member')
            assert not lifecycle.candidate_dir.exists()
        '''))

    def test_changed_prepared_bundle_refuses_without_rewriting_its_history(self):
        self.run_case(FIXTURE + dedent(r'''
            review=w._plain(w._review_handler(c).local_result)['data']['review']
            source=Path(review['source']); changed=source.read_bytes()+b'Changed bundle.\n'
            source.write_bytes(changed)
            try: w._review_handler(c)
            except w.DomainError as exc: assert exc.code=='INPUT_CHANGED'
            else: raise AssertionError('Retained immutable prepared source was overwritten')
            assert source.read_bytes()==changed
            try: w._accepted_extraction_review(accept(review))
            except w.DomainError as exc: assert exc.code=='INPUT_CHANGED'
            else: raise AssertionError('Changed source bytes inherited native approval')
        '''))

    def test_single_raw_can_earn_lifecycle_acceptance_without_a_bundle(self):
        self.run_case(FIXTURE + dedent(r'''
            import yaml
            members[:]=[raws[0],*members[-2:]]
            c=replace(c,data={**c.data,'step_data':rows})
            review=w._plain(w._review_handler(c).local_result)['data']['review']
            lifecycle=accept(review)
            prepared=w._plain(w._raw_lifecycle_handler(lifecycle).local_result)
            projected=prepared['data']['projected_raws']
            assert len(projected)==1 and projected[0]['name']==raws[0]['name']
            text=Path(projected[0]['path']).read_text(encoding='utf-8')
            fields=yaml.safe_load(text.split('---',2)[1])
            assert fields['status']=='published'
            assert fields['last_verified']=='1970-01-01T00:00:03.000Z'
            assert all(Path(path).read_bytes()==body for path,body in originals.items())
        '''))




if __name__ == '__main__':
    unittest.main()
