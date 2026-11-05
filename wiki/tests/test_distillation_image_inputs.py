"""Selected-image task preparation boundaries; no native or original effects."""
from textwrap import dedent
import unittest
import test_workflows as support


FIXTURE = r'''
from _workflow.contracts import ContractError, task_to_dict
raw=note('raw/threads/raw-a.md','Alpha only after Q.\n',{'type':'source','source_class':'thread'})
sid='a'*64; uid='u000001'
raw_ref=w._ref(raw)
primary={**raw_ref,'source_id':sid,'unit_id':uid,'destination':'raw/threads/raw-a.md'}
item={'item_id':'u000001-alpha','unit_id':uid,'source_id':sid,'raw':primary}
inventory={'scope':{'selected_units':[uid],'exclusions':[]},'expected_units':[{'unit_id':uid,'source_id':sid,'original_sha256':'b'*64}]}
root=ROOT/'project/docs/tmp/pipeline-runs/synthetic-run';root.mkdir(parents=True)
image=root/'reference-image-0001-000001.png'
image.write_bytes(bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000b49444154789c636000020000050001a5f645400000000049454e44ae426082'))
visual={**w._ref(image),'optional':False,'source_reference':'https://example.invalid/selected.png'}
scope=root/'capture-scope.json';scope.write_text(json.dumps(inventory),encoding='utf-8')
fidelity=root/'capture-verdict.json';fidelity.write_text('{"overall_verdict":"PASS"}',encoding='utf-8')
scope_ref=w._ref(scope,'capture-scope.json'); fidelity_ref=w._ref(fidelity,'capture-verdict.json')
assignments={'items':[item],'raw_inputs':[raw_ref],'citation_targets':{'raw/threads/raw-a':raw_ref}}
rows={'capture.scope':[{'item_id':'main','status':'done','data':{'image_inputs':[visual]},'artifacts':[scope_ref]}],
      'capture.fidelity':[{'item_id':'main','status':'done','data':{'verification':'PASS'},'artifacts':[fidelity_ref]}],
      'capture.lifecycle':[{'item_id':'main','status':'done','data':{},'artifacts':[primary]}],
      'L3.items':[{'item_id':'main','status':'done','data':assignments,'artifacts':[]}]}
c=context('L3.notes','distill',item_id=item['item_id'],data={'item':item,'step_data':rows,'inventory':inventory,
          'topic':'alpha','domain':'science','topic_slug':'alpha','image_inputs':[visual],'capture_root':str(root)})
c=replace(c,request=request('wiki-ingest','synthetic thread','--vault',str(VAULT)))
prepared=w._distill_handler(c)
fresh=prepared.tasks[0]
'''


class DistillationImageInputsTests(unittest.TestCase):
    run_case = support.WorkflowTests.run_case

    def test_selected_image_is_an_exact_input_without_parent_read_authority(self):
        self.run_case(FIXTURE + dedent(r'''
            original=image.read_bytes()
            expected=w._input(visual)
            assert expected in fresh.inputs and str(image) in fresh.read_roots
            assert str(root) not in fresh.read_roots
            assert fresh.inputs==(w._input(raw_ref),w._input(scope_ref),w._input(fidelity_ref),expected)
            assert fresh.outputs==(w._output('distillation.md','text/markdown'),
                                   w._output('citation-bindings.json'),w._output('coverage.json'))
            assert fresh.allowed_actions==('read','write')
            task_data=w._plain(prepared.local_result['data']['task_context'])
            assert task_data['raw_inputs']==[raw_ref]
            assert task_data['unit_id']==uid and task_data['source_ids']==[sid]
            assert image.read_bytes()==original and not Path(fresh.candidate_dir).exists()
            assert not (ROOT/'project/state.db').exists()
        '''))

    def test_multiple_declared_images_retain_order_and_exact_file_grants(self):
        self.run_case(FIXTURE + dedent(r'''
            second=root/'reference-image-0001-000002.png';second.write_bytes(image.read_bytes())
            second_ref=w._ref(second)
            selected=[visual,second_ref,visual]
            current=replace(c,data={**c.data,'image_inputs':selected})
            task=w._distill_handler(current).tasks[0]
            assert [ref for ref in task.inputs if ref.media_type.startswith('image/')]==[w._input(visual),w._input(second_ref)]
            assert str(image) in task.read_roots and str(second) in task.read_roots
            assert str(root) not in task.read_roots
            assert task.outputs==fresh.outputs and task.artifact_id==fresh.artifact_id
            assert not (ROOT/'project/state.db').exists()
        '''))

    def test_stale_or_missing_images_and_failed_capture_refuse_preparation(self):
        self.run_case(FIXTURE + dedent(r'''
            before={image:image.read_bytes(),raw:raw.read_bytes()}
            original=task_to_dict(fresh)
            for fault in ('changed-image','missing-image','changed-raw','capture-fail'):
                ctx=c
                if fault=='changed-image': image.write_bytes(b'Changed actual visual')
                elif fault=='missing-image': image.unlink()
                elif fault=='changed-raw': raw.write_bytes(b'Changed captured source')
                else:
                    altered={**rows,'capture.fidelity':[{**rows['capture.fidelity'][0],'data':{'verification':'FAIL'}}]}
                    ctx=replace(c,data={**c.data,'step_data':altered})
                try: w._distill_handler(ctx)
                except (w.DomainError,ContractError,OSError): pass
                else: raise AssertionError('Unusable source/image evidence admitted: '+fault)
                for path,body in before.items(): path.write_bytes(body)
            assert task_to_dict(fresh)==original
            assert {path:path.read_bytes() for path in before}==before
            assert not (ROOT/'project/state.db').exists()
        '''))


if __name__ == '__main__':
    unittest.main()
