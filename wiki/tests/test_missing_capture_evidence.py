"""Synthetic ordinary distillation preparation; no native or original effects."""
import unittest
import test_workflows as support


class MissingCaptureEvidenceTests(unittest.TestCase):
    run_case = support.WorkflowTests.run_case

    def test_current_distillation_binds_capture_evidence_and_refuses_invalid_prefix(self):
        self.run_case(r'''
            from _workflow.contracts import ContractError, task_to_dict
            raw=note('raw/videos/raw-a.md','Alpha only after Q.\n',{'type':'source','source_class':'video'})
            sid='a'*64; uid='u000001'
            raw_ref=w._ref(raw)
            primary={**raw_ref,'source_id':sid,'unit_id':uid,'destination':'raw/videos/raw-a.md'}
            item={'item_id':'u000001-alpha','unit_id':uid,'source_id':sid,'raw':primary}
            inventory={'scope':{'selected_units':[uid],'exclusions':[]},
                       'expected_units':[{'unit_id':uid,'source_id':sid,'original_sha256':'b'*64}]}
            root=ROOT/'project/docs/tmp/pipeline-runs/synthetic-run';root.mkdir(parents=True)
            scope=root/'capture-scope.json';scope.write_text(json.dumps(inventory),encoding='utf-8')
            fidelity=root/'capture-verdict.json';fidelity.write_text('{"overall_verdict":"PASS"}',encoding='utf-8')
            scope_ref=w._ref(scope,'capture-scope.json'); fidelity_ref=w._ref(fidelity,'capture-verdict.json')
            assignments={'items':[item],'raw_inputs':[raw_ref],
                         'citation_targets':{'raw/videos/raw-a':raw_ref}}
            rows={'capture.scope':[{'item_id':'main','status':'done','data':{},'artifacts':[scope_ref]}],
                  'capture.fidelity':[{'item_id':'main','status':'done','data':{'verification':'PASS'},'artifacts':[fidelity_ref]}],
                  'capture.lifecycle':[{'item_id':'main','status':'done','data':{},'artifacts':[primary]}],
                  'L3.items':[{'item_id':'main','status':'done','data':assignments,'artifacts':[]}]}
            c=context('L3.notes','distill',item_id=item['item_id'],data={'item':item,'step_data':rows,'inventory':inventory,
                      'topic':'alpha','domain':'science','topic_slug':'alpha'})
            c=replace(c,request=request('wiki-ingest','synthetic video','--vault',str(VAULT)))
            prepared=w._distill_handler(c);task=prepared.tasks[0]
            expected=tuple(w._input(ref) for ref in (raw_ref,scope_ref,fidelity_ref))
            assert task.inputs==expected
            assert all(ref.path in task.read_roots for ref in expected)
            assert str(root) not in task.read_roots
            assert task.outputs==(w._output('distillation.md','text/markdown'),
                                  w._output('citation-bindings.json'),w._output('coverage.json'))
            assert task.allowed_actions==('read','write')
            task_data=w._plain(prepared.local_result['data']['task_context'])
            assert task_data['raw_inputs']==[raw_ref]
            assert task_data['citation_targets']=={'raw/videos/raw-a':raw_ref}
            before={path:path.read_bytes() for path in (scope,fidelity,raw)}
            original=task_to_dict(task);context_data=w._plain(c.data)
            for variant in ('changed-scope','changed-fidelity','changed-raw','missing-scope',
                            'fidelity-fail','unprojected-raw','foreign-assignment'):
                data=w._plain(c.data)
                if variant=='changed-scope': scope.write_bytes(b'{}')
                elif variant=='changed-fidelity': fidelity.write_bytes(b'{}')
                elif variant=='changed-raw': raw.write_bytes(b'Changed raw evidence')
                elif variant=='missing-scope': del data['step_data']['capture.scope']
                elif variant=='fidelity-fail':
                    data['step_data']['capture.fidelity'][0]['data']={'verification':'FAIL'}
                elif variant=='unprojected-raw':
                    data['step_data']['capture.lifecycle'][0]['artifacts']=[]
                else:
                    data['item']['unit_id']='u000999'
                try: w._distill_handler(replace(c,data=data))
                except (w.DomainError,ContractError): pass
                else: raise AssertionError('Invalid capture evidence admitted: '+variant)
                for path,body in before.items(): path.write_bytes(body)
            assert task_to_dict(task)==original and w._plain(c.data)==context_data
            assert {path:path.read_bytes() for path in before}==before
            assert not (ROOT/'project/state.db').exists()
        ''')


if __name__ == '__main__':
    unittest.main()
