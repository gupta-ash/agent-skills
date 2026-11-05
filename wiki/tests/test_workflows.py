"""Source-only isolated domain regressions. No native execution or live fixtures.

These cases exercise decisions/data and declared counter identities; synthetic
review-shaped data below is a unit input, never qualification or a native receipt.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = Path(__file__).resolve().parents[2]


class WorkflowTests(unittest.TestCase):
    def run_case(self, code):
        with tempfile.TemporaryDirectory(prefix="wiki-domain-") as temporary:
            base = Path(temporary).resolve()
            skills = base / "skills"
            shutil.copytree(REPO / "_shared/workflow/src", skills / "_workflow")
            for skill in ("wiki", "wiki-compile", "obsidian"):
                shutil.copytree(REPO / skill, skills / skill, ignore=shutil.ignore_patterns("tests", "docs", "__pycache__"))
            fixture = base / "fixture"
            fixture.mkdir()
            environment = dict(os.environ)
            for key in list(environment):
                if key.startswith("VAULT_"):
                    del environment[key]
            for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR"):
                directory = base / key.lower()
                directory.mkdir()
                environment[key] = str(directory)
            bootstrap = f"import sys\nsys.path.insert(0,{str(skills)!r})\nfrom pathlib import Path\nROOT=Path({str(fixture)!r})\nSKILLS=Path({str(skills)!r})\n"
            script = fixture / "workflow-case.py"
            script.write_text(bootstrap + SETUP + textwrap.dedent(code), encoding="utf-8")
            result = subprocess.run([sys.executable, "-I", "-B", str(script)], cwd=fixture, env=environment, capture_output=True, text=True, timeout=45)
            if result.returncode == 77:
                self.skipTest(result.stdout.strip())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_hyphenated_compile_resource_loads_from_installed_sibling(self):
        self.run_case(r'''
            path=SKILLS/'wiki-compile/scripts/wiki_topic_match.py'
            matcher=w._module(path,'_public_wiki_topic_match_consumer')
            candidates=ROOT/'topic-candidates';candidates.mkdir()
            (candidates/'matching.md').write_text('Research methods preserve data lineage.\n',encoding='utf-8')
            (candidates/'unrelated.md').write_text('An unrelated example.\n',encoding='utf-8')
            result=matcher.match_topic('research-methods-data-lineage-evidence',candidates,mode='raws')
            assert Path(matcher.__file__).resolve()==path.resolve()
            assert result['count']==1 and [Path(value).name for value in result['files']]==['matching.md']
            assert not (ROOT/'project').exists()
        ''')

    def test_disjoint_argument_grammars_and_defaults(self):
        self.run_case(r'''
            options=w.parse_arguments(request('wiki-ingest','A condition Q','--topic','alpha','--domain','science'))
            assert options['depth']==1 and not options['clickup'] and not options['no_compile']
            for depth in ('0','1','2'):
                assert w.parse_arguments(request('wiki-ingest','body','--depth',depth))['depth']==int(depth)
            options=w.parse_arguments(request('wiki-reverify','--vault',str(VAULT)))
            assert options['freshness']=='monthly' and options['source_class']==[]
            for verb,args in [('wiki-content-check',['--vault',str(VAULT),'--all','--apply']),('wiki-content-check',['--vault',str(VAULT),'--all','--freshness','monthly']),('wiki-reverify',['--vault',str(VAULT),'operand']),('wiki-reverify',['--vault',str(VAULT),'--all']),('wiki-ingest',['source','--check']),('wiki-ingest',['source','--depth','3']),('wiki-strategize',['goal','--type','arbitrary'])]:
                try: w.parse_arguments(request(verb,*args))
                except w.DomainError as exc: assert exc.code=='INVALID_ARGUMENTS'
                else: raise AssertionError((verb,args))
        ''')

    def test_maintenance_requires_vault_and_selector(self):
        self.run_case(r'''
            for verb,args in [('wiki-reverify',[]),('wiki-content-check',['--all']),('wiki-content-check',['--vault',str(VAULT)])]:
                try: w.parse_arguments(request(verb,*args))
                except w.DomainError: pass
                else: raise AssertionError(verb)
            result=w.parse_arguments(request('wiki-reverify','--vault',str(VAULT),'--source-class','web','--source-class','pdf'))
            assert result['source_class']==['web','pdf']
        ''')

    def test_selector_union_uses_paths_and_detects_ambiguity(self):
        self.run_case(r'''
            note('wiki/alpha.md','[QUOTE] Alpha starts at10. ^[[[raw-a]]]\n')
            note('wiki/nested/beta.md','[PARAPHRASE] Example cost20. ^[[[raw-b]]]\n')
            selector=ROOT/'slugs.txt'; selector.write_text('# comment\nalpha\nmissing\n',encoding='utf-8')
            options=w.parse_arguments(request('wiki-content-check','--vault',str(VAULT),'--slugs','alpha','--slug-file',str(selector),'--wildcard','b*'))
            pages,warnings,refs=w.select_pages(resolved(),options)
            assert [p['slug'] for p in pages]==['alpha','beta']
            assert warnings==[{'code':'SLUG_NOT_FOUND','slug':'missing'}]
            assert refs[0]['sha256']==w._hash(selector)
            note('wiki/other/alpha.md','Another Alpha.')
            try: w.select_pages(resolved(),options)
            except w.DomainError as exc: assert exc.code=='AMBIGUOUS_SLUG'
            else: raise AssertionError('ambiguous basename silently selected')
        ''')

    def test_claim_scope_masks_code_comments_and_preserves_offsets(self):
        self.run_case(r'''
            text='---\ntitle: "[QUOTE] metadata"\n---\n# Alpha\n`[QUOTE] inline`\n```text\n[PARAPHRASE] code\n```\n<!-- [INFERENCE] comment -->\n[QUOTE] Alpha requires conditionQ. ^[[[raw-a]]]\nContinuation retaining qualification.\n\n[PARAPHRASE] Exact cost20. ^[[[raw-b]]]\n'
            claims=w.extract_claims(text,'p1')
            assert len(claims)==2,claims
            assert claims[0]['raw_archive_ref']=='raw-a'
            assert 'Continuation retaining qualification.' in claims[0]['claim_text']
            assert text.encode()[claims[0]['body_offset']:].startswith(b'[QUOTE] Alpha')
            assert claims[1]['claim_id']=='p1-c002'
            assert claims[0]['claim_sha256']==sha256(claims[0]['claim_text'].encode()).hexdigest()
        ''')

    def test_claim_missing_raw_and_section_fallback_are_not_verification(self):
        self.run_case(r'''
            first=w.extract_claims('[QUOTE] No citation.\n','p')[0]
            assert first['raw_archive_ref'] is None
            second=w.extract_claims('[INFERENCE] Meaning.\n\n## Sources\n- ^[[[raw-a]]]\n','p')[0]
            assert second['raw_archive_ref']=='raw-a' and second['raw_archive_resolution']=='section-fallback'
        ''')

    def test_content_thresholds_and_exact_half_boundary(self):
        self.run_case(r'''
            p=page(10)
            def reports(values): return [dict(claim_verdict=v) for v in values]
            assert w.content_page_report(page(0),[])['score'] is None
            assert w.content_page_report(p,reports(['VERIFIED']*7+['WEAKENED']*3))['status']=='NEEDS_ATTENTION'
            assert w.content_page_report(p,reports(['VERIFIED']*8+['WEAKENED']*2))['status']=='VERIFIED'
            assert w.content_page_report(p,reports(['VERIFIED']*5+['INSUFFICIENT_DATA']*5))['status']=='INSUFFICIENT_DATA'
            assert w.content_page_report(p,reports(['VERIFIED']*9+['INSUFFICIENT_DATA']))['status']=='NEEDS_ATTENTION'
            for defect in ('REFUTED','FABRICATED'):
                assert w.content_page_report(p,reports(['VERIFIED']*9+[defect]))['status']=='NEEDS_ATTENTION'
            try: w.content_page_report(p,reports(['VERIFIED']*9))
            except w.DomainError as exc: assert exc.code=='CLAIM_ACCOUNTING_INVALID'
            else: raise AssertionError('missing claim accepted')
        ''')

    def test_tooling_failure_retains_claim_one_and_nulls_counters(self):
        self.run_case(r'''
            p=page(3)
            report=w.content_page_report(p,[{'claim_verdict':'VERIFIED','claim_id':'p-c001'}],tooling_failure=True,unreviewed=2)
            assert report['status']=='TOOLING_FAILURE' and report['score'] is None
            assert report['findings'][0]['claim_id']=='p-c001'
            for key in ('verified','weakened','refuted','fabricated','insufficient_data'):
                assert report['summary']['claims_'+key] is None
            assert report['summary']['claims_not_reviewed']==2
        ''')

    def test_content_fanin_rejects_duplicates_phantoms_and_stale_pages(self):
        self.run_case(r'''
            p=page(1); r=w.content_page_report(p,[{'claim_verdict':'VERIFIED'}])
            assert w.aggregate_content_report([p],[r])['verified']==1
            missing=w.aggregate_content_report([p],[])
            assert not missing['fan_in_complete'] and missing['missing_cc2_outputs']==['p']
            for records in ([r,r],[{**r,'page_id':'phantom'}],[{**r,'page_sha256':'0'*64}]):
                try: w.aggregate_content_report([p],records)
                except w.DomainError: pass
                else: raise AssertionError('invalid fanin accepted')
        ''')

    def test_zero_claim_run_persists_insufficient_report_without_refreshing_knowledge(self):
        self.run_case(r'''
            from _workflow.controller import Controller
            path=note('wiki/no-claims.md','Navigation only.\n',{'type':'compilation','artifact_role':'synthesis','title':'No claims','domain':'science','last_verified':'2020-01-01'})
            before=path.read_bytes()
            def no_native(**kwargs):
                raise AssertionError('Zero claims must not dispatch native work')
            outcome=Controller(w.build_workflow,skills_root=SKILLS,adapter_factory=no_native).run(request('wiki-content-check','--vault',str(VAULT),'--slugs','no-claims'))
            assert outcome.status=='done',outcome.error
            report=json.loads(next(Path(ref['path']).read_text(encoding='utf-8') for ref in outcome.outputs if ref['name']=='content-check-report.json'))
            assert report['processed']==1 and report['verified']==0 and report['fan_in_complete']
            assert report['score'] is None and report['summary']['insufficient_data']==1
            assert report['pages'][0]['summary']['total_claims']==0
            assert path.read_bytes()==before
            manifest=json.loads((VAULT/'raw/_manifest.json').read_text(encoding='utf-8'))
            assert manifest['last_verified_at']=='2020-01-01T00:00:00Z'
        ''')

    def test_drift_strict_transcript_delta_and_bucket_precedence(self):
        self.run_case(r'''
            assert w.normalized_levenshtein('a'*20,'b'+'a'*19)==.05
            assert not (w.normalized_levenshtein('a'*20,'b'+'a'*19)<.05)
            assert w.normalized_levenshtein('a'*100,'b'+'a'*99)<.05
            assert w.normalized_levenshtein('a'*20,'bb'+'a'*18)>.05
            assert w.normalized_levenshtein('','')==0
            record={'source_class':'book','source_available':False,'backfill':True,'hash_match':False}
            assert w.drift_bucket(record)=='skipped_book'
            record['source_class']='web'; assert w.drift_bucket(record)=='source_unavailable'
            record['source_available']=True; assert w.drift_bucket(record)=='backfilled'
            record['backfill']=False; assert w.drift_bucket(record)=='drift_detected'
            record['hash_match']=True; assert w.drift_bucket(record)=='clean'
            record['hash_match']=None; assert w.drift_bucket(record)=='source_unavailable'
        ''')

    def test_compile_zero_one_two_raw_boundary_and_legacy_three(self):
        self.run_case(r'''
            for count in range(4):
                if count:
                    note('raw/documents/alpha-'+str(count)+'.md','Alpha detailed evidence '+str(count),{'type':'source','source_class':'document','topic':'alpha'})
                try: data=w.preflight(request('wiki-compile','science: alpha','--vault',str(VAULT)))
                except w.DomainError as exc:
                    assert count<3 and exc.code in ('RECOMMEND_INGEST','RECOMMEND_REINGEST')
                else:
                    assert count==3 and len(data['legacy_items'])==3 and not data['accepted_records']
        ''')

    def test_compile_graph_has_separate_l2_l3_and_shared_identity(self):
        self.run_case(r'''
            for n in range(3): note(f'raw/documents/alpha-{n}.md','Alpha condition Q.',{'type':'source','source_class':'document','topic':'alpha'})
            graph=w.build_workflow(request('wiki-compile','science: alpha','--vault',str(VAULT)))
            by={s.step_id:s for s in graph.steps}
            assert by['L2.review'].kind=='review'
            review_steps=[s for s in graph.steps if s.step_id.startswith('L3.review')]
            assert len(review_steps)==3
            identities={s.policy['artifact_id'] for s in [by['L2.review'],*review_steps]}
            assert identities=={'compile:{run_id}:output'}
            assert len([s for s in graph.steps if s.step_id.startswith('L3.verify')])==3
            assert len([s for s in graph.steps if s.step_id.startswith('L3.repair')])==2
            assert by['L4.publish'].dependencies==('L3.accepted',)
            assert all(s.prepare_handler is w.prepare_step for s in graph.steps)
        ''')

    def test_ingest_graph_checkpoint_precedes_conditional_synthesis(self):
        self.run_case(r'''
            source=ROOT/'source.txt'; source.write_text('Alpha starts at10 only after conditionQ.',encoding='utf-8')
            graph=w.build_workflow(request('wiki-ingest',str(source),'--topic','alpha','--domain','science','--vault',str(VAULT),'--no-compile'))
            steps={s.step_id:s for s in graph.steps}
            assert steps['L3.notes'].concurrency==4
            assert steps['L3.notes'].fanout_source=='L3.items.items'
            assert steps['L6.select'].dependencies==('checkpoint',)
            assert steps['checkpoint'].kind=='publish'
            assert len([s for s in graph.steps if s.step_id.startswith('L4L5.repair')])==3
            assert {s.policy['artifact_id'] for s in graph.steps if s.step_id.startswith('L4L5.review')}=={'ingest:{run_id}:remediation-verdicts'}
            assert steps['L1.review'].policy['artifact_id']=='ingest:{run_id}:extraction'
            assert steps['L2.review'].policy['artifact_id']=='ingest:{run_id}:index'
            assert steps['L3.review'].policy['artifact_id']=='ingest:{run_id}:note:{item_id}'
        ''')

    def test_content_graph_fixed_claim_allowance_and_sequential_page_chain(self):
        self.run_case(r'''
            note('wiki/alpha.md','[QUOTE] A ^[[[raw-a]]]\n\n[PARAPHRASE] B ^[[[raw-b]]]\n\n[INFERENCE] C ^[[[raw-c]]]\n')
            graph=w.build_workflow(request('wiki-content-check','--vault',str(VAULT),'--all'))
            reviews=[s for s in graph.steps if s.kind=='review']
            assert len(reviews)==3
            assert len({s.policy['artifact_id'] for s in reviews})==3
            for s in reviews:
                assert s.policy['max_full_reviews']==1 and s.policy['max_repair_rounds']==0
                assert s.policy['allow_review_fail'] is True
            evaluations=[s for s in graph.steps if s.policy['handler']=='claim_evaluate']
            assert evaluations[1].dependencies==(reviews[0].step_id,)
            assert evaluations[2].dependencies==(reviews[1].step_id,)
            assert graph.metadata['max_concurrency']==4
            assert next(s for s in graph.steps if s.step_id=='CC1').concurrency==8
        ''')

    def test_content_graph_snapshots_exact_page_and_raw_inputs(self):
        self.run_case(r'''
            note('wiki/alpha.md','[QUOTE] A ^[[[raw-a]]]\n')
            note('raw/documents/raw-a.md','A',{'type':'source','source_class':'document'})
            graph=w.build_workflow(request('wiki-content-check','--vault',str(VAULT),'--all'))
            inputs={r['path']:r['sha256'] for r in graph.metadata['inputs']}
            assert str(VAULT/'wiki/alpha.md') in inputs and str(VAULT/'raw/documents/raw-a.md') in inputs
            (VAULT/'wiki/alpha.md').write_text('changed',encoding='utf-8')
            assert inputs[str(VAULT/'wiki/alpha.md')]!=w._hash(VAULT/'wiki/alpha.md')
        ''')

    def test_evidence_only_strategy_refuses_before_graph_or_task(self):
        self.run_case(r'''
            make_schema(tiers=['A','B'])
            try: w.build_workflow(request('wiki-strategize','science: goal','--type','plan','--vault',str(VAULT)))
            except w.DomainError as exc: assert exc.code=='TIER_C_PROHIBITED'
            else: raise AssertionError('Tier C dispatched')
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_strategy_raw_override_does_not_promote_distillation(self):
        self.run_case(r'''
            note('wiki/alpha.md','Alpha.',{'type':'compilation','artifact_role':'topic_distill'})
            for args in ([],['--allow-raw-only']):
                try: w.preflight(request('wiki-strategize','science: alpha','--vault',str(VAULT),*args))
                except w.DomainError as exc: assert exc.code=='RECOMMEND_COMPILE'
                else: raise AssertionError('distillation masquerades as accepted synthesis')
        ''')

    def test_preview_has_no_database_candidate_network_or_audit_mutation(self):
        self.run_case(r'''
            source=ROOT/'source.txt'; source.write_text('Alpha after Q.',encoding='utf-8')
            before={str(p):p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
            result=w.preview(request('wiki-ingest',str(source),'--topic','alpha','--domain','science','--vault',str(VAULT),'--dry-run','--clickup'))
            assert result['status']=='preview' and result['plan']['clickup']=='planned only'
            after={str(p):p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
            assert before==after and not (ROOT/'project').exists()
            graph=w.build_workflow(request('wiki-reverify','--vault',str(VAULT),'--dry-run'))
            assert graph.metadata['preview']['network']=='not performed'
        ''')

    def test_local_publication_intent_does_not_touch_target(self):
        self.run_case(r'''
            target=VAULT/'verify-log.md'; before=target.read_bytes()
            c=context('test.local','audit',data={'vault_root':str(VAULT)})
            intent={'kind':'replace','target':str(target),'expected_sha256':w._hash(target),'candidate_path':str(c.candidate_dir/'candidate.md')}
            result=w._local(c,{'publications':[intent]},{'candidate.md':b'new private candidate'})
            assert target.read_bytes()==before
            effect=result.local_result['data']['publications'][0]
            assert effect['root']==str(VAULT)
            assert result.local_result['artifacts'][0]['sha256']==sha256(b'new private candidate').hexdigest()
        ''')

    def test_human_reference_protection_is_not_overridden_by_region(self):
        self.run_case(r'''
            target=VAULT/'_hub.md'
            target.write_text('---\nhuman_edited: true\n---\n<!-- ash_wiki:begin navigation -->\nManual\n<!-- ash_wiki:end navigation -->\n',encoding='utf-8')
            c=context('navigation','navigation',data={'vault_root':str(VAULT),'schema_sha256':resolved().constraints_ref.sha256,'accepted_checkpoint':[]})
            c=replace(c,request=replace(c.request,workflow='wiki-ingest'))
            prepared=w.prepare_step(c)
            assert prepared.status=='local_result'
            assert prepared.local_result['data']['protected_skips'][0]['status']=='skipped_protected'
            assert b'Manual' in target.read_bytes()
        ''')

    def test_book_drift_skip_performs_no_fetch_or_snapshot(self):
        self.run_case(r'''
            path=note('raw/books/alpha.md','Original book.',{'type':'source','source_class':'book','content_hash':'sha256:'+'1'*64})
            raw={**w._ref(path),'item_id':'book-a'}
            c=context('L1.refetch','drift',data={'item':raw,'options':{'backfill_mode':False},'vault_root':str(VAULT)})
            result=w.prepare_step(c)
            assert result.status=='local_result'
            record=result.local_result['data']['diff']
            assert record['hash_match']=='skip' and not record.get('snapshot_ref')
        ''')

    def test_local_document_drift_compares_source_not_body_hash(self):
        self.run_case(r'''
            source=ROOT/'binary.dat'; source.write_bytes(b'actual source bytes')
            baseline=provenance.source_hash(source.read_bytes(),'bytes')
            path=note('raw/documents/alpha.md','Different archived note body.',{'type':'source','source_class':'document','source_document_local':str(source),'content_hash':'sha256:'+'9'*64,**baseline})
            raw={**w._ref(path),'item_id':'raw-a'}
            c=context('L1.refetch','drift',data={'item':raw,'options':{'backfill_mode':False},'vault_root':str(VAULT)})
            result=w.prepare_step(c)
            assert result.local_result['data']['diff']['hash_match'] is True
            source.write_bytes(b'changed bytes')
            c=context('L1.refetch','drift',data={'item':raw,'options':{'backfill_mode':False},'vault_root':str(VAULT)})
            result=w.prepare_step(c)
            assert result.local_result['data']['diff']['hash_match'] is False
        ''')

    def test_missing_baseline_never_silently_rebaselines(self):
        self.run_case(r'''
            source=ROOT/'source.txt'; source.write_text('A',encoding='utf-8')
            path=note('raw/documents/alpha.md','A',{'type':'source','source_class':'document','source_document_local':str(source),'content_hash':'sha256:'+'1'*64})
            c=context('L1.refetch','drift',data={'item':{**w._ref(path),'item_id':'raw-a'},'options':{'backfill_mode':True},'vault_root':str(VAULT)})
            before=path.read_bytes(); result=w.prepare_step(c)
            diff=result.local_result['data']['diff']
            assert diff['baseline_status']=='BASELINE_UNAVAILABLE' and diff['hash_match'] is None
            assert path.read_bytes()==before
        ''')

    def test_consolidated_all_pass_fast_path_does_not_prepare_review(self):
        self.run_case(r'''
            c=context('L4L5.consolidate1','consolidate',policy={'sequence':'L4L5','round_number':1,'source_step':'L4L5.verify2'},data={'step_data':{'L4L5.repair1':[{'item_id':'a','status':'skipped','data':{},'artifacts':[]}]}})
            result=w.prepare_step(c)
            assert result.status=='skip'
        ''')

    def test_per_item_pass_does_not_skip_failed_sibling(self):
        self.run_case(r'''
            c=context('L4L5.repair1','repair',fanout='L3.items.items',item_id='b',data={'step_data':{'L4L5.verify1':[{'item_id':'a','status':'done','data':{'verification':'PASS'},'artifacts':[]},{'item_id':'b','status':'done','data':{'verification':'FAIL'},'artifacts':[]}]}})
            assert not w._sequence_done(c,'L4L5')
            a=replace(c,item_id='a'); assert w._sequence_done(a,'L4L5')
        ''')

    def test_page_outcome_fails_after_durable_report_not_clean_done(self):
        self.run_case(r'''
            c=context('CC3.outcome','report_outcome',data={'report':{'fan_in_complete':True,'tooling_failures':['p']}})
            result=w.prepare_step(c)
            assert result.status=='local_result' and result.local_result['data']['run_status']=='failed'
            assert result.local_result['data']['error']['code']=='CONTENT_CHECK_TOOLING_FAILURE'
        ''')


    def test_lifecycle_projection_preserves_utf8_crlf_and_all_other_bytes(self):
        self.run_case(r'''
            c=context('capture.lifecycle','raw_lifecycle')
            c.candidate_dir.mkdir(parents=True)
            original=ROOT/'H0.md'
            before='---\r\ntitle: Café\r\nstatus: draft # retained comment\r\nlast_verified: null\r\ncustom: "[unknown]"\r\n---\r\n[QUOTE] Alpha starts at10 only after conditionQ.\r\n'.encode('utf-8')
            original.write_bytes(before)
            source={**w._ref(original,'source-unit'),'attempt_id':'synthetic-byte-test-only'}
            # This checks source byte construction, NOT native admission/qualification.
            _,projection,h1=w._project_lifecycle(c,source,'unit-projection','2026-10-17T01:02:03.004Z',[])
            after=Path(h1['path']).read_bytes()
            assert original.read_bytes()==before and h1['sha256']==sha256(after).hexdigest()
            rebuilt=b''; cursor=0
            for edit in projection['edits']:
                assert before[edit['start']:edit['end']].hex()==edit['before_hex']
                assert b'\n' not in bytes.fromhex(edit['after_hex']) and b'\r' not in bytes.fromhex(edit['after_hex'])
                rebuilt+=before[cursor:edit['start']]+bytes.fromhex(edit['after_hex']); cursor=edit['end']
            rebuilt+=before[cursor:]
            assert rebuilt==after
            assert before.split(b'---\r\n',2)[2]==after.split(b'---\r\n',2)[2]
            assert b'title: Caf\xc3\xa9\r\n' in after and b'# retained comment' in after
            proof=json.loads(Path(h1['projection_ref']['path']).read_text(encoding='utf-8'))
            assert proof['changed_fields']==['status','last_verified'] and proof['accepted_at']=='2026-10-17T01:02:03.004Z'
            metadata,_=w._metadata(h1['path'])
            assert metadata['status']=='published' and metadata['last_verified']==proof['accepted_at']
            assert h1['native_producer_ref']['sha256']==source['sha256'] and 'attempt_id' not in h1
        ''')

    def test_lifecycle_projection_refuses_alias_missing_fields_and_false_earned_state(self):
        self.run_case(r'''
            c=context('capture.lifecycle','raw_lifecycle'); c.candidate_dir.mkdir(parents=True)
            for index,header in enumerate(['status: draft\nlast_verified: &clock null\nother: *clock\n','status: draft\n','status: published\nlast_verified: null\n','status: draft\nstatus: draft\nlast_verified: null\n']):
                source=ROOT/f'bad-{index}.md'; source.write_text('---\n'+header+'---\nBody stays.\n',encoding='utf-8')
                ref={**w._ref(source,'source'),'attempt_id':'synthetic-only'}
                try: w._project_lifecycle(c,ref,'bad-'+str(index),'2026-10-17T01:02:03Z',[])
                except w.DomainError as exc: assert exc.code.startswith('LIFECYCLE_')
                else: raise AssertionError('unsafe metadata projected')
        ''')

    def test_earned_timestamp_uses_observed_gate_completion_not_generation(self):
        self.run_case(r'''
            rows={'capture.fidelity':[{'item_id':'a','status':'done','data':{'completed_at':2000,'settled_at':1900},'artifacts':[]}],'L1.review':[{'item_id':'a','status':'done','data':{'completed_at':1000},'artifacts':[]}]}
            c=context('capture.lifecycle','raw_lifecycle',data={'step_data':rows})
            assert w._earned_at(c,['capture.fidelity','L1.review'])=='1970-01-01T00:00:02.000Z'
            del rows['capture.fidelity'][0]['data']['completed_at']; del rows['capture.fidelity'][0]['data']['settled_at']
            c=context('capture.lifecycle','raw_lifecycle',data={'step_data':rows})
            try: w._earned_at(c,['capture.fidelity'])
            except w.DomainError as exc: assert exc.code=='EARNED_TIME_UNAVAILABLE'
            else: raise AssertionError('generation/wall clock substituted')
        ''')

    def test_native_review_wire_has_real_focus_file_and_exact_input_refs(self):
        self.run_case(r'''
            source=note('wiki/alpha.md','[QUOTE] Alpha starts at10 only afterQ.\n')
            raw=note('raw/documents/raw-a.md','Alpha starts at10 only afterQ.\n',{'type':'source','source_class':'document'})
            c=context('L2.review','review')
            review=w._prepare_review(c,w._ref(source,'candidate'),[{**w._ref(raw),'unit_id':'u000001'}],'Review exactly this condition.',artifact_id='synthetic:output',tier='B',contract='vault-wiki')
            assert set(review)=={'artifact_id','artifact_tier','contract','source','focus','input_refs'}
            assert Path(review['focus']).is_file() and Path(review['focus']).is_absolute()
            focus=Path(review['focus']).read_text(encoding='utf-8')
            assert 'Alpha starts at10 only afterQ.' in focus and 'Review exactly this condition.' in focus
            assert all(set(ref)=={'path','sha256','media_type'} for ref in review['input_refs'])
            assert any(ref['path']==str(source) for ref in review['input_refs'])
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_distill_capture_scope_grants_exact_files_not_extraction_companions(self):
        self.run_case(r'''
            (ROOT/'project').mkdir()
            raw=note('raw/videos/raw-a.md','Alpha only after Q.\n',{'type':'source','source_class':'video'})
            item={'item_id':'u000001-alpha','unit_id':'u000001','source_id':'a'*64,
                  'raw':{**w._ref(raw),'destination':'raw/videos/raw-a.md'}}
            rows={}
            expected={str(raw):w._hash(raw)}
            for step,name in [('capture.scope','capture-scope.json'),('capture.fidelity','capture-verdict.json')]:
                path=ROOT/name; path.write_text(json.dumps({'retained':step}),encoding='utf-8')
                reference=w._ref(path,name); expected[str(path)]=reference['sha256']
                rows[step]=[{'item_id':'main','status':'done','data':{},'artifacts':[reference]}]
            excluded=[]
            for step,name in [('L1.extract','raw-note-map.json'),('L1.extract','capture-fidelity.json'),('L3.notes','other-distillation.md')]:
                path=ROOT/name; path.write_text('{}',encoding='utf-8'); excluded.append(path)
                rows.setdefault(step,[]).append({'item_id':'other','status':'done','data':{},'artifacts':[w._ref(path,name)]})
            c=context('L3.notes','distill',item_id=item['item_id'],data={'item':item,'step_data':rows,
                      'topic':'alpha','domain':'science','topic_slug':'alpha'})
            c=replace(c,request=request('wiki-ingest','synthetic-source','--vault',str(VAULT)))
            task=w._distill_handler(c).tasks[0]
            assert {ref.path:ref.sha256 for ref in task.inputs}==expected
            assert all(path not in map(Path,task.read_roots) for path in excluded)
            assert ROOT not in map(Path,task.read_roots)
            assert task.allowed_actions==('read','write')
            assert [output.name for output in task.outputs]==['distillation.md','citation-bindings.json','coverage.json']
            assert not (ROOT/'project/state.db').exists()
        ''')


    def test_index_canonical_citations_bind_current_private_raws_without_new_reads(self):
        self.run_case(r'''
            from wiki.runtime.citations import extract_citations
            c=book_context()
            plans,units=w._capture_plan(c)
            projected=[]
            for name,plan in plans.items():
                path=c.paths['run_root']/'attempts/native/candidate'/plan['relative_path']
                path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b'---\ntype: source\nartifact_role: capture\nbook_title: Atlas\n---\nCondition Q remains.\n')
                projected.append(w._ref(path,name))
            mapping=c.paths['run_root']/'attempts/native/candidate/raw-note-map.json'
            mapping.write_text(w._json({'units':units}),encoding='utf-8')
            captured_by_unit={raw['unit_id']:raw for raw in c.data['captured_raws']}
            expected_units=[{**unit,'original_locator':captured_by_unit[unit['unit_id']]['original_locator']} for unit in c.data['inventory']['expected_units']]
            c=replace(c,step=StepSpec('L2.index',(),'task',w.prepare_step,None,1,{'handler':'index','artifact_id':'ingest:{run_id}:index','artifact_tier':'B'}),
                paths={**c.paths,'attempt_id':'canonical-index','candidate_dir':c.paths['run_root']/'attempts/canonical-index/candidate'},
                data={**c.data,'source_type':'book','projected_raws':projected,
                    'inventory':{**c.data['inventory'],'expected_units':expected_units,'units':expected_units},
                    'step_data':{'L1.extract':[{'item_id':'main','status':'done','data':{},'artifacts':[w._ref(mapping,'raw-note-map.json')]}]}})
            protected={Path(ref['path']):Path(ref['path']).read_bytes() for ref in projected+list(c.data['captured_raws'])}
            prepared=w._index_handler(c);task=prepared.tasks[0]
            expected={(ref['path'],ref['sha256'],ref['media_type']) for ref in projected+list(c.data['captured_raws'])}
            assert {(ref.path,ref.sha256,ref.media_type) for ref in task.inputs}==expected
            assert str(c.paths['run_root']) not in task.read_roots
            metadata={'type':'moc','source_type':'book','title':'Atlas','domain':'science','topic':'alpha','vault':'SyntheticWiki',
                'author':'synthetic-generator','pipeline_id':c.run_id,'status':'draft','last_verified':None,'freshness':'permanent',
                'confidence':'high','human_edited':False,'tags':['domain/science','topic/alpha','type/moc','vault/SyntheticWiki']}
            body='# Atlas\n\n## Source Identity\n\n'
            for plan in plans.values():
                body+='[QUOTE] Condition Q remains. ^[[['+plan['destination'].removesuffix('.md')+']]]\n\n'
            text='---\n'+yaml.safe_dump(metadata,sort_keys=False)+'---\n'+body
            destination=Path(task.candidate_dir);destination.mkdir(parents=True)
            note_path=destination/'index.md';note_path.write_bytes(text.encode('utf-8'))
            bindings=[]
            for citation,ref in zip(extract_citations(text),projected):
                source=Path(ref['path']).read_text(encoding='utf-8');span='Condition Q remains.';start=source.index(span)
                claim_start=text.rfind('\n',0,citation.start)+1;claim_end=text.find('\n',citation.end)
                bindings.append({'citation_start':citation.start,'raw_path':ref['path'],'raw_sha256':ref['sha256'],
                    'start':start,'end':start+len(span),'span':span,'claim_start':claim_start,'claim_end':claim_end,
                    'claim_sha256':sha256(text[claim_start:claim_end].encode('utf-8')).hexdigest()})
            document={**{key:getattr(task,key) for key in ('run_id','step_id','item_id','attempt_id')},
                'task_sha256':task.semantic_sha256,'subjects':w._index_membership(c),'bindings':bindings}
            companion=destination/'index-map.json';companion.write_text(json.dumps(document),encoding='utf-8')
            saved=json.loads(w._json(w._plain(prepared.local_result)['data']))
            current=replace(c,data={**c.data,**saved,'task':task})
            valid=w.validate_artifact(task.outputs[0],note_path,current)
            assert valid.valid,valid.errors
            mapped=w.validate_artifact(task.outputs[1],companion,current)
            assert mapped.valid,mapped.errors
            wrong=[{**bindings[0],'raw_path':projected[1]['path'],'raw_sha256':projected[1]['sha256']},*bindings[1:]]
            companion.write_text(json.dumps({**document,'bindings':wrong}),encoding='utf-8')
            refused=w.validate_artifact(task.outputs[0],note_path,current)
            assert not refused.valid and any(error['code']=='CITATION_BINDING_INVALID' for error in refused.errors),refused.errors
            assert all(path.read_bytes()==content for path,content in protected.items())
            assert not (ROOT/'project/state.db').exists()
        ''')


    def test_original_index_repair_persists_exact_map_bindings_without_new_authority(self):
        self.run_case(r'''
            from _workflow.contracts import task_to_dict, task_from_dict, result_from_dict, result_to_dict, semantic_hash
            from wiki.runtime.citations import extract_citations
            (ROOT/'project').mkdir()
            c=context('L2.index','index',item_id='main',policy={'artifact_id':'ingest:{run_id}:index','artifact_tier':'B'})
            run_root=c.paths['run_root']
            raw=run_root/'attempts/capture/candidate/preface.md'; raw.parent.mkdir(parents=True)
            raw.write_bytes('---\ntype: source\nartifact_role: capture\nbook_title: Synthetic Atlas\n---\nPréface\r\n[QUOTE] Source-owned marker stays literal.\r\nCondition Q remains.\r\n'.encode('utf-8'))
            raw_ref=w._ref(raw)
            unit={'unit_id':'u000001','destination':'raw/books/synthetic-atlas/ch01-preface.md','original_locator':{'title':'Preface'},'original_sha256':raw_ref['sha256']}
            membership=[{'unit_id':unit['unit_id'],'path':unit['destination'],'original_locator':unit['original_locator']}]
            mapping=run_root/'attempts/capture/candidate/raw-note-map.json'; mapping.write_text(json.dumps({'units':[unit]}),encoding='utf-8')
            captured={**raw_ref,'unit_id':unit['unit_id'],'source_id':'a'*64,'source_class':'book','identity':{'kind':'canonical-reference','id':'synthetic:atlas'},'metadata':{'title':'Synthetic Atlas','author':'Synthetic Author'},'original_locator':unit['original_locator']}
            c=replace(c,request=request('wiki-ingest','synthetic book','--vault',str(VAULT)),
                paths={**c.paths,'attempt_id':'original-index','candidate_dir':run_root/'attempts/original-index/candidate'},
                data={**c.data,'source_type':'book','options':{'title':None},'topic':'alpha','domain':'science','topic_slug':'alpha',
                    'captured_raws':[captured],'projected_raws':[raw_ref],'inventory':{'expected_units':[unit],'units':[unit]},
                    'step_data':{'L1.extract':[{'item_id':'main','status':'done','data':{},'artifacts':[w._ref(mapping,'raw-note-map.json')]}]}})
            plans,_=w._capture_plan(c)
            projected=[]
            for name,plan in plans.items():
                if name.startswith('source-'):
                    projected.append(w._ref(raw,name))
                else:
                    archive=run_root/'attempts/capture/candidate/archive-full.md'
                    archive.write_bytes(raw.read_bytes())
                    projected.append(w._ref(archive,name))
            c=replace(c,data={**c.data,'projected_raws':projected})
            values=w._book_values(c,captured)
            target=w.schema.resolve_output_path(resolved(),'book_index',values)
            # The historical two-output producer had neither a binding-file
            # selection nor the private evidence root. Keep that old TaskSpec.
            previous=w._task(c,'produce','Produce the complete navigation index.',[w._output('index.md','text/markdown'),w._output('index-map.json')],
                inputs=[*projected,captured],artifact='ingest:'+c.run_id+':index',data={'output_type':'moc','artifact_role':None,'path_values':values,
                    'output_paths':{'index.md':target.relative_to(VAULT).as_posix()},'expected_index_map':membership,
                    'expected_subjects':[unit['unit_id']],'subject_field':'unit_id'})
            old=previous.tasks[0]
            metadata={'type':'moc','source_type':'book','title':'Synthetic Atlas','domain':'science','topic':'alpha','vault':'SyntheticWiki',
                'author':'synthetic-generator','pipeline_id':c.run_id,'status':'draft','last_verified':None,'freshness':'permanent',
                'confidence':'high','human_edited':False,'tags':['domain/science','topic/alpha','type/moc','vault/SyntheticWiki']}
            # The generated claims stay visible; source-owned raw markers stay untouched.
            body='# Synthetic Atlas\r\n\r\n## Source Identity\r\n\r\n[PARAPHRASE] The title is Synthetic Atlas. ^[[[preface]]]\r\n\r\n## Source Limitations\r\n\r\n[QUOTE] Condition Q remains. ^[[[preface]]]\r\n'
            markdown=('---\n'+yaml.safe_dump(metadata,sort_keys=False)+'---\n'+body).encode('utf-8')
            old_dir=Path(old.candidate_dir); old_dir.mkdir(parents=True)
            (old_dir/'index.md').write_bytes(markdown)
            (old_dir/'index-map.json').write_text(json.dumps({'subjects':membership,'claim_bindings':[{'location':'Source Identity'}]}),encoding='utf-8')
            task_path=old_dir.parent/'task.json'; task_path.write_text(w._json(task_to_dict(old))+'\n',encoding='utf-8')
            snapshots=old_dir.parent/'snapshots'; snapshots.mkdir(exist_ok=True)
            for resource in old.resources:
                retained=snapshots/resource.sha256
                if not retained.exists():
                    retained.write_bytes(Path(resource.path).read_bytes())
            artifacts=[{'name':output.name,'relative_path':output.relative_path,'media_type':output.media_type,'sha256':w._hash(old_dir/output.relative_path)} for output in old.outputs]
            result=result_from_dict({'schema_version':1,**{key:getattr(old,key) for key in ('run_id','step_id','item_id','attempt_id')},
                'task_sha256':old.semantic_sha256,'status':'completed','verification':None,'decision':None,'error':None,'evidence':[],'artifacts':artifacts})
            old_context=replace(c,data={**c.data,**w._plain(previous.local_result)['data'],'task':old,'task_result':result})
            errors=w.validate_artifact(old.outputs[0],old_dir/'index.md',old_context).errors
            assert [error['code'] for error in errors]==['CITATION_SUPPORT_MISSING']*2,errors
            candidates=[{'path':str(old_dir/output.relative_path),'sha256':artifact['sha256'],'media_type':output.media_type} for output,artifact in zip(old.outputs,artifacts)]
            diagnosis=old_dir.parent/'structural-rejection.json'
            diagnosis.write_text(w._json({'task_sha256':old.semantic_sha256,'result':result_to_dict(result),'errors':errors,'affected_candidates':candidates[:1]})+'\n',encoding='utf-8')
            repair={'kind':'candidate','original_task':w._ref(task_path),'original_candidates':candidates,'diagnostics':[w._ref(diagnosis)]}
            original_bytes={path:path.read_bytes() for path in (raw,task_path,diagnosis,old_dir/'index.md',old_dir/'index-map.json')}
            current=replace(c,paths={**c.paths,'attempt_id':'index-repair','candidate_dir':run_root/'attempts/index-repair/candidate'},
                data={**c.data,'task_context':w._plain(previous.local_result)['data']['task_context'],'source_continuation':repair})
            prepared=w._index_handler(current)
            task=prepared.tasks[0]
            assert task.duty=='repair' and task.outputs==old.outputs and task.allowed_actions==old.allowed_actions
            assert str(run_root) not in task.read_roots
            saved=json.loads(w._json(w._plain(prepared.local_result)['data']))
            resumed=replace(current,data={**current.data,**saved,'task':task})
            destination=Path(task.candidate_dir); destination.mkdir(parents=True)
            note_path=destination/'index.md'; note_path.write_bytes(markdown)
            text=markdown.decode('utf-8'); source=raw.read_bytes().decode('utf-8')
            bindings=[]
            for citation,span in zip(extract_citations(text),('book_title: Synthetic Atlas','Condition Q remains.')):
                claim_start=text.rfind('\n',0,citation.start)+1; claim_end=text.find('\n',citation.end)
                start=source.index(span)
                bindings.append({'citation_start':citation.start,'raw_path':str(raw),'raw_sha256':raw_ref['sha256'],
                    'start':start,'end':start+len(span),'span':span,'claim_start':claim_start,'claim_end':claim_end,
                    'claim_sha256':sha256(text[claim_start:claim_end].encode('utf-8')).hexdigest()})
            identity={**{key:getattr(task,key) for key in ('run_id','step_id','item_id','attempt_id')},'task_sha256':task.semantic_sha256}
            document={**identity,'subjects':membership,'bindings':bindings}
            companion=destination/'index-map.json'
            def validate_document(value):
                companion.write_text(json.dumps(value),encoding='utf-8')
                return w.validate_artifact(task.outputs[0],note_path,resumed)
            valid=validate_document(document)
            assert valid.valid,valid.errors
            mapped=w.validate_artifact(task.outputs[1],companion,resumed)
            assert mapped.valid,mapped.errors
            assert not (destination/'citation-bindings.json').exists()
            refused=validate_document({**identity,'subjects':membership,'claim_bindings':[{'location':'Source Identity'}]})
            assert not refused.valid and any(error['code']=='CITATION_SUPPORT_INVALID' for error in refused.errors),refused.errors
            for changed,code in (([], 'CITATION_SUPPORT_MISSING'),([bindings[0],bindings[0],bindings[1]],'CITATION_DUPLICATE_SUPPORT'),
                    ([{**bindings[0],'span':'fabricated'},bindings[1]],'RAW_SPAN_INVALID'),
                    ([{**bindings[0],'claim_sha256':'0'*64},bindings[1]],'CITATION_BINDING_INVALID')):
                refused=validate_document({**document,'bindings':changed})
                assert not refused.valid and code in {error['code'] for error in refused.errors},refused.errors
            sibling=run_root/'attempts/unrelated/candidate/preface.md'; sibling.parent.mkdir(parents=True); sibling.write_bytes(raw.read_bytes())
            assert not any(sibling.is_relative_to(Path(grant)) for grant in task.read_roots)
            refused=validate_document({**document,'bindings':[{**bindings[0],'raw_path':str(sibling)},bindings[1]]})
            assert not refused.valid and any(error['code']=='CITATION_BINDING_INVALID' for error in refused.errors),refused.errors
            assert all(path.read_bytes()==content for path,content in original_bytes.items())
            assert not (ROOT/'project/state.db').exists()
        ''')


    def test_verifier_binds_actual_capture_acceptance_and_applicable_gather_map(self):
        self.run_case(r'''
            (ROOT/'project').mkdir()
            candidate=note('wiki/alpha.md','[QUOTE] Alpha only after Q.\n')
            raw=note('raw/documents/raw-a.md','Alpha only after Q.\n',{'type':'source','source_class':'document'})
            rows={'L3.notes':[{'item_id':'a','status':'done','data':{},'artifacts':[w._ref(candidate,'distillation.md')]}]}
            expected=[]
            for step in ('L1.extract','L1.review','capture.scope','capture.validate','capture.fidelity','capture.lifecycle','legacy.capture','L1.gather'):
                path=ROOT/(step+'.json'); path.write_text(json.dumps({'step':step,'retained_evidence':True}),encoding='utf-8')
                reference=w._ref(path); expected.append(reference)
                rows[step]=[{'item_id':'main','status':'done','data':{},'artifacts':[reference]}]
            other=ROOT/'other-gather.json'; other.write_text('{}',encoding='utf-8')
            rows['L6.gather']=[{'item_id':'other','status':'done','data':{},'artifacts':[w._ref(other)]}]
            c=context('L4L5.verify1','verify',item_id='a',fanout='L3.items.items',data={'step_data':rows,'captured_raws':[w._ref(raw)]})
            prepared=w._verify_handler(c)
            actual={ref.path:ref.sha256 for ref in prepared.tasks[0].inputs}
            assert all(actual[ref['path']]==ref['sha256'] for ref in expected)
            assert str(other) not in actual
            assert str(raw) in actual and str(candidate) in actual
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_consolidated_review_receives_current_repaired_bundle_verdict_and_capture_gates(self):
        self.run_case(r'''
            raw=note('raw/documents/raw-a.md','Alpha only after Q.\n',{'type':'source','source_class':'document'})
            repaired=note('wiki/repaired.md','[QUOTE] Alpha only after Q.\n')
            bundle=[w._ref(repaired,'distillation.md')]
            for name in ('citation-bindings.json','coverage.json','repair-receipt.json'):
                path=ROOT/name; path.write_text(json.dumps({'member':name,'current':True}),encoding='utf-8'); bundle.append(w._ref(path,name))
            verdict=ROOT/'verdict.json'; verdict.write_text('{"overall_verdict":"PASS","subject":"a"}',encoding='utf-8')
            consolidated=ROOT/'remediation-verdicts.json'; consolidated.write_text('{"verdicts":[{"subject":"a","overall_verdict":"PASS"}]}',encoding='utf-8')
            gate=ROOT/'capture-acceptance.json'; gate.write_text('{"actual_capture_gate":"hash-bound"}',encoding='utf-8')
            rows={'L4L5.repair1':[{'item_id':'a','status':'done','data':{},'artifacts':bundle}],
                  'L4L5.verify2':[{'item_id':'a','status':'done','data':{},'artifacts':[w._ref(verdict,'verdict.json')]}],
                  'L4L5.consolidate1':[{'item_id':'main','status':'done','data':{'touched':['a']},'artifacts':[w._ref(consolidated,'remediation-verdicts.json')]}],
                  'capture.fidelity':[{'item_id':'main','status':'done','data':{},'artifacts':[w._ref(gate)]}]}
            c=context('L4L5.review1','review',policy={'source_step':'L4L5.consolidate1','sequence':'L4L5'},data={'step_data':rows,'captured_raws':[w._ref(raw)]})
            review=w._review_handler(c).local_result['data']['review']
            assert review['source']==str(consolidated)
            inputs={ref['path']:ref['sha256'] for ref in review['input_refs']}
            assert all(inputs[ref['path']]==ref['sha256'] for ref in [*bundle,w._ref(verdict),w._ref(gate)])
            focus=Path(review['focus']).read_text(encoding='utf-8')
            assert 'actual_capture_gate' in focus and 'repair-receipt.json' in focus and '"subject":"a"' in focus
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_capture_gate_evidence_hash_change_refuses_preparation(self):
        self.run_case(r'''
            evidence=ROOT/'capture-acceptance.json'; evidence.write_text('{"accepted":true}',encoding='utf-8')
            reference=w._ref(evidence)
            c=context('L4L5.verify1','verify',data={'step_data':{'capture.fidelity':[{'item_id':'main','status':'done','artifacts':[reference]}]}})
            evidence.write_text('{"accepted":false}',encoding='utf-8')
            try: w._knowledge_evidence_refs(c)
            except w.DomainError as exc: assert exc.code=='INPUT_CHANGED'
            else: raise AssertionError('changed capture acceptance evidence admitted')
        ''')

    def test_checked_candidate_repair_preserves_outputs_resources_and_exact_read_grants(self):
        self.run_case(r'''
            from copy import deepcopy
            (ROOT/'project').mkdir()
            evidence=ROOT/'source.json'; evidence.write_text('{"actual":"source evidence"}',encoding='utf-8')
            c=context('L3.notes','distill',item_id='a')
            outputs=[w._output('record.json')]
            normal=w._task(c,'produce','Produce the assigned source subject only.',outputs,inputs=[w._ref(evidence)]).tasks[0]
            original=Path(normal.candidate_dir)/'record.json'; original.parent.mkdir(parents=True)
            original.write_text('{"omitted":"required original metadata"}',encoding='utf-8')
            original_task=ROOT/'original-task.json'; original_task.write_text(json.dumps(w.task_to_dict(normal)),encoding='utf-8')
            diagnostic=ROOT/'structural-rejection.json'; diagnostic.write_text('{"unit_only":true,"errors":[{"code":"ARTIFACT_FIELD_MISSING","field":"confidence"}]}',encoding='utf-8')
            continuation={'kind':'candidate','original_task':w._ref(original_task),'original_candidates':[w._ref(original)],'diagnostics':[w._ref(diagnostic)]}
            paths={**c.paths,'attempt_id':'current-repair','candidate_dir':Path(c.paths['run_root'])/'attempts/current-repair/candidate'}
            replacement=replace(c,paths=paths,data={**c.data,'source_continuation':continuation})
            repaired=w._task(replacement,'produce','Produce the assigned source subject only.',outputs,inputs=[w._ref(evidence)]).tasks[0]
            assert repaired.duty=='repair' and repaired.outputs==normal.outputs
            assert (repaired.run_id,repaired.step_id,repaired.item_id,repaired.artifact_id,repaired.review_contract,repaired.artifact_tier)==(normal.run_id,normal.step_id,normal.item_id,normal.artifact_id,normal.review_contract,normal.artifact_tier)
            assert repaired.resources==normal.resources and repaired.allowed_actions==normal.allowed_actions
            assert repaired.write_roots==(str(paths['candidate_dir']),)
            assert repaired.inputs==(*normal.inputs,w._input(w._ref(original)),w._input(w._ref(diagnostic)))
            assert repaired.read_roots==tuple(dict.fromkeys((*normal.read_roots,str(original),str(diagnostic))))
            assert str(original.parent) not in repaired.read_roots and str(diagnostic.parent) not in repaired.read_roots
            assert str(original_task) not in {ref.path for ref in repaired.inputs}
            assert 'genuine retained diagnostics' in repaired.instructions and 'do not regrade' in repaired.instructions
            assert repaired.semantic_sha256==w.semantic_hash(w.task_to_dict(repaired))
            for change in ('extra','empty','stale','outside','foreign'):
                bad=deepcopy(continuation)
                if change=='extra': bad['arbitrary_read_root']=str(ROOT)
                elif change=='empty': bad['diagnostics']=[]
                elif change=='stale': bad['diagnostics'][0]['sha256']='0'*64
                elif change=='outside': bad['original_candidates']=[w._ref(evidence)]
                else:
                    foreign=ROOT/'foreign-task.json'; wire=w.task_to_dict(normal); wire['artifact_id']='foreign:artifact'; wire['semantic_sha256']=w.semantic_hash(wire); foreign.write_text(json.dumps(wire),encoding='utf-8'); bad['original_task']=w._ref(foreign)
                invalid=replace(replacement,data={**replacement.data,'source_continuation':bad})
                try: w._task(invalid,'produce','Produce the assigned source subject only.',outputs,inputs=[w._ref(evidence)])
                except w.DomainError as exc: assert exc.code in ('SOURCE_CONTINUATION_INVALID','INPUT_CHANGED'), (change,exc.code)
                else: raise AssertionError('unchecked candidate repair '+change)
            assert original.read_text(encoding='utf-8')=='{"omitted":"required original metadata"}'
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_aggregate_coverage_is_immutable_input_outside_native_candidate(self):
        self.run_case(r'''
            from copy import deepcopy
            capture_root=ROOT/'capture'; capture_root.mkdir()
            raw=capture_root/'source.md'; raw.write_text('---\ntype: source\nsource_class: document\n---\nAlpha only after Q.\n',encoding='utf-8')
            sid=provenance.source_id({'kind':'canonical-reference','id':'synthetic:coverage'})
            expected=w.capture.enumerate_snapshot_units([{'path':raw.name,'sha256':w._hash(raw),'source_id':sid,'kind':'markdown_document_section'}],capture_root)
            units=deepcopy(expected)
            for unit in units:
                unit.update(produced_paths=list(unit['expected_paths']),produced_sha256=unit['original_sha256'],coverage={'complete_source_read':True,'full_body_retained':True,'missing':[],'unavailable':[],'truncation':[],'images':[]})
            inventory={'schema_version':1,'expected_units':expected,'units':units}
            c=context('coverage','coverage')
            c=replace(c,paths={**c.paths,'candidate_dir':c.candidate_dir/'candidate'})
            previous=Path(c.paths['run_root'])/'attempts/prior/candidate/distillation.md'; previous.parent.mkdir(parents=True)
            previous.write_text('[QUOTE] Alpha only after Q.\n',encoding='utf-8')
            reference=w._ref(previous,'distillation.md')
            entry={'unit_id':expected[0]['unit_id'],'topic':'alpha','artifact_refs':[{'path':previous.name,'sha256':reference['sha256']}],'disposition':'covered','reason':'Declared current note.','scope_rule':'all supplied units'}
            (previous.parent/'coverage.json').write_text(json.dumps({'schema_version':1,'entries':[entry]}),encoding='utf-8')
            companions=[w._ref(previous.parent/'coverage.json','coverage.json')]
            for name in ('citation-bindings.json','repair-receipt.json'):
                member=previous.parent/name; member.write_text('{"actual_current_companion":true}',encoding='utf-8'); companions.append(w._ref(member,name))
            rows={'L3.items':[{'item_id':'main','status':'done','data':{'items':[{'item_id':'a','unit_id':expected[0]['unit_id']}]},'artifacts':[]}],
                  'L3.notes':[{'item_id':'a','status':'done','data':{},'artifacts':[reference,*companions]}]}
            c=replace(c,data={**c.data,'inventory':inventory,'topic':'alpha','capture_root':str(capture_root),'captured_raws':[w._ref(raw)],'step_data':rows})
            prepared=w._coverage_handler(c)
            assert prepared.status=='ready', prepared
            task=prepared.tasks[0]
            for companion in companions:
                assert any(ref.path==companion['path'] and ref.sha256==companion['sha256'] for ref in task.inputs)
                assert companion['path'] in task.read_roots
            aggregate=Path(prepared.local_result['data']['task_context']['aggregate_coverage_ref']['path'])
            assert aggregate.parent==c.candidate_dir.parent/'prepared-inputs'
            assert not aggregate.is_relative_to(c.candidate_dir)
            assert any(ref.path==str(aggregate) and ref.sha256==w._hash(aggregate) for ref in task.inputs)
            assert str(aggregate) in task.read_roots and all(not aggregate.is_relative_to(Path(root)) for root in task.write_roots)
            original=aggregate.read_bytes()
            assert w._coverage_handler(c).tasks[0].semantic_sha256==task.semantic_sha256
            assert aggregate.read_bytes()==original
            copies=prepared.local_result['data']['task_context']['source_unit_copies']
            assert len(copies)==1 and copies[0]['original']==w._ref(raw)
            copied=Path(copies[0]['prepared']['path'])
            assert copied.parent==aggregate.parent and copied.read_bytes()==raw.read_bytes()
            assert copies[0]['prepared']['sha256']==w._hash(raw)
            assert str(raw) not in {ref.path for ref in task.inputs}
            assert any(ref.path==str(copied) and ref.sha256==w._hash(raw) for ref in task.inputs)
            assert str(copied) in task.read_roots and all(not copied.is_relative_to(Path(root)) for root in task.write_roots)
            foreign=ROOT/'unbound-source.md'; foreign.write_bytes(raw.read_bytes())
            invalid=replace(c,data={**c.data,'captured_raws':[w._ref(foreign)]})
            try: w._coverage_handler(invalid)
            except w.DomainError as exc: assert exc.code=='CAPTURE_INPUT_UNBOUND'
            else: raise AssertionError('unbound source acquired a prepared read grant')
            copied.write_bytes(b'changed immutable source unit')
            try: w._coverage_handler(c)
            except w.DomainError as exc: assert exc.code=='INPUT_CHANGED'
            else: raise AssertionError('changed prepared source unit overwritten')
            copied.write_bytes(raw.read_bytes())
            aggregate.write_bytes(b'changed immutable preparation')
            try: w._coverage_handler(c)
            except w.DomainError as exc: assert exc.code=='INPUT_CHANGED'
            else: raise AssertionError('changed prepared aggregate input overwritten')
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_real_text_extractor_capture_retains_source_and_independent_unit_hash(self):
        self.run_case(r'''
            source=ROOT/'source.txt'; original=b'Alpha starts at10 only after conditionQ.\nExample cost20. Constraint remains3 units.\n'; source.write_bytes(original)
            c=context('L1.acquire','acquire',data={'options':{'transcript':None}})
            inventory,refs,images,diagnostics=w._capture_sources(c,[{'source':str(source),'source_class':'document'}])
            assert len(inventory['expected_units'])==1 and len(refs)==1 and not images
            unit=inventory['expected_units'][0]
            assert (c.candidate_dir/unit['original_locator']['path']).read_bytes()==original
            assert w.capture.validate_capture(inventory,c.candidate_dir).valid
            Path(refs[0]['path']).write_text('truncated',encoding='utf-8')
            assert not w.capture.validate_capture(inventory,c.candidate_dir).valid
        ''')

    def test_flat_book_aggregate_preserves_units_without_false_file_independence(self):
        self.run_case(r'''
            make_schema(book_paths={'raw_layer':'raw/books/','slug_by':None,'index_file':None,'chapter_pattern':'{book-slug}.md','raw_archive':None})
            c=book_context()
            plans,units=w._capture_plan(c)
            assert len(plans)==1 and len(units)==2
            assert {item['destination'] for item in units}=={'raw/books/atlas.md'}
            assert len({item['output_name'] for item in units})==1
            assert next(iter(plans.values()))['aggregate_units']==['u000001','u000002']
            assert len({item['source_id'] for item in units})==1
            assert not (VAULT/'raw/books/atlas.md').exists()
        ''')

    def test_different_origins_cannot_overwrite_a_declared_flat_capture_target(self):
        self.run_case(r'''
            make_schema(book_paths={'raw_layer':'raw/books/','slug_by':None,'index_file':None,'chapter_pattern':'{book-slug}.md','raw_archive':None})
            c=book_context(different_origins=True)
            before={str(p):p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
            try: w._capture_plan(c)
            except w.DomainError as exc: assert exc.code=='CAPTURE_PATH_COLLISION'
            else: raise AssertionError('different books collapsed into one raw')
            assert before=={str(p):p.read_bytes() for p in VAULT.rglob('*') if p.is_file()}
        ''')

    def test_isbn_edition_not_chapter_filename_identifies_legacy_origin(self):
        self.run_case(r'''
            a={'path':str(VAULT/'raw/books/ch01.md'),'metadata':{'source_class':'book','chapter_index':1,'isbn':'978-1-234567-89-0','edition':'2'}}
            b={'path':str(VAULT/'raw/books/ch20.md'),'metadata':{'source_class':'book','chapter_index':20,'isbn':'9781234567890','edition':'2'}}
            assert provenance.source_id(w._origin_material(a))==provenance.source_id(w._origin_material(b))
            unknown={'path':a['path'],'metadata':{'source_class':'book','chapter_index':1}}
            try: w._origin_material(unknown)
            except w.DomainError as exc: assert exc.code=='SOURCE_ORIGIN_UNAVAILABLE'
            else: raise AssertionError('chapter file treated as independent originating work')
        ''')

    def test_declared_chapter_numbers_remain_separate_from_file_ordinals(self):
        self.run_case(r'''
            assert w._chapter_number('Chapter IV: Conditions')==4
            assert w._chapter_number('Chapter Twenty-One: Methods')==21
            assert w._chapter_number('7. Worked Example')==7
            assert w._chapter_number('Preface') is None
            c=book_context()
            values=w._book_values(c,c.data['captured_raws'][1])
            assert values['NN']=='21'  # This is the second file, but source chapter21.
        ''')

    def test_exact_levenshtein_matches_independent_matrix_oracle(self):
        self.run_case(r'''
            import random
            def oracle(left,right):
                if not left and not right: return 0.0
                row=list(range(len(right)+1))
                for i,a in enumerate(left,1):
                    following=[i]
                    for j,b in enumerate(right,1): following.append(min(following[-1]+1,row[j]+1,row[j-1]+(a!=b)))
                    row=following
                return row[-1]/max(len(left),len(right))
            random.seed(91)
            for _ in range(120):
                left=''.join(random.choice('abéQ') for _ in range(random.randrange(14)))
                right=''.join(random.choice('abéQ') for _ in range(random.randrange(14)))
                assert w.normalized_levenshtein(left,right)==oracle(left,right),(left,right)
        ''')

    def test_restore_reuses_frozen_claim_scope_without_live_reselection(self):
        self.run_case(r'''
            path=note('wiki/alpha.md','[QUOTE] Alpha conditionQ. ^[[[raw-a]]]\n')
            req=request('wiki-content-check','--vault',str(VAULT),'--all')
            graph=w.build_workflow(req)
            snapshot={'metadata':graph.metadata}
            path.write_text('[QUOTE] Changed.\n\n[PARAPHRASE] New claim.\n',encoding='utf-8')
            restored=w.restore_workflow(req,snapshot=snapshot)
            assert [s.step_id for s in restored.steps]==[s.step_id for s in graph.steps]
            assert restored.metadata==graph.metadata
            assert len([s for s in restored.steps if s.policy['handler']=='claim_evaluate'])==1
        ''')

    def test_no_review_aborts_page_remainder_without_another_claim_dispatch(self):
        self.run_case(r'''
            p=page(3); p['claims']=[{'claim_id':f'p-c{n:03d}','claim_sha256':'b'*64,'raw_archive_ref':'raw-a'} for n in range(1,4)]
            rows={'CC2.p-c002.review':[{'item_id':'root','status':'done','data':{'review_gate':{'tooling_failure':True,'accepted':False}},'artifacts':[]}]}
            c=context('CC2.p-c003.evaluate','claim_evaluate',policy={'page':p,'claim':p['claims'][2]},data={'step_data':rows})
            result=w.prepare_step(c)
            assert result.status=='skip' and not result.tasks
            # Fixed CC1 subject registration, not a fabricated review result.
            real=note('wiki/claims.md','[QUOTE] A.\n\n[QUOTE] B.\n\n[QUOTE] C.\n')
            item={'item_id':'p','slug':'claims','path':str(real),'sha256':w._hash(real)}
            item['claims']=w.extract_claims(real.read_text(encoding='utf-8'),'p')
            cc=context('CC1','claims',data={'item':item})
            prepared=w.prepare_step(cc)
            assert len(prepared.local_result['data']['review_subjects'])==3
            assert all(subject['max_full_reviews']==1 and subject['max_repairs']==0 for subject in prepared.local_result['data']['review_subjects'])
        ''')


SETUP = r'''
import json
from hashlib import sha256
from dataclasses import replace
try:
    import yaml
except ImportError:
    print('PyYAML unavailable'); raise SystemExit(77)
from _workflow.controller import RunRequest, StepContext, StepSpec
from wiki.runtime import workflows as w, provenance
VAULT=ROOT/'vault'; VAULT.mkdir()

def make_schema(tiers=None,**extras):
    fields={'wiki_name':'SyntheticWiki','extends':'canonical-schema.md','extends_version':'3.1','domains':['science'],'topics':{'science':['alpha']}}
    fields.update(extras)
    controls={'workflow_controls_version':1,'allowed_tiers':tiers or ['A','B','C'],'managed_navigation':{'_hub.md':'navigation','log.md':'operations','verify-log.md':'verification'}}
    (VAULT/'.wiki-schema.md').write_text('---\n'+yaml.safe_dump(fields,sort_keys=False)+'---\n\n<!-- ash_wiki:begin controls -->\n```yaml\n'+yaml.safe_dump(controls,sort_keys=False)+'```\n<!-- ash_wiki:end controls -->\n',encoding='utf-8')
make_schema()
(VAULT/'raw').mkdir()
(VAULT/'raw/_manifest.json').write_text('{"last_verified_at":"2020-01-01T00:00:00Z","wiki_workflow":{"artifacts":[]}}',encoding='utf-8')
for filename,region in [('_hub.md','navigation'),('log.md','operations'),('verify-log.md','verification')]:
    (VAULT/filename).write_text('Manual outside bytes\n<!-- ash_wiki:begin '+region+' -->\n<!-- ash_wiki:end '+region+' -->\n',encoding='utf-8')

def note(relative,body,metadata=None):
    path=VAULT/relative; path.parent.mkdir(parents=True,exist_ok=True)
    prefix='---\n'+yaml.safe_dump(metadata,sort_keys=False)+'---\n' if metadata else ''
    path.write_bytes((prefix+body).encode('utf-8')); return path

def request(verb,*arguments):
    return RunRequest(1,verb,ROOT/'project',tuple(arguments),'omp','gpt','auto',None)

def resolved(): return w.schema.resolve_schema(VAULT)

def page(count):
    return {'item_id':'p','path':str(VAULT/'wiki/p.md'),'slug':'p','sha256':'a'*64,'claims':[{'claim_id':f'p-c{n:03d}'} for n in range(1,count+1)]}

def context(step_id,handler,*,policy=None,data=None,fanout=None,item_id='root'):
    step=StepSpec(step_id,(),'local',w.prepare_step,fanout,1,{'handler':handler,**(policy or {})})
    run_root=ROOT/'project/docs/tmp/pipeline-runs/synthetic-run'
    candidate=run_root/'attempts'/('attempt-'+step_id.replace('.','-'))
    paths={'project_root':ROOT/'project','run_root':run_root,'candidate_dir':candidate,'attempt_id':'attempt-'+step_id.replace('.','-'),'skills_root':SKILLS}
    return StepContext(request('wiki-content-check','--vault',str(VAULT),'--all'),'synthetic-run',step,item_id,0,{},(),paths,{'vault_root':str(VAULT),**(data or {})})

def book_context(different_origins=False):
    c=context('L1.extract','extract',data={'options':{'title':None},'topic':'alpha','domain':'science','topic_slug':'alpha'})
    folder=ROOT/'capture'; folder.mkdir(exist_ok=True)
    snapshot=folder/'original.epub'; snapshot.write_bytes(b'Synthetic immutable original snapshot; no native execution')
    raws=[]; expected=[]
    for ordinal,title in enumerate(('Chapter IV: Conditions','Chapter Twenty-One: Methods'),1):
        path=folder/f'chapter-{ordinal}.md'; path.write_text(title+'\nCondition Q remains.\n',encoding='utf-8')
        material={'kind':'canonical-reference','id':'synthetic:atlas'+(str(ordinal) if different_origins else '')}
        sid=provenance.source_id(material); uid=f'u{ordinal:06d}'
        raws.append({**w._ref(path),'unit_id':uid,'source_id':sid,'source_class':'book','identity':material,'original_locator':{'title':title,'chapter_ordinal':ordinal},'metadata':{'title':'Atlas','author':'Casey','source_reference':str(snapshot)}})
        expected.append({'unit_id':uid,'original_sha256':w._hash(snapshot)})
    return replace(c,data={**c.data,'capture_root':str(folder),'captured_raws':raws,'inventory':{'expected_units':expected}})
'''


if __name__ == '__main__':
    unittest.main()
