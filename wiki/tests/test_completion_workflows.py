"""Synthetic domain transitions; no native/model or live-provider qualification."""
import unittest
from textwrap import dedent

import test_workflows as fixtures


class CompletionWorkflowTests(unittest.TestCase):
    run_case = fixtures.WorkflowTests.run_case

    def test_json_fail_cannot_be_replaced_by_typed_pass(self):
        self.run_case(r'''
            c=context('consume','verify')
            path=ROOT/'verdict.json'
            path.write_text('{"overall_verdict":"FAIL","subjects":[],"criteria":[]}',encoding='utf-8')
            c=replace(c,data={**c.data,'step_data':{'verify':[{'item_id':'root','data':{'verification':'PASS'},'artifacts':[{**w._ref(path),'name':'verdict.json'}]}]}})
            try: w._verdict(c,'verify')
            except w.DomainError as exc: assert exc.code=='VERDICT_RESULT_CONFLICT'
            else: raise AssertionError('A failed document must block the typed PASS')
        ''')
    def test_retained_negative_diagnostic_cannot_earn_pass_or_artifact_authority(self):
        self.run_case(r'''
            from _workflow.contracts import TaskResult, result_to_dict
            c=context('consume','verify')
            diagnostic=ROOT/'rejected-verdict.json'
            diagnostic.write_text('{"overall_verdict":"PASS","malformed_projection":',encoding='utf-8')
            original=ROOT/'original-result.json'
            result=TaskResult(1,c.run_id,'verify','root','original-verifier','completed','FAIL',(),'1'*64,(),None,None)
            original.write_text(json.dumps(result_to_dict(result)),encoding='utf-8')
            data={'negative_only':True,'verification':'FAIL','rejected_verdict_diagnostic':w._ref(diagnostic),'original_task_result':w._ref(original)}
            row={'status':'negative_only','item_id':'root','artifacts':[],'data':data}
            def consume(value):
                return w._verdict(replace(c,data={**c.data,'step_data':{'verify':[value]}}),'verify')
            assert not w._pass(consume(row))
            assert not w._pass({'overall_verdict':'PASS','negative_only':True})
            for invalid in (
                {**row,'data':{**data,'verification':'PASS'}},
                {**row,'status':'done'},
                {**row,'data':{**data,'negative_only':False}},
                {**row,'artifacts':[{**w._ref(diagnostic),'name':'verdict.json'}]},
            ):
                try: consume(invalid)
                except w.DomainError as exc: assert exc.code=='VERDICT_RESULT_CONFLICT',exc.code
                else: raise AssertionError('A retained rejection granted accepted verdict authority')
            original.write_text(json.dumps(result_to_dict(replace(result,verification='PASS'))),encoding='utf-8')
            try: consume(row)
            except w.DomainError as exc: assert exc.code=='INPUT_CHANGED',exc.code
            else: raise AssertionError('Changed original result retained negative authority')
            row={**row,'data':{**data,'original_task_result':w._ref(original)}}
            try: consume(row)
            except w.DomainError as exc: assert exc.code=='VERDICT_RESULT_CONFLICT',exc.code
            else: raise AssertionError('A positive original result was treated as retained FAIL')
        ''')


    def test_negative_frozen_subject_and_criterion_block_acceptance(self):
        self.run_case(r'''
            from types import MappingProxyType
            for collection in ('subjects','criteria'):
                value=MappingProxyType({'overall_verdict':'PASS',collection:(MappingProxyType({'criterion_id':'fidelity','verdict':'FAIL','evidence':'Condition Q was omitted'}),)})
                assert not w._pass(value)
        ''')

    def test_distillation_freezes_capture_evidence_and_refuses_stale_current_inputs(self):
        self.run_case(r'''
            sid=provenance.source_id({'kind':'canonical-reference','id':'synthetic:capture-grants'})
            raw=note('raw/documents/alpha.md','Alpha starts only after Q.',{'type':'source','source_id':sid})
            for legacy in (False,True):
                item_id='legacy-one' if legacy else 'u000001-alpha'
                item={'item_id':item_id,'unit_id':'u000001','source_id':sid,'raw':{**w._ref(raw),'destination':'raw/documents/alpha.md'}}
                inventory=ROOT/('legacy-inventory.json' if legacy else 'selected-scope.json')
                verdict=ROOT/('legacy-verdict.json' if legacy else 'capture-verdict.json')
                inventory.write_text('{"selected_units":["u000001"]}',encoding='utf-8')
                verdict.write_text('{"overall_verdict":"PASS"}',encoding='utf-8')
                inventory_ref=w._ref(inventory,'capture-inventory.json' if legacy else 'capture-scope.json')
                verdict_ref=w._ref(verdict,'capture-verdict.json')
                inventory_step='legacy.capture' if legacy else 'capture.scope'
                verdict_step='legacy.fidelity' if legacy else 'capture.fidelity'
                rows={inventory_step:[{'item_id':'main','status':'done','artifacts':[inventory_ref],'data':{}}],
                      verdict_step:[{'item_id':item_id if legacy else 'main','status':'done','artifacts':[verdict_ref],'data':{'verification':'PASS'}}]}
                if legacy:
                    sibling=ROOT/'unrelated-legacy-verdict.json'; sibling.write_text('{"overall_verdict":"PASS"}',encoding='utf-8')
                    sibling_ref=w._ref(sibling,'capture-verdict.json')
                    rows[verdict_step].append({'item_id':'legacy-other','status':'done','artifacts':[sibling_ref],'data':{'verification':'PASS'}})
                    sibling.write_text('changed unrelated sibling',encoding='utf-8')
                c=context('legacy.distill' if legacy else 'L3.notes','distill',item_id=item_id,
                          data={'item':item,'topic':'alpha','topic_slug':'alpha','domain':'science','step_data':rows})
                c=replace(c,request=request('wiki-compile' if legacy else 'wiki-ingest','synthetic source'))
                c.request.project_root.mkdir(parents=True,exist_ok=True)
                task=w._distill_handler(c).tasks[0]
                assert {ref.path for ref in task.inputs}=={str(raw),str(inventory),str(verdict)}
                assert str(inventory) in task.read_roots and str(verdict) in task.read_roots
                assert str(ROOT) not in task.read_roots
                assert task.write_roots==(task.candidate_dir,)
                for path in (inventory,verdict):
                    original=path.read_bytes(); path.write_bytes(b'changed required capture evidence')
                    try: w._distill_handler(c)
                    except w.DomainError as exc: assert exc.code=='INPUT_CHANGED',exc.code
                    else: raise AssertionError('A stale selected capture input was admitted to distillation')
                    path.write_bytes(original)
        ''')

    def test_selected_primary_context_binds_cross_unit_qualification_for_distill_and_candidate_repair(self):
        self.run_case(r'''
            from copy import deepcopy
            from _workflow.contracts import task_to_dict
            from wiki.runtime.citations import validate_citations
            c=book_context()
            sid=c.data['captured_raws'][0]['source_id']
            foreign_sid=provenance.source_id({'kind':'canonical-reference','id':'synthetic:foreign'})
            raws=w._plain(c.data['captured_raws'])
            foreign={**deepcopy(raws[0]),'unit_id':'u000003','source_id':foreign_sid,
                     'identity':{'kind':'canonical-reference','id':'synthetic:foreign'}}
            foreign['metadata']['title']='Foreign work'
            raws.append(foreign)
            expected=[{**unit,'source_id':raw['source_id']} for unit,raw in zip(
                [*w._plain(c.data['inventory']['expected_units']),{'unit_id':'u000003','original_sha256':'f'*64}],raws)]
            inventory={'expected_units':expected,'scope':{'selected_units':[raw['unit_id'] for raw in raws],'exclusions':[]}}
            c=replace(c,request=request('wiki-ingest','synthetic book'),data={**c.data,'captured_raws':raws,'inventory':inventory})
            plans,units=w._capture_plan(c)
            root=Path(c.paths['run_root']);root.mkdir(parents=True)
            scope=root/'selected-scope.json';scope.write_text(json.dumps(inventory),encoding='utf-8')
            mapping=root/'raw-note-map.json';mapping.write_text(json.dumps(w._plain({'units':units})),encoding='utf-8')
            fidelity=root/'capture-verdict.json';fidelity.write_text('{"overall_verdict":"PASS"}',encoding='utf-8')
            projected=[]
            bodies=['Worked example: cost 20 credits excludes tax.','The cost example is conditional on Condition Q.','A foreign work asserts no condition.']
            for unit,body in zip(units,bodies):
                path=root/'retained-primary'/unit['unit_id']/'raw.md';path.parent.mkdir(parents=True)
                path.write_text('---\ntype: source\nsource_id: '+unit['source_id']+'\n---\n'+body,encoding='utf-8')
                projected.append(w._ref(path,unit['output_name']))
            rows={'capture.scope':[{'item_id':'root','status':'done','data':{},'artifacts':[w._ref(scope,'capture-scope.json')]}],
                  'capture.fidelity':[{'item_id':'root','status':'done','data':{'verification':'PASS'},'artifacts':[w._ref(fidelity,'capture-verdict.json')]}],
                  'L1.extract':[{'item_id':'root','status':'done','data':{},'artifacts':[w._ref(mapping,'raw-note-map.json')]}],
                  'capture.lifecycle':[{'item_id':'root','status':'done','data':{},'artifacts':projected}]}
            items_context=replace(c,step=replace(c.step,step_id='L3.items'),data={**c.data,'projected_raws':projected,'step_data':rows})
            assignments=w._plain(w._distill_items_handler(items_context).local_result['data'])
            item=assignments['items'][0]
            rows['L3.items']=[{'item_id':'root','status':'done','data':assignments,'artifacts':[]}]
            c=context('L3.notes','distill',item_id=item['item_id'],data={**c.data,'item':item,'step_data':rows})
            c=replace(c,request=request('wiki-ingest','synthetic book'))
            # A real original TaskSpec predates the additional primary-context grant.
            old_context=replace(c,paths={**c.paths,'attempt_id':'original-attempt','candidate_dir':root/'attempts/original-attempt/candidate'},
                                data={**c.data,'step_data':{key:value for key,value in rows.items() if key!='L3.items'}})
            old=w._distill_handler(old_context).tasks[0]
            old_json=root/'original-task.json';old_json.write_text(json.dumps(task_to_dict(old)),encoding='utf-8')
            Path(old.candidate_dir).mkdir(parents=True,exist_ok=True)
            rejected=Path(old.candidate_dir)/'distillation.md';rejected.write_text('The cost is 20 credits without qualification.',encoding='utf-8')
            diagnostic=root/'actual-synthetic-finding.json';diagnostic.write_text('{"finding":"The selected appendix supplies Condition Q."}',encoding='utf-8')
            protected={path:path.read_bytes() for path in (old_json,rejected,diagnostic,scope,mapping,fidelity,*[Path(ref['path']) for ref in projected])}
            fresh=w._distill_handler(c)
            continuation={'kind':'candidate','original_task':w._ref(old_json),
                          'original_candidates':[w._ref(rejected)],'diagnostics':[w._ref(diagnostic)]}
            repaired_context=replace(c,paths={**c.paths,'attempt_id':'repair-attempt','candidate_dir':root/'attempts/repair-attempt/candidate'},
                                     data={**c.data,'source_continuation':continuation})
            repaired=w._distill_handler(repaired_context)
            support=projected[1];foreign_ref=projected[2]
            label=units[1]['destination'].removesuffix('.md')
            claim='[PARAPHRASE] The worked cost example is conditional on Condition Q. ^[[['+label+']]]'
            raw_text=Path(support['path']).read_bytes().decode('utf-8');span=bodies[1];start=raw_text.index(span)
            binding={'citation_start':claim.index('^[[['),'raw_path':support['path'],'raw_sha256':support['sha256'],
                     'start':start,'end':start+len(span),'span':span,'claim_start':0,'claim_end':len(claim),'claim_sha256':sha256(claim.encode()).hexdigest()}
            for prepared in (fresh,repaired):
                task=prepared.tasks[0];data=w._plain(prepared.local_result)['data']['task_context']
                inputs={ref.path:ref.sha256 for ref in task.inputs}
                assert inputs[support['path']]==support['sha256']
                assert support['path'] in task.read_roots
                assert str(Path(support['path']).parent) not in task.read_roots
                assert foreign_ref['path'] not in inputs and foreign_ref['path'] not in task.read_roots
                assert data['unit_id']==item['unit_id'] and data['source_ids']==[sid]
                assert task.outputs==old.outputs and task.artifact_id==old.artifact_id
                valid=validate_citations(claim,data['raw_inputs'],root,[binding],citation_targets=data['citation_targets'])
                assert valid.valid,valid.errors
                for bad_ref,bad_label in ((foreign_ref,units[2]['destination'].removesuffix('.md')),
                                          (support,'raw/books/not-selected')):
                    bad_claim=claim.replace(label,bad_label)
                    bad={**binding,'raw_path':bad_ref['path'],'raw_sha256':bad_ref['sha256'],
                         'claim_end':len(bad_claim),'claim_sha256':sha256(bad_claim.encode()).hexdigest()}
                    assert not validate_citations(bad_claim,data['raw_inputs'],root,[bad],citation_targets=data['citation_targets']).valid
                coverage=next(spec for spec in task.outputs if spec.name=='coverage.json')
                candidate=Path(task.candidate_dir);candidate.mkdir(parents=True,exist_ok=True)
                current=candidate/'distillation.md';current.write_text(claim,encoding='utf-8')
                document={key:getattr(task,key) for key in ('run_id','step_id','item_id','attempt_id')}
                document.update(schema_version=1,task_sha256=task.semantic_sha256,entries=[{'unit_id':item['unit_id'],'topic':'alpha',
                    'artifact_refs':[{'path':'distillation.md','sha256':w._hash(current)}],'disposition':'covered',
                    'reason':'See the existing worked-example claim.','scope_rule':'Assigned unit only; appendix is supporting context.'}])
                if task.duty=='repair':
                    document['repair_receipt']={'corrections':[{'location':'worked-example claim','evidence_bindings':[binding]}]}
                path=candidate/'coverage.json';path.write_text(json.dumps(document),encoding='utf-8')
                validation_context=replace(c,paths={**c.paths,'candidate_dir':candidate,'attempt_id':task.attempt_id},data={**c.data,**data,'task':task})
                validation=w.validate_artifact(coverage,path,validation_context)
                assert validation.valid,validation.errors
                document['entries'].append({**document['entries'][0],'unit_id':units[1]['unit_id']})
                path.write_text(json.dumps(document),encoding='utf-8')
                assert not w.validate_artifact(coverage,path,validation_context).valid,'Context widened unit coverage'
            assert repaired.tasks[0].duty=='repair'
            # Independent selection, projection membership and exact bytes all remain mandatory.
            for variant in ('unselected','foreign-path','stale'):
                changed_rows=deepcopy(rows)
                if variant=='unselected':
                    changed=deepcopy(inventory);changed['scope']['selected_units'].remove(units[1]['unit_id'])
                    changed['scope']['exclusions']=[{'unit_id':units[1]['unit_id'],'scope_rule':'explicit','reason':'Not selected'}]
                    changed_scope=root/'partial-scope.json';changed_scope.write_text(json.dumps(changed),encoding='utf-8')
                    changed_rows['capture.scope'][0]['artifacts']=[w._ref(changed_scope,'capture-scope.json')]
                elif variant=='foreign-path':
                    changed_rows['L3.items'][0]['data']['items'][1]['raw'].update({key:foreign_ref[key] for key in ('path','sha256','media_type')})
                else:
                    Path(support['path']).write_text('Changed supporting primary bytes',encoding='utf-8')
                try: w._distill_handler(replace(c,data={**c.data,'step_data':changed_rows}))
                except w.DomainError as exc: assert exc.code in ('DISTILL_CONTEXT_INVALID','INPUT_CHANGED'),exc.code
                else: raise AssertionError('Unselected/foreign/stale supporting raw was admitted: '+variant)
                if variant=='stale': Path(support['path']).write_bytes(protected[Path(support['path'])])
            assert all(path.read_bytes()==content for path,content in protected.items())
        ''')

    def test_distillation_and_repair_reject_invalid_or_stale_coverage_before_review(self):
        self.run_case(r'''
            from copy import deepcopy
            from _workflow.contracts import OutputSpec
            sid=provenance.source_id({'kind':'canonical-reference','id':'synthetic:coverage'})
            raw=note('raw/documents/alpha.md','Alpha starts only after Q.',{'type':'source','source_id':sid})
            item={'item_id':'u000001-alpha','unit_id':'u000001','source_id':sid,'raw':{**w._ref(raw),'destination':'raw/documents/alpha.md'}}
            c=context('L3.notes','distill',item_id=item['item_id'],data={'item':item,'topic':'alpha','topic_slug':'alpha','domain':'science'})
            c=replace(c,request=request('wiki-ingest','synthetic source'))
            c.request.project_root.mkdir(parents=True)
            original=w._distill_handler(c)
            inherited=w._plain(original.local_result)['data']['task_context']
            for current_path in ('distillation.md','repaired.md'):
                prepared=original if current_path=='distillation.md' else w._task(c,'repair','Repair the same assigned note.',[OutputSpec('distillation.md',current_path,'text/markdown',True),OutputSpec('coverage.json','coverage.json','application/json',True)],inputs=[w._ref(raw)],data=inherited)
                task=prepared.tasks[0]
                data=w._plain(prepared.local_result)['data']['task_context']
                checked=replace(c,data={**c.data,**data,'task':task})
                c.candidate_dir.mkdir(parents=True,exist_ok=True)
                current=c.candidate_dir/current_path; current.write_text('Current candidate preserves condition Q.',encoding='utf-8')
                decoy=c.candidate_dir/'citation-bindings.json'; decoy.write_text('[]',encoding='utf-8')
                identities={key:getattr(task,key) for key in ('run_id','step_id','item_id','attempt_id')}
                document={**identities,'task_sha256':task.semantic_sha256,'schema_version':1,'entries':[{'unit_id':'u000001','topic':'alpha','artifact_refs':[{'path':current_path,'sha256':w._hash(current)}],'disposition':'covered','reason':'See the existing Alpha claim.','scope_rule':'The assigned unit/topic.'}]}
                spec=next(output for output in task.outputs if output.name=='coverage.json')
                path=c.candidate_dir/'coverage.json'
                path.write_text(json.dumps(document),encoding='utf-8')
                result=w.validate_artifact(spec,path,checked)
                assert result.valid,result.errors
                malformed=[]
                wrong=deepcopy(document); wrong['coverage']=wrong.pop('entries'); malformed.append(wrong)
                wrong=deepcopy(document); wrong['entries'][0]['artifact_refs']=[current_path]; malformed.append(wrong)
                wrong=deepcopy(document); wrong['entries'][0]['unit_id']='u000002'; malformed.append(wrong)
                wrong=deepcopy(document); wrong['entries'][0]['topic']='beta'; malformed.append(wrong)
                wrong=deepcopy(document); wrong['entries'][0]['scope_rule']={'selected_units':['u000001']}; malformed.append(wrong)
                wrong=deepcopy(document); wrong['entries'][0]['artifact_refs'][0]['sha256']='0'*64; malformed.append(wrong)
                wrong=deepcopy(document); wrong['entries'][0]['artifact_refs']=[{'path':decoy.name,'sha256':w._hash(decoy)}]; malformed.append(wrong)
                for wrong in malformed:
                    path.write_text(json.dumps(wrong),encoding='utf-8')
                    result=w.validate_artifact(spec,path,checked)
                    assert not result.valid,('Malformed coverage reached review',current_path,wrong)
                path.write_text(json.dumps(document),encoding='utf-8')
                current.write_text('Changed candidate drops condition Q.',encoding='utf-8')
                assert not w.validate_artifact(spec,path,checked).valid,'Stale coverage hash reached review'
        ''')

    def test_missing_named_upstream_cannot_fall_back_to_global_raws(self):
        self.run_case(r'''
            for n in range(3):
                note(f'raw/documents/alpha-{n}.md','Alpha after Q.',{'type':'source','source_class':'documents','provenance_class':'document','topic':'alpha'})
            try: w.preflight(request('wiki-compile','--from-pipeline','nonexistent','science: alpha','--vault',str(VAULT)))
            except w.DomainError as exc: assert exc.code=='UPSTREAM_ACCEPTANCE_UNAVAILABLE',exc.code
            else: raise AssertionError('Named handoff silently substituted the global corpus')
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_unenrolled_empty_report_preserves_every_vault_byte(self):
        self.run_case(r'''
            (VAULT/'.wiki-schema.md').write_text('---\nwiki_name: SyntheticWiki\nextends: canonical-schema.md\nextends_version: "3.0"\ndomains: [science]\ntopics: {science: [alpha]}\n---\n',encoding='utf-8')
            before={p.relative_to(VAULT).as_posix():p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
            from _workflow.controller import Controller
            def forbidden_native(**kwargs): raise AssertionError('Empty reporting has no native work')
            outcome=Controller(w.build_workflow,skills_root=SKILLS,adapter_factory=forbidden_native).run(request('wiki-content-check','--vault',str(VAULT),'--all'))
            assert outcome.status=='done',outcome.error
            assert before=={p.relative_to(VAULT).as_posix():p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
            for verb,args in [('wiki-ingest',['body','--title','Input','--topic','alpha','--domain','science']),('wiki-reverify',['--snapshot-on-drift'])]:
                try: w.preflight(request(verb,*args,'--vault',str(VAULT)))
                except w.DomainError as exc: assert exc.code=='VAULT_CONTROLS_NOT_ENROLLED'
                else: raise AssertionError('Unenrolled mutation was admitted')
        ''')

    def test_transcript_url_is_not_a_local_preflight_path(self):
        self.run_case(r'''
            data=w.preflight(request('wiki-ingest','https://example.invalid/lesson','--source-class','course','--transcript','https://example.invalid/transcript.txt','--topic','alpha','--domain','science','--vault',str(VAULT)))
            assert data['options']['transcript']=='https://example.invalid/transcript.txt'
            assert all('https:' not in ref['path'] for ref in data['inputs'])
        ''')

    def test_missing_local_sources_never_become_supplied_text(self):
        self.run_case(r'''
            before={p.relative_to(VAULT).as_posix():p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
            for index,operand in enumerate((str(ROOT/'missing.pdf'),'./missing','../missing','missing.pdf','missing.docx')):
                for title in (None,'Explicit title'):
                    c=context(f'L1.acquire-{index}-{bool(title)}','acquire',data={'options':{'payload':operand,'title':title,'source_class':None,'transcript':None}})
                    try: w._acquire_handler(c)
                    except w.DomainError as exc: assert exc.code=='SOURCE_UNAVAILABLE',exc.code
                    else: raise AssertionError('Missing local source was captured as filename text')
                    assert not c.candidate_dir.exists()
            assert before=={p.relative_to(VAULT).as_posix():p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
            body='Alpha needs Q/R clearance before starting; worked cost20.'
            c=context('L1.supplied-text','acquire',data={'options':{'payload':body,'title':'Alpha source','source_class':None,'transcript':None}})
            data=w._acquire_handler(c).local_result['data']
            assert data['inventory']['expected_units'][0]['original_sha256']==sha256(body.encode()).hexdigest()
            assert Path(data['captured_raws'][0]['path']).read_text(encoding='utf-8')==body
        ''')

    def test_physical_source_class_keeps_original_provenance_in_drift(self):
        self.run_case(r'''
            note('raw/books/alpha.md','Book.',{'type':'source','source_class':'books','provenance_class':'book','freshness':'permanent','content_hash':'sha256:'+'1'*64})
            data=w.preflight(request('wiki-reverify','--vault',str(VAULT),'--freshness','all','--source-class','book'))
            assert [Path(item['path']).name for item in data['items']]==['alpha.md']
            for physical,origin in [('books','book'),('articles','web'),('threads','thread'),('assets','image'),('documents','pdf'),('videos','course')]:
                assert w._provenance_class({'source_class':physical,'provenance_class':origin})==origin
            assert w._provenance_class({'source_class':'documents'}) is None
            assert w._provenance_class({'source_class':'videos'}) is None
            try: w._provenance_class({'source_class':'books','provenance_class':'web'})
            except w.DomainError: pass
            else: raise AssertionError('Conflicting origin was accepted')
        ''')

    def test_supplied_transcript_preserves_original_bytes_and_drifts_by_same_method(self):
        self.run_case(r'''
            original=b'Condition Q is required.\r\nThe exact cost is20.\r\n'
            transcript=ROOT/'transcript.txt'; transcript.write_bytes(original)
            c=context('L1.acquire','acquire',data={'options':{'transcript':str(transcript)}})
            c=replace(c,request=request('wiki-ingest','https://example.invalid/lesson'))
            inventory,raws,images,diagnostics=w._capture_sources(c,[{'source':'https://example.invalid/lesson','source_class':'course'}])
            assert (c.candidate_dir/'original-0001.txt').read_bytes()==original
            raw=raws[0]; text=Path(raw['path']).read_text(encoding='utf-8')
            assert text=='Condition Q is required.\nThe exact cost is20.\n'
            assert raw['metadata']['transcript_method']=='supplied-file'
            assert raw['metadata']['transcript_source']==str(transcript)
            assert inventory['expected_units'][0]['original_sha256']==sha256(original).hexdigest()
            path=note('raw/videos/lesson.md',text,{'type':'source','source_class':'videos','provenance_class':'course','lesson_url':'https://example.invalid/lesson','transcript_method':'supplied-file','transcript_source':str(transcript),'content_hash':provenance.content_hash(text)})
            item={**w._ref(path),'item_id':'lesson'}
            d=context('L1.refetch','drift',data={'item':item,'options':{'backfill_mode':False}})
            same=w.prepare_step(d).local_result['data']['diff']
            assert same['source_available'] and same['hash_match'] is True
            transcript.write_text('Unrelated replacement transcript.',encoding='utf-8')
            changed=w.prepare_step(d).local_result['data']['diff']
            assert changed['source_available'] and changed['hash_match'] is False
            assert changed['source_ref']==str(transcript)
        ''')

    def test_capture_rejects_unassigned_acquisition_metadata(self):
        self.run_case(r'''
            source=ROOT/'source.txt'; body='Alpha starts at10 only after condition Q.\n'
            source.write_bytes(body.encode('utf-8'))
            data=w.preflight(request('wiki-ingest',str(source),'--vault',str(VAULT),'--topic','alpha','--domain','science'))
            data={**data,'planned_date':'2001-02-03'}
            acquire=context('L1.acquire','acquire',data=data)
            captured=w.prepare_step(acquire).local_result['data']
            scope=w.prepare_step(context('capture.scope','capture_scope',data={**data,**captured})).local_result
            c=context('L1.extract','extract',data={**data,**captured,**scope['data'],'step_data':{'capture.scope':[w._plain(scope)]}})
            prepared=w.prepare_step(c); task=prepared.tasks[0]
            c=replace(c,data={**c.data,**prepared.local_result['data'],'task':task})
            output=next(spec for spec in task.outputs if spec.media_type=='text/markdown')
            assigned=c.data['task_context']['output_context'][output.name]
            original=Path(assigned['source_snapshot']['path'])
            metadata=w._plain(assigned['expected_metadata'])
            metadata.update(type='source',artifact_role='capture',title='Alpha',domain='science',
                source_hash=w._hash(original),source_hash_kind='bytes',source_hash_algorithm='sha256',
                ingested=data['planned_date'],backfill=False,vault='SyntheticWiki',author='Synthetic Generator',
                pipeline_id=c.run_id,status='draft',last_verified=None,freshness='permanent',confidence='high',
                human_edited=False,tags=['domain/science','topic/alpha','type/source','vault/SyntheticWiki'])
            metadata['content_hash']=provenance.content_hash(body)
            path=c.candidate_dir/output.relative_path; path.parent.mkdir(parents=True,exist_ok=True)
            def candidate(**changes):
                path.write_text('---\n'+yaml.safe_dump({**metadata,**changes},sort_keys=False)+'---\n'+body,encoding='utf-8')
                return w.validate_artifact(output,path,c)
            valid=candidate(); assert valid.valid,valid.errors
            for field,value in (('ingested','2001-02-02'),('source_hash_kind','text-lf')):
                invalid=candidate(**{field:value})
                assert not invalid.valid,('Unassigned acquisition metadata was admitted',field)
                assert any(error['code']=='ARTIFACT_SUBJECT_MISMATCH' and error['details'].get('field')==field for error in invalid.errors),invalid.errors
            assert source.read_text(encoding='utf-8')==body
        ''')

    def test_distillation_accepts_exact_json_raw_membership_and_rejects_tampering(self):
        self.run_case(r'''
            source=ROOT/'source.txt'; source.write_bytes(b'Alpha starts at10 only after condition Q.\n')
            data=w.preflight(request('wiki-ingest',str(source),'--vault',str(VAULT),'--topic','alpha','--domain','science'))
            captured=w._plain(w.prepare_step(context('L1.acquire','acquire',data=data)).local_result['data'])
            captured['captured_raws'][0]['metadata']['source_authors']=['Invented Author']
            c=context('L3.items','distill_items',data={**data,**captured})
            _,units=w._capture_plan(c)
            mapping=ROOT/'raw-note-map.json'
            mapping.write_text(json.dumps({'units':w._plain(units)}),encoding='utf-8')
            projected=ROOT/'projected.md'; projected.write_bytes(source.read_bytes())
            rows={'L1.extract':[{'item_id':'root','data':{},'artifacts':[{**w._ref(mapping),'name':'raw-note-map.json'}]}],
                'capture.fidelity':[{'item_id':'root','data':{'verification':'PASS'},'artifacts':[]}]}
            c=replace(c,data={**c.data,'projected_raws':[{**w._ref(projected),'name':units[0]['output_name']}],'step_data':rows})
            result=w.prepare_step(c).local_result['data']
            assert [item['unit_id'] for item in result['items']]==[units[0]['unit_id']]
            assert result['items'][0]['raw']['path']==str(projected)
            exact=json.loads(mapping.read_text(encoding='utf-8'))
            for field,value in (('source_id','0'*64),('destination','raw/documents/other.md'),
                    ('metadata',{'source_authors':['Different Author']})):
                changed=json.loads(json.dumps(exact)); changed['units'][0][field]=value
                mapping.write_text(json.dumps(changed),encoding='utf-8')
                changed_rows={**rows,'L1.extract':[{'item_id':'root','data':{},'artifacts':[{**w._ref(mapping),'name':'raw-note-map.json'}]}]}
                invalid=replace(c,data={**c.data,'step_data':changed_rows})
                refused=w.prepare_step(invalid)
                assert refused.status=='blocked',(field,refused.status)
                assert refused.local_result['data']['error']['code']=='RAW_SUBJECT_MISMATCH'
            assert source.read_bytes()==b'Alpha starts at10 only after condition Q.\n'
        ''')

    def test_captured_html_resolves_relative_links_and_persists_depth_selection(self):
        self.run_case(r'''
            from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
            from threading import Thread
            requests=[]
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    requests.append(self.path)
                    bodies={'/nested/root.html':b'<html><title>Alpha</title><p>Only after condition Q, cost20.</p><a href="first.html">First</a><a href="first.html">Again</a><a href="/other.html">Other</a></html>','/nested/first.html':b'<html><p>R follows Q.</p><a href="second.html">Second</a></html>','/other.html':b'<html><p>Not selected.</p></html>'}
                    body=bodies[self.path];self.send_response(200);self.send_header('Content-Type','text/html');self.end_headers();self.wfile.write(body)
                def log_message(self,*args): pass
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            thread=Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base='http://127.0.0.1:'+str(server.server_port)
                c=context('L1.acquire','acquire',data={'options':{'transcript':None}})
                inventory,raws,images,diagnostics=w._capture_sources(c,[{'source':base+'/nested/root.html','source_class':'web'}])
                assert w._source_links(raws)==[base+'/nested/first.html',base+'/other.html']
                data={'captured_raws':raws,'options':{'payload':base+'/nested/root.html','depth':2},'decisions':{'follow-links-1':[base+'/nested/first.html']}}
                for raw in raws: raw['capture_depth']=0
                selected=w.prepare_step(context('L1.links1','links',policy={'link_depth':1},data=data)).local_result['data']
                assert [item['url'] for item in selected['items']]==[base+'/nested/first.html']
                assert set(selected['unfetched_links'])=={base+'/other.html'}
                linked=context('L1.linked1','linked',data={'item':selected['items'][0],'options':{'transcript':None}})
                captured=w.prepare_step(linked).local_result['data']['link_capture']
                assert w._source_links(captured['raw_inputs'])==[base+'/nested/second.html']
                deeper=context('L1.links2','links',policy={'link_depth':2},data={**data,'options':{**data['options'],'depth':1},'captured_raws':captured['raw_inputs']})
                result=w.prepare_step(deeper).local_result['data']
                assert not result['items'] and base+'/nested/second.html' in result['unfetched_links']
                assert requests==['/nested/root.html','/nested/first.html']
            finally:
                server.shutdown();server.server_close();thread.join()
        ''')

    def test_unavailable_units_require_explicit_bound_partial_scope(self):
        self.run_case(r'''
            root=ROOT/'capture';root.mkdir()
            first=root/'first.txt';first.write_bytes(b'Condition Q and exact cost20.')
            second=root/'second.txt';second.write_bytes(b'Original unit inaccessible to extractor.')
            identity={'kind':'canonical-reference','id':'synthetic:partial'};sid=provenance.source_id(identity)
            refs=[{**w._ref(first),'source_id':sid,'kind':'section'},{**w._ref(second),'source_id':sid,'kind':'section'}]
            expected=w.capture.enumerate_snapshot_units(refs,root)
            actual=[{**expected[0],'produced_paths':expected[0]['expected_paths'],'produced_sha256':expected[0]['original_sha256'],'coverage':{'complete':True,'images':[]}}, {**expected[1],'status':'unavailable','reason':'Extractor cannot read this declared unit'}]
            inventory={'schema_version':1,'capture_scope':'complete','expected_units':expected,'units':actual,'diagnostics':{}}
            raw={**w._ref(first),'unit_id':'u000001','source_id':sid}
            c=context('capture.scope','capture_scope',data={'inventory':inventory,'capture_root':str(root),'captured_raws':[raw]})
            decision=w.prepare_step(c)
            assert decision.status=='blocked' and decision.decision['key']=='capture.scope'
            full=replace(c,data={**c.data,'decisions':{'capture.scope':'full'}})
            assert w.prepare_step(full).status=='blocked'
            partial=replace(c,data={**c.data,'decisions':{'capture.scope':'available'}})
            result=w.prepare_step(partial)
            assert result.status=='local_result'
            retained=result.local_result['data']['inventory']
            assert retained['capture_scope']=='partial' and retained['scope']['selected_units']==('u000001',)
            assert retained['units'][1]['status']=='outside_scope'
            assert w.capture.validate_capture(w._plain(retained),root).valid
            assert inventory['units'][1]['status']=='unavailable'
            assert first.read_bytes()==b'Condition Q and exact cost20.'
            stale=root/'unselected-inventory.json';stale.write_text(w._json(inventory),encoding='utf-8')
            extraction=context('L1.extract','extract',data={**w._plain(result.local_result['data']),'step_data':{'capture.scope':[{'artifacts':[{**w._ref(stale),'name':'capture-scope.json'}]}]}})
            try: w._extract_handler(extraction)
            except w.DomainError as exc: assert exc.code=='CAPTURE_INVENTORY_CHANGED',exc.code
            else: raise AssertionError('Extraction accepted the original complete inventory after an explicit partial selection')
        ''')

    def test_thread_without_archive_never_refetches_mutable_origin(self):
        self.run_case(r'''
            path=note('raw/threads/thread.md','Original comment.',{'type':'source','source_class':'threads','provenance_class':'thread','source_url':'https://www.reddit.com/comments/synthetic','content_hash':'sha256:'+'1'*64})
            c=context('L1.refetch','drift',data={'item':{**w._ref(path),'item_id':'thread'},'options':{'backfill_mode':False}})
            record=w.prepare_step(c).local_result['data']['diff']
            assert record['source_available'] is False and record['hash_match'] is None
            assert record['source_ref'] is None and record['fetch_error']['code']=='SOURCE_UNAVAILABLE'
        ''')

    def test_local_archive_resolution_requires_contained_current_snapshot(self):
        self.run_case(r'''
            original=note('raw/documents/original.bin','Exact acquired original bytes')
            sid=provenance.source_id({'kind':'canonical-reference','id':'synthetic:archive'})
            digest=w._hash(original);logical='local:archive/document/'+sid+'/'+digest+'.bin'
            manifest=VAULT/'raw/_manifest.json'
            value=json.loads(manifest.read_text(encoding='utf-8'))
            value['wiki_workflow']['snapshots']={logical:{'source_id':sid,'path':'raw/documents/original.bin','sha256':digest,'media_type':'application/octet-stream'}}
            manifest.write_text(json.dumps(value),encoding='utf-8')
            path,media=w._local_snapshot(resolved(),logical)
            assert path==original and path.read_bytes()==b'Exact acquired original bytes'
            original.write_bytes(b'Human replacement')
            try: w._local_snapshot(resolved(),logical)
            except (w.DomainError,ValueError): pass
            else: raise AssertionError('Stale logical snapshot was read')
            value['wiki_workflow']['snapshots'][logical]['path']='../outside.bin'
            manifest.write_text(json.dumps(value),encoding='utf-8')
            try: w._local_snapshot(resolved(),logical)
            except (w.DomainError,ValueError): pass
            else: raise AssertionError('Logical archive escaped the vault')
        ''')

    def test_fresh_inline_initialization_preserves_requested_identity_across_root_spelling(self):
        self.run_case(r'''
            from _workflow.controller import Controller
            from contextlib import closing
            import os
            import sqlite3
            fresh=ROOT/'fresh-vault'
            fresh.mkdir()
            selected=str(fresh).swapcase() if os.name=='nt' else str(fresh)
            outcome=Controller(w.build_workflow,skills_root=SKILLS).run(request('wiki-ingest','--init','--vault',selected,'--wiki-name','ExampleObs','--domain','examples'))
            assert outcome.status=='done',outcome.error
            resolved=w.schema.resolve_schema(fresh,None)
            assert resolved.wiki_name=='ExampleObs' and resolved.canonical_version=='3.1'
            assert resolved.domains==('examples',)
            plan_ref=next(ref for ref in outcome.outputs if ref['name']=='control-plan.json')
            envelope=json.loads(Path(plan_ref['path']).read_text(encoding='utf-8'))
            plan=w.vault_controls.hydrate_control_plan(envelope)
            w.vault_controls.verify_controls(plan,envelope=envelope)
            assert envelope['schema_identity']['wiki_name']=='ExampleObs'
            assert all(item['expected_sha256'] is None for item in envelope['plan']['changes'])
            with closing(sqlite3.connect(ROOT/'project/state.db')) as connection:
                assert connection.execute('SELECT COUNT(*) FROM wf_attempts').fetchone()[0]==0
                effects=connection.execute('SELECT operation_id,status FROM wf_publications').fetchall()
            assert effects and all(status=='applied' and operation.startswith('controls:'+outcome.run_id+':initialize:') for operation,status in effects)
        ''')

    def test_pipeline_init_uses_the_durable_plan_without_native_reservation(self):
        self.run_case(r'''
            from _workflow.controller import Controller
            from contextlib import closing
            import sqlite3
            import os
            original=b'---\r\nwiki_name: SyntheticWiki\r\nextends: canonical-schema.md\r\nextends_version: "3.0"\r\ndomains: [science]\r\ntopics: {science: [alpha]}\r\nindex_file: index.md\r\n---\r\nHuman schema outside bytes.\r\n'
            (VAULT/'.wiki-schema.md').write_bytes(original)
            protected=note('index.md','Protected catalog.',{'human_edited':True})
            raw=note('raw/documents/source.bin','Invented immutable source bytes.')
            before={str(path):path.read_bytes() for path in (protected,raw)}
            vault_argument=str(VAULT).swapcase() if os.name=='nt' else str(VAULT)
            outcome=Controller(w.build_workflow,skills_root=SKILLS).run(request('wiki-ingest','--init','--vault',vault_argument))
            assert outcome.status=='done',outcome.error
            assert (VAULT/'.wiki-schema.md').read_bytes().startswith(original)
            assert before=={str(path):path.read_bytes() for path in (protected,raw)}
            plan_ref=next(ref for ref in outcome.outputs if ref['name']=='control-plan.json')
            envelope=json.loads(Path(plan_ref['path']).read_text(encoding='utf-8'))
            plan=w.vault_controls.hydrate_control_plan(envelope)
            w.vault_controls.verify_controls(plan,envelope=envelope)
            with closing(sqlite3.connect(ROOT/'project/state.db')) as connection:
                assert connection.execute('SELECT COUNT(*) FROM wf_attempts').fetchone()[0]==0
                effects=connection.execute('SELECT operation_id,status FROM wf_publications').fetchall()
            assert all(status=='applied' and operation.startswith('controls:'+outcome.run_id+':initialize:') for operation,status in effects)
        ''')

    def test_pipeline_init_rechecks_protected_guards_after_each_effect(self):
        self.run_case(r'''
            from _workflow.controller import Controller
            from _workflow.publication import PublicationManager
            from unittest.mock import patch
            from contextlib import closing
            import sqlite3
            (VAULT/'.wiki-schema.md').write_bytes(b'---\nwiki_name: SyntheticWiki\nextends: canonical-schema.md\nextends_version: "3.0"\ndomains: [science]\ntopics: {science: [alpha]}\nindex_file: index.md\n---\n')
            protected=note('index.md','Original human catalog.',{'human_edited':True})
            human_edit=b'---\nhuman_edited: true\n---\nConcurrent human correction.\n'
            original_publish=PublicationManager.publish_file
            def crossing(manager,run_id,attempt_id,candidate,target,*args,**kwargs):
                receipt=original_publish(manager,run_id,attempt_id,candidate,target,*args,**kwargs)
                if Path(target).name=='.wiki-schema.md':
                    protected.write_bytes(human_edit)
                return receipt
            with patch.object(PublicationManager,'publish_file',crossing):
                outcome=Controller(w.build_workflow,skills_root=SKILLS).run(request('wiki-ingest','--init','--vault',str(VAULT)))
            assert outcome.status=='blocked',outcome.error
            assert outcome.error['code']=='CONTROL_PREIMAGE_CONFLICT',outcome.error
            assert protected.read_bytes()==human_edit
            with closing(sqlite3.connect(ROOT/'project/state.db')) as connection:
                effects=connection.execute('SELECT target,status FROM wf_publications').fetchall()
                assert effects==[(str(VAULT/'.wiki-schema.md'),'applied')],effects
                assert connection.execute('SELECT COUNT(*) FROM wf_attempts').fetchone()[0]==0
        ''')

    def test_pipeline_control_plan_seeds_linked_recovery_after_a_journalled_effect(self):
        self.run_case(r'''
            from _workflow.controller import Controller
            from _workflow.publication import PublicationManager
            from unittest.mock import patch
            from contextlib import closing, redirect_stdout
            from io import StringIO
            import sqlite3
            (VAULT/'.wiki-schema.md').write_bytes(b'---\nwiki_name: SyntheticWiki\nextends: canonical-schema.md\nextends_version: "3.0"\ndomains: [science]\ntopics: {science: [alpha]}\nindex_file: index.md\n---\n')
            original_publish=PublicationManager.publish_file
            def interruption(manager,run_id,attempt_id,candidate,target,*args,**kwargs):
                receipt=original_publish(manager,run_id,attempt_id,candidate,target,*args,**kwargs)
                if Path(target).name=='.wiki-schema.md':
                    raise RuntimeError('Interrupted after a journalled schema effect')
                return receipt
            with patch.object(PublicationManager,'publish_file',interruption):
                outcome=Controller(w.build_workflow,skills_root=SKILLS).run(request('wiki-ingest','--init','--vault',str(VAULT)))
            assert outcome.status=='blocked',outcome.error
            with closing(sqlite3.connect(ROOT/'project/state.db')) as connection:
                rows=connection.execute("SELECT payload_json FROM wf_events WHERE run_id=? AND event_type='local_output_validated' ORDER BY event_id DESC",(outcome.run_id,)).fetchall()
                payload=next(json.loads(row[0]) for row in rows if json.loads(row[0]).get('step_id')=='initialize')
                effects=connection.execute('SELECT target,status FROM wf_publications').fetchall()
            assert effects==[(str(VAULT/'.wiki-schema.md'),'applied')],effects
            predecessor=Path(next(ref['path'] for ref in payload['artifacts'] if ref['name']=='control-plan.json'))
            old_plan=predecessor.read_bytes()
            adopted_schema=(VAULT/'.wiki-schema.md').read_bytes()
            recovery_request=ROOT/'recovery-request.json'
            recovery_request.write_text(json.dumps({'schema_version':1,'selection_key':None,'vault_root':str(VAULT),'project_root':str(ROOT/'recovery-project'),'overrides':{}}),encoding='utf-8')
            successor=ROOT/'successor-plan.json'
            output=StringIO()
            with redirect_stdout(output):
                exit_code=w.vault_controls.main(['plan','--request',str(recovery_request),'--out',str(successor),'--predecessor-plan',str(predecessor)])
            assert exit_code==0,output.getvalue()
            envelope=json.loads(successor.read_bytes())
            assert envelope['predecessor_plan_ref']=={'path':str(predecessor),'sha256':sha256(old_plan).hexdigest()}
            assert envelope['plan']['changes'] and all(item['path']!='.wiki-schema.md' for item in envelope['plan']['changes'])
            assert (VAULT/'.wiki-schema.md').read_bytes()==adopted_schema
            assert predecessor.read_bytes()==old_plan and not (ROOT/'recovery-project/state.db').exists()
            unbound=ROOT/'unbound-predecessor.json'
            unbound.write_bytes(old_plan)
            output=StringIO()
            with redirect_stdout(output):
                exit_code=w.vault_controls.main(['plan','--request',str(recovery_request),'--out',str(ROOT/'unbound-successor.json'),'--predecessor-plan',str(unbound)])
            assert exit_code==2 and json.loads(output.getvalue())['error']['code']=='CONTROL_PLAN_INVALID',output.getvalue()
            assert not (ROOT/'unbound-successor.json').exists() and predecessor.read_bytes()==old_plan
        ''')

    def test_archive_requires_intent_and_reconciles_exact_observed_response_without_repost(self):
        self.run_case(_ARCHIVE_FIXTURE + dedent(r'''
            with archive_fixture('success') as (c, operation, store, calls):
                try: w.execute_archive(c,store,operation)
                except w.DomainError as exc: assert exc.code=='EXTERNAL_EFFECT_REFUSED'
                else: raise AssertionError('An unjournalled POST was permitted')
                assert calls==[]
                store.append_event(c.run_id,None,'external_effect_authorized',{'operation_id':operation['operation_id'],'operation':operation})
                result=w.execute_archive(c,store,operation)
                assert result['status']=='completed' and result['archive_url'].endswith(operation['source_url'])
                reconciled=w.execute_archive(replace(c,data={**c.data,'effect_mode':'reconcile'}),store,operation)
                assert reconciled['status']=='completed' and reconciled['archive_url']==result['archive_url']
                assert [call[0] for call in calls]==['POST','GET']
                assert calls[0][2]==operation['operation_id']
                try: w.execute_archive(c,store,operation)
                except w.DomainError as exc: assert exc.code=='EXTERNAL_EFFECT_REFUSED'
                else: raise AssertionError('An observed operation was posted twice')
                assert [call[0] for call in calls]==['POST','GET']
        '''))

    def test_unknown_archive_outcome_cannot_become_local_success_or_another_post(self):
        self.run_case(_ARCHIVE_FIXTURE + dedent(r'''
            for mode in ('server-error','unbound-location'):
                with archive_fixture(mode) as (c, operation, store, calls):
                    store.append_event(c.run_id,None,'external_effect_authorized',{'operation_id':operation['operation_id'],'operation':operation})
                    result=w.execute_archive(c,store,operation)
                    assert result['status']=='unknown' and result['archive_url'] is None
                    reconciled=w.execute_archive(replace(c,data={**c.data,'effect_mode':'reconcile'}),store,operation)
                    assert reconciled['status']=='unknown' and reconciled['archive_url'] is None
                    assert [call[0] for call in calls]==['POST']
        '''))

    def test_definite_archive_refusal_is_advisory_but_disabled_archiving_has_no_request(self):
        self.run_case(_ARCHIVE_FIXTURE + dedent(r'''
            with archive_fixture('forbidden') as (c, operation, store, calls):
                store.append_event(c.run_id,None,'external_effect_authorized',{'operation_id':operation['operation_id'],'operation':operation})
                result=w.execute_archive(c,store,operation)
                assert result['status']=='failed' and result['advisory']=='REMOTE_ARCHIVE_UNAVAILABLE' and result['http_status']==403
                assert [call[0] for call in calls]==['POST']
            with archive_fixture('success',outbound=False) as (c, operation, store, calls):
                assert operation is None and calls==[]
                assert not w.prepare_archive(c).local_result['data']['external_actions']
        '''))

    def test_extractor_temporary_outputs_stay_inside_capture_authority(self):
        self.run_case(r'''
            import os
            c=context('L1.acquire','acquire')
            c.candidate_dir.mkdir(parents=True)
            script=SKILLS/'wiki/scripts/synthetic_asset.py'
            script.write_text('import json,tempfile\nfrom pathlib import Path\np=Path(tempfile.mkdtemp())/"asset.bin"\np.write_bytes(b"owned asset")\nprint(json.dumps({"asset":str(p)}))\n',encoding='utf-8')
            before={key:os.environ.get(key) for key in ('TEMP','TMP','TMPDIR')}
            result=w._extractor(c,'synthetic_asset.py',[])
            asset=Path(result['asset'])
            assert asset.is_relative_to(c.candidate_dir),asset
            assert asset.read_bytes()==b'owned asset'
            assert before=={key:os.environ.get(key) for key in before}
        ''')

    def test_book_chapters_and_cover_retain_capture_authority(self):
        self.run_case(r'''
            from unittest.mock import patch
            source=ROOT/'invented.mobi'; source.write_bytes(b'Invented immutable book container')
            acquired=context('L1.acquire','acquire',data={'options':{'transcript':None}})
            body='Chapter IV: Conditions\nAlpha starts at10 only after conditionQ.\n'
            cover_bytes=b'\xff\xd8\xffSynthetic retained cover'
            def extracted(*args):
                directory=acquired.candidate_dir/'extractor-temp';directory.mkdir()
                cover=directory/'cover.jpg';cover.write_bytes(cover_bytes)
                return {'title':'Atlas','author':'Casey','cover_image_path':str(cover),'chapters':[{'number':4,'title':'Chapter IV: Conditions','content_markdown':body}]}
            with patch.object(w,'_extractor',side_effect=extracted):
                inventory,raws,images,diagnostics=w._capture_sources(acquired,[{'source':str(source),'source_class':'book'}])
            raw=raws[0]
            assert raw['original_locator']['content_path']==raw['path']
            assert Path(raw['path']).read_text(encoding='utf-8')==body
            assert raw['original_locator']['chapter']==4
            assert inventory['expected_units'][0]['original_locator']['content_path']==raw['path']
            scope_context=context('capture.scope','capture_scope',data={'capture_root':str(acquired.candidate_dir),'captured_raws':raws,'image_inputs':images,'inventory':inventory})
            scoped=w._capture_scope_handler(scope_context).local_result
            c=context('L1.extract','extract',data={'options':{'title':None},'topic':'alpha','domain':'science','topic_slug':'alpha','planned_date':'2026-01-01','capture_root':str(acquired.candidate_dir),**w._plain(scoped['data']),'step_data':{'capture.scope':[w._plain(scoped)]}})
            task=w._extract_handler(c).tasks[0]
            inputs={ref.path:ref.sha256 for ref in task.inputs}
            assert inputs[raw['original_locator']['content_path']]==sha256(body.encode()).hexdigest()
            cover_path=raw['metadata']['cover_image_path']
            assert inputs[cover_path]==sha256(cover_bytes).hexdigest()
            selected_inventory=next(ref for ref in task.inputs if Path(ref.path).name=='capture-scope.json')
            assert json.loads(Path(selected_inventory.path).read_text(encoding='utf-8'))==w._plain(scoped['data']['inventory'])
            assert not any(Path(selected_inventory.path).is_relative_to(Path(root)) for root in task.write_roots)
            assert Path(cover_path).is_relative_to(acquired.candidate_dir)
        ''')


_ARCHIVE_FIXTURE = r'''
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
from urllib.request import Request,urlopen as native_urlopen
from unittest.mock import patch
from _workflow.store import Store

@contextmanager
def archive_fixture(mode,outbound=True):
    make_schema(outbound_archiving=outbound)
    captured=ROOT/('capture-'+mode+str(outbound));captured.mkdir()
    snapshot=captured/'original.html';snapshot.write_bytes(b'<p>Invented source: only after Q; exact cost20.</p>')
    source='https://8.8.8.8/synthetic-archive-fixture'
    sid=provenance.source_id({'kind':'canonical-reference','id':'synthetic:archive:'+mode})
    digest=w._hash(snapshot)
    expected=w.capture.enumerate_snapshot_units([{**w._ref(snapshot),'source_id':sid,'kind':'section'}],captured)
    raw={**w._ref(snapshot),'source_id':sid,'unit_id':expected[0]['unit_id'],'source_class':'web','metadata':{'source_reference':source,'snapshot_media_type':'text/html','local_archive_ref':'local:archive/web/'+sid+'/'+digest+'.html'}}
    c=context('L1.archive','archive',data={'capture_root':str(captured),'captured_raws':[raw],'inventory':{'expected_units':expected},'effect_mode':'execute'})
    c=replace(c,request=request('wiki-ingest',source,'--vault',str(VAULT)))
    operations=w.prepare_archive(c).local_result['data']['external_actions']
    operation=w._plain(operations[0]) if operations else None
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append(('POST',self.path,self.headers.get('X-Archive-Operation-Id')))
            self.send_response(403 if mode=='forbidden' else 500 if mode=='server-error' else 200)
            if mode=='success': self.send_header('Content-Location','/web/20261017000000/'+source)
            elif mode=='unbound-location': self.send_header('Content-Location','/web/20261017000000/https://8.8.8.8/different-source')
            self.end_headers();self.wfile.write(b'Loopback-only archive response')
        def do_GET(self):
            calls.append(('GET',self.path,None))
            self.send_response(200);self.end_headers();self.wfile.write(snapshot.read_bytes())
        def log_message(self,*args): pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    def loopback_archive(request,**kwargs):
        assert request.full_url.startswith('https://web.archive.org/'),request.full_url
        target='http://127.0.0.1:'+str(server.server_port)+request.full_url[len('https://web.archive.org'):]
        return native_urlopen(Request(target,data=request.data,headers=dict(request.header_items()),method=request.get_method()),**kwargs)
    store=Store(ROOT/('archive-project-'+mode+str(outbound)));store.initialize()
    store.create_run(c.run_id,'synthetic-archive','a'*64,{},[],run_kind='inline')
    try:
        with store.acquire_owner(c.run_id), patch.object(w,'urlopen',side_effect=loopback_archive):
            yield c,operation,store,calls
    finally:
        store.close();server.shutdown();server.server_close();thread.join()
'''


if __name__ == '__main__':
    unittest.main()
