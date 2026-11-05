"""Synthetic installed-layout consumers; authored only, never native proof."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[2]


class TaskPreparationTests(unittest.TestCase):
    def run_case(self, code):
        with tempfile.TemporaryDirectory(prefix='wiki-tasks-') as temporary:
            base = Path(temporary).resolve()
            export = base / 'skills'
            (export / '_workflow').mkdir(parents=True)
            (export / 'wiki/runtime').mkdir(parents=True)
            (export / 'wiki/scripts').mkdir()
            shutil.copyfile(REPO / '_shared/workflow/src/contracts.py', export / '_workflow/contracts.py')
            for name in ('tasks', 'schema', 'capture', 'coverage', 'citations', 'provenance'):
                shutil.copyfile(REPO / f'wiki/runtime/{name}.py', export / f'wiki/runtime/{name}.py')
            shutil.copyfile(REPO / 'wiki/scripts/tag_ontology.py', export / 'wiki/scripts/tag_ontology.py')
            for directory in ('tasks', 'templates', 'checklists', '_archive'):
                shutil.copytree(REPO / f'wiki/{directory}', export / f'wiki/{directory}')
            for name in ('canonical-schema.md', 'wiki_verify.md', 'wiki_lint.md'):
                shutil.copyfile(REPO / f'wiki/{name}', export / f'wiki/{name}')
            env = {key: value for key, value in os.environ.items() if not key.startswith('VAULT_')}
            for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TMP', 'TEMP', 'TMPDIR'):
                path = base / key.lower()
                path.mkdir(exist_ok=True)
                env[key] = str(path)
            fixture = base / 'fixture'
            fixture.mkdir()
            prelude = f'import sys\nsys.path.insert(0,{str(export)!r})\nROOT=__import__("pathlib").Path({str(fixture)!r})\nSKILLS=__import__("pathlib").Path({str(export)!r})\n'
            result = subprocess.run([sys.executable, '-I', '-B', '-c', prelude + SETUP + textwrap.dedent(code)], cwd=fixture, env=env, capture_output=True, text=True, timeout=40)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_complete_resource_and_evidence_hashes_are_frozen(self):
        self.run_case('''
            task = prepare()
            assert task.semantic_sha256 == semantic_hash(task)
            assert task.write_roots == (str(CANDIDATE),)
            assert task.inputs == (RAW,)
            assert all(Path(ref.path).is_file() and sha256(Path(ref.path).read_bytes()).hexdigest() == ref.sha256 for ref in task.resources)
            assert RESOLVED.constraints_ref in task.resources
            assert not CANDIDATE.exists()
            assert not (PROJECT/'state.db').exists()
            for duty in DUTIES:
                assert prepare(duty=duty).duty == duty
        ''')

    def test_evidence_change_changes_semantics_and_stale_ref_refuses(self):
        self.run_case('''
            first = prepare()
            Path(RAW.path).write_text('Condition Q changed.',encoding='utf-8')
            rejected(lambda: prepare())
            fresh = ref(Path(RAW.path))
            CONTEXT.data['raw_inputs'] = (fresh,)
            second = prepare(inputs=(fresh,))
            assert first.semantic_sha256 != second.semantic_sha256
        ''')

    def test_missing_and_changed_source_resources_refuse(self):
        self.run_case('''
            selected = SKILLS/'wiki/tasks/distill.md'
            selected.unlink()
            rejected(lambda: prepare())
            selected.write_text('A replacement full duty.',encoding='utf-8')
            task = prepare()
            selected.write_text('Changed after dispatch.',encoding='utf-8')
            path = output_file(SPEC, compilation())
            CONTEXT.data['task'] = task
            assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_persisted_task_validates_frozen_resources_after_installation_changes(self):
        self.run_case(r'''
            from _workflow.contracts import task_to_dict
            task = prepare()
            CONTEXT.data['task'] = task
            path = output_file(SPEC, compilation())
            supports(path)
            assert validate_artifact(SPEC, path, CONTEXT).valid
            run_root = PROJECT/'docs/tmp/pipeline-runs'/RUN
            snapshots = run_root/'attempts'/task.attempt_id/'snapshots'
            snapshots.mkdir()
            for resource in task.resources:
                target = snapshots/resource.sha256
                if not target.exists():
                    target.write_bytes(Path(resource.path).read_bytes())
            task_path = run_root/'attempts'/task.attempt_id/'task.json'
            task_path.write_text(json.dumps(task_to_dict(task)), encoding='utf-8')
            original_task = task_path.read_bytes()
            for resource in task.resources:
                if Path(resource.path).name in ('wiki_verify.md', 'wiki-citation-discipline.md', 'wiki-all.md', 'compilation.md'):
                    Path(resource.path).write_bytes(b'Later installed resource generation.\n')
            checked = validate_artifact(SPEC, path, CONTEXT)
            assert checked.valid, checked.errors
            assert task_path.read_bytes() == original_task and task.semantic_sha256 == semantic_hash(task)
            path = output_file(SPEC, compilation({'sources': 0}))
            supports(path)
            assert not validate_artifact(SPEC, path, CONTEXT).valid
        ''')

    def test_persisted_resources_require_exact_task_snapshots_and_live_raw_inputs(self):
        self.run_case(r'''
            from _workflow.contracts import task_to_dict
            task = prepare()
            CONTEXT.data['task'] = task
            path = output_file(SPEC, compilation())
            supports(path)
            run_root = PROJECT/'docs/tmp/pipeline-runs'/RUN
            snapshots = run_root/'attempts'/task.attempt_id/'snapshots'
            snapshots.mkdir()
            for resource in task.resources:
                target = snapshots/resource.sha256
                if not target.exists():
                    target.write_bytes(Path(resource.path).read_bytes())
            task_path = run_root/'attempts'/task.attempt_id/'task.json'
            task_path.write_text(json.dumps(task_to_dict(task)), encoding='utf-8')
            assert validate_artifact(SPEC, path, CONTEXT).valid
            selected = snapshots/task.resources[0].sha256
            retained = selected.read_bytes()
            source_snapshots = run_root/'snapshots'
            source_snapshots.mkdir()
            (source_snapshots/selected.name).write_bytes(retained)
            selected.unlink()
            assert not validate_artifact(SPEC, path, CONTEXT).valid
            selected.write_bytes(b'Wrong bytes at the expected content-addressed path.\n')
            assert not validate_artifact(SPEC, path, CONTEXT).valid
            selected.write_bytes(retained)
            changed = task_to_dict(task)
            changed['attempt_id'] = 'another-attempt'
            task_path.write_text(json.dumps(changed), encoding='utf-8')
            assert not validate_artifact(SPEC, path, CONTEXT).valid
            task_path.write_text(json.dumps(task_to_dict(task)), encoding='utf-8')
            (snapshots/RAW.sha256).write_bytes(Path(RAW.path).read_bytes())
            Path(RAW.path).write_bytes(b'Changed raw evidence.\n')
            assert not validate_artifact(SPEC, path, CONTEXT).valid
        ''')

    def test_selected_archive_is_exact_and_incompatible_pin_refuses(self):
        self.run_case('''
            pinned = dataclasses.replace(RESOLVED,canonical_version='3.0',canonical_sha256=sha256((SKILLS/'wiki/_archive/canonical-schema-v3.0.md').read_bytes()).hexdigest())
            CONTEXT.data['resolved_vault'] = pinned
            task = prepare()
            assert any(Path(r.path).name == 'canonical-schema-v3.0.md' for r in task.resources)
            (SKILLS/'wiki/_archive/canonical-schema-v3.0.md').unlink()
            rejected(lambda: prepare())
            CONTEXT.data['resolved_vault'] = dataclasses.replace(RESOLVED,canonical_version='8.0')
            rejected(lambda: prepare())
        ''')

    def test_candidate_escape_and_vault_write_permissions_refuse(self):
        self.run_case('''
            original = CONTEXT.candidate_dir
            CONTEXT.candidate_dir = VAULT/'wiki/candidates'
            rejected(lambda: prepare())
            CONTEXT.candidate_dir = original
            rejected(lambda: prepare(allowed_actions=('read','write','publish')))
            rejected(lambda: prepare(allowed_actions=('read',)))
            rejected(lambda: prepare(duty='standalone'))
            rejected(lambda: OutputSpec('escape','../outside.md','text/markdown',True))
            rejected(lambda: prepare(outputs=(OutputSpec('unresolved','{topic}.md','text/markdown',True),)))
        ''')

    def test_tier_and_profile_semantic_override_refuse_before_dispatch(self):
        self.run_case('''
            CONTEXT.data['resolved_vault'] = dataclasses.replace(RESOLVED,allowed_tiers=('A','B'))
            rejected(lambda: prepare(artifact_tier='C'))
            CONTEXT.data['resolved_vault'] = RESOLVED
            CONTEXT.data['execution_profile'] = {'instructions':'override'}
            rejected(lambda: prepare())
            CONTEXT.data['execution_profile'] = {'backend':'codex','resource_hints':{'timeout_seconds':30}}
            first = prepare()
            CONTEXT.data['execution_profile'] = {'backend':'omp','resource_hints':{'timeout_seconds':60}}
            assert prepare().semantic_sha256 == first.semantic_sha256
            CONTEXT.request.profile = 'unknown'
            rejected(lambda: prepare())
        ''')

    def test_note_candidates_check_actual_citations_without_accepting_truth(self):
        self.run_case('''
            path = output_file(SPEC,compilation())
            supports(path)
            verdict = validate_artifact(SPEC,path,CONTEXT)
            assert verdict.valid, verdict.errors
            assert any(d['code']=='MECHANICAL_VALIDATION_ONLY' for d in verdict.diagnostics)
            CONTEXT.data['citation_bindings'] = []
            assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_output_name_path_and_required_presence_are_checked(self):
        self.run_case('''
            assert not validate_artifact(SPEC,CANDIDATE/SPEC.relative_path,CONTEXT).valid
            optional = OutputSpec('optional','optional.txt','text/plain',False)
            assert validate_artifact(optional,CANDIDATE/'optional.txt',CONTEXT).valid
            wrong = output_file(OutputSpec('wrong','other.md','text/markdown',True),compilation())
            assert not validate_artifact(SPEC,wrong,CONTEXT).valid
            outside = ROOT/'outside.md'
            outside.write_text(compilation(),encoding='utf-8')
            assert not validate_artifact(SPEC,outside,CONTEXT).valid
        ''')

    def test_low_contract_notes_do_not_inherit_compilation_requirements(self):
        self.run_case(r'''
            CONTEXT.data.update(output_type='entity',artifact_role=None,output_paths={'note':'wiki/science/person.md'})
            text = render({'type':'entity','title':'Synthetic person','domain':'science'},'# Synthetic person\n')
            path = output_file(SPEC,text)
            assert validate_artifact(SPEC,path,CONTEXT).valid
            for typ in ('query','moc'):
                CONTEXT.data['output_type'] = typ
                path = output_file(SPEC,render({'type':typ,'title':'Synthetic reference','domain':'science'},'# Synthetic reference\n'))
                assert validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_tier_c_raw_ratio_requires_recommendations_without_becoming_unconditional_failure(self):
        self.run_case('''
            from wiki.runtime.tasks import _tier_c
            def check(compilations, raws, confidence, *, recommendation=True, wrong_body=False):
                entries = [{'slug':'compiled-'+str(i),'source_class':'compilation','citation_count':1} for i in range(compilations)]
                entries += [{'slug':'raw-'+str(i),'source_class':'documents','citation_count':1} for i in range(raws)]
                metadata = {'type':'plan','goal':'Choose a condition policy','compiled_from':entries,'confidence':confidence}
                if raws and recommendation: metadata['recommend_compile'] = ['[[raw-'+str(i)+']]' for i in range(raws)]
                reasoning = '\\n'.join(str(i+1)+'. Evidence applies. [PARAPHRASE] ^[[['+entry['slug']+']]]' for i,entry in enumerate(entries))
                body = '## Goal\\nChoose a condition policy\\n\\n## Context\\n'+('Context '*22)+'\\n\\n## Recommendation\\nUse the evidence.\\n\\n## Reasoning Chain\\n'+reasoning+'\\n\\n## Evidence Base\\nSupplied sources.\\n\\n## Counter-Considerations\\n'+('Counter evidence '*12)+'\\n\\n## Review Triggers\\n- When observed cost exceeds 20 units.\\n'
                if raws and recommendation:
                    body += '\\n## Recommend-Compile\\n'+('[[different-raw]]' if wrong_body else '\\n'.join(metadata['recommend_compile']))
                errors = []
                _tier_c(metadata,body,errors)
                return errors
            assert not check(7,3,'high') # Exactly thirty percent, enough compilations.
            assert not check(2,1,'medium') # Higher ratio is permissible with disclosure/downgrade.
            assert check(2,1,'high')
            assert check(2,1,'medium',recommendation=False)
            assert check(2,1,'medium',wrong_body=True)
            assert not check(1,0,'low')
            assert check(1,0,'medium')
        ''')

    def test_canonical_citation_assignment_is_hash_bound_and_validates_exact_raw_support(self):
        self.run_case('''
            baseline = prepare()
            target = 'raw/documents/canonical-origin'
            CONTEXT.data['citation_targets'] = {target:RAW}
            bound = prepare()
            assert bound.semantic_sha256 != baseline.semantic_sha256
            payload = compilation({'compiled_from':[{'slug':target,'source_class':'documents','citation_count':1}]}).replace('^[[[origin]]]', '^[[['+target+']]]')
            path = output_file(SPEC,payload)
            supports(path)
            result = validate_artifact(SPEC,path,CONTEXT)
            assert result.valid, result.errors
            CONTEXT.data['citation_targets'] = {target:InputRef(RAW.path,'0'*64,RAW.media_type)}
            rejected(lambda:prepare())
            assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_declared_supersession_variant_matches_exact_template_date_and_parent(self):
        self.run_case('''
            canonical = CONTEXT.data['output_paths']['note']
            target = Path(canonical)
            suffix = target.with_name(target.stem+'-2026-10-17'+target.suffix).as_posix()
            CONTEXT.data.update(output_paths={'note':suffix}, publication_variant={'kind':'supersession-date-suffix','canonical_target':canonical,'date':'2026-10-17'})
            path = output_file(SPEC, compilation())
            supports(path)
            result = validate_artifact(SPEC,path,CONTEXT)
            assert result.valid, result.errors
            for destination in (target.with_name(target.stem+'-2026-10-18.md').as_posix(), 'wiki/foreign/'+Path(suffix).name):
                CONTEXT.data['output_paths']['note'] = destination
                assert not validate_artifact(SPEC,path,CONTEXT).valid
            CONTEXT.data['output_paths']['note'] = suffix
            path = output_file(SPEC, compilation({'supersedes':canonical.removesuffix('.md')}))
            supports(path)
            assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_retained_variant_requires_existing_canonical_not_a_fictional_target(self):
        self.run_case('''
            canonical = 'wiki/custom/retained.md'
            CONTEXT.data.update(path_values=None, accepted_target_path=canonical, output_paths={'note':'wiki/custom/retained-2026-10-17.md'}, publication_variant={'kind':'supersession-date-suffix','canonical_target':canonical,'date':'2026-10-17'})
            path = output_file(SPEC, compilation())
            supports(path)
            assert not validate_artifact(SPEC,path,CONTEXT).valid
            existing = VAULT/canonical
            existing.parent.mkdir(parents=True,exist_ok=True)
            existing.write_bytes(b'Existing retained canonical')
            result = validate_artifact(SPEC,path,CONTEXT)
            assert result.valid, result.errors
            CONTEXT.data['publication_variant']['canonical_target'] = RESOLVED.canonical_files['hub']
            assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_wrong_identity_type_role_and_unknown_domain_refuse(self):
        self.run_case('''
            for changes in ({'vault':'PhysicalSelection'}, {'pipeline_id':'fabricated-run'}, {'type':'source'}, {'artifact_role':'capture'}, {'domain':'unknown'}, {'source_ids':['chapter-1']}):
                path = output_file(SPEC,compilation(changes))
                supports(path)
                assert not validate_artifact(SPEC,path,CONTEXT).valid, changes
        ''')

    def test_unknown_tags_and_compound_markers_refuse(self):
        self.run_case('''
            path = output_file(SPEC,compilation({'tags':['topic/not-declared']}))
            supports(path)
            assert not validate_artifact(SPEC,path,CONTEXT).valid
            text = compilation().replace('[PARAPHRASE]','[QUOTE/PARAPHRASE]')
            path = output_file(SPEC,text)
            supports(path)
            assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_provenance_shape_count_and_contributor_oneof_refuse(self):
        self.run_case('''
            for changes in ({'compiled_from':['origin']},{'compiled_from':[{'slug':'origin','source_class':'documents','citation_count':True}]},{'contributing_books':['Invented book']},{'sources':True}):
                path = output_file(SPEC,compilation(changes))
                supports(path)
                assert not validate_artifact(SPEC,path,CONTEXT).valid, changes
        ''')

    def test_drafts_cannot_claim_verification_and_pins_keep_time_domains(self):
        self.run_case('''
            for timestamp in ('2026-10-16','2026-10-16T12:00:00Z'):
                path = output_file(SPEC,compilation({'last_verified':timestamp}))
                supports(path)
                assert not validate_artifact(SPEC,path,CONTEXT).valid
            path = output_file(SPEC,compilation({'status':'published','last_verified':'2026-10-16'}))
            supports(path)
            codes = {e['code'] for e in validate_artifact(SPEC,path,CONTEXT).errors}
            assert 'ARTIFACT_VERIFIED_TIME_INVALID' in codes and 'ARTIFACT_ACCEPTANCE_UNBOUND' in codes
            pinned = dataclasses.replace(RESOLVED,canonical_version='3.0',canonical_sha256=sha256((SKILLS/'wiki/_archive/canonical-schema-v3.0.md').read_bytes()).hexdigest())
            CONTEXT.data['resolved_vault'] = pinned
            codes = {e['code'] for e in validate_artifact(SPEC,path,CONTEXT).errors}
            assert 'ARTIFACT_VERIFIED_TIME_INVALID' not in codes and 'ARTIFACT_ACCEPTANCE_UNBOUND' in codes
        ''')

    def test_distillation_cannot_target_raw_or_protected_reference(self):
        self.run_case('''
            path = output_file(SPEC,compilation())
            supports(path)
            for destination in ('raw/documents/generated.md','.wiki-schema.md','../escape.md'):
                CONTEXT.data['output_paths'] = {'note':destination}
                assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_capture_body_hash_source_hash_and_modality_are_separate(self):
        self.run_case(r'''
            CONTEXT.data.update(output_type='source',artifact_role='capture',artifact_tier='A',output_paths={'note':'raw/documents/capture.md'},source_snapshot=RAW,source_author='Original Author')
            metadata = {'type':'source','artifact_role':'capture','source_ids':[SID], 'title':'Original','domain':'science','source':'Supplied original','source_class':'documents','provenance_class':'document','raw_modality':'documents','source_hash':RAW.sha256,'source_hash_kind':'bytes','source_hash_algorithm':'sha256','ingested':'2026-10-16','backfill':False,'source_file':RAW.path,'vault':'SyntheticLib','author':'Original Author','pipeline_id':RUN,'status':'draft','last_verified':None,'freshness':'permanent','confidence':'high','human_edited':False,'tags':['domain/science','topic/conditions','type/source','vault/SyntheticLib']}
            text = render(metadata,'Faithful source bytes.\n')
            metadata['content_hash'] = content_hash(text)
            path = output_file(SPEC,render(metadata,'Faithful source bytes.\n'))
            assert validate_artifact(SPEC,path,CONTEXT).valid
            for key,value in (('source_hash','0'*64),('raw_modality','pdf'),('source_class','pdf'),('content_hash','sha256:'+'0'*64)):
                changed = dict(metadata,**{key:value})
                path = output_file(SPEC,render(changed,'Faithful source bytes.\n'))
                assert not validate_artifact(SPEC,path,CONTEXT).valid, key
            literal = 'The original template says: Dear {name}, retain this literal token.\nPrior author: [INFERENCE] ^[[[earlier-source]]]\n'
            Path(RAW.path).write_bytes(literal.encode('utf-8'))
            snapshot = ref(Path(RAW.path))
            CONTEXT.data.update(source_snapshot=snapshot,raw_inputs=(snapshot,))
            metadata['source_hash'] = snapshot.sha256
            metadata['content_hash'] = content_hash(render(metadata,literal))
            path = output_file(SPEC,render(metadata,literal))
            assert validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_complete_verdict_subjects_and_attempt_identity_are_required(self):
        self.run_case('''
            jspec = OutputSpec('verdict','verdict.json','application/json',True)
            task = prepare(duty='verify',outputs=(jspec,))
            CONTEXT.data.update(task=task,artifact_kind='verdict',expected_subjects=('a','b'))
            document = {key:getattr(task,key) for key in ('run_id','step_id','item_id','attempt_id')}
            document.update(task_sha256=task.semantic_sha256,overall_verdict='FAIL',fail_count=1,subjects=[{'subject_id':'a','verdict':'FAIL','evidence':'origin: Condition Q is omitted.'},{'subject_id':'b','verdict':'PASS','evidence':'origin: Condition Q is required.'}],criteria=[{'criterion_id':key,'verdict':'FAIL' if key=='semantic_fidelity' else 'PASS','evidence':'origin: Condition Q is required.'} for key in __import__('wiki.runtime.tasks',fromlist=['VERDICT_CRITERIA']).VERDICT_CRITERIA['verify']])
            path = output_file(jspec,json.dumps(document))
            assert validate_artifact(jspec,path,CONTEXT).valid
            for subjects in ([{'subject_id':'a'}],[{'subject_id':'a'},{'subject_id':'a'}],[{'subject_id':'a'},{'subject_id':'phantom'}]):
                path = output_file(jspec,json.dumps(dict(document,subjects=subjects)))
                assert not validate_artifact(jspec,path,CONTEXT).valid
            path = output_file(jspec,json.dumps(dict(document,attempt_id='stale')))
            assert not validate_artifact(jspec,path,CONTEXT).valid
        ''')

    def test_verdict_spellings_and_typed_results_cannot_disagree(self):
        self.run_case('''
            from types import MappingProxyType
            from wiki.runtime.tasks import VERDICT_CRITERIA
            jspec = OutputSpec('verdict','verdict.json','application/json',True)
            CONTEXT.data.update(artifact_kinds={'verdict':'verdict'},expected_subjects=('origin',),task_result=MappingProxyType({'verification':'PASS'}))
            document = {'overall_verdict':'PASS','subjects':[{'subject_id':'origin','verdict':'PASS','evidence':'origin: Condition Q is required.'}],'criteria':[{'criterion_id':key,'verdict':'PASS','evidence':'origin: Condition Q is required.'} for key in VERDICT_CRITERIA['verify']]}
            for spelling in ('overall_verdict','verification','verdict'):
                legacy = dict(document)
                legacy.pop('overall_verdict')
                legacy[spelling] = 'PASS'
                path = output_file(jspec,json.dumps(legacy))
                result = validate_artifact(jspec,path,CONTEXT)
                assert result.valid, result.errors
            for changed in (dict(document,overall_verdict='FAIL'),dict(document,verification='FAIL'),dict(document,verdict='FAIL')):
                path = output_file(jspec,json.dumps(changed))
                result = validate_artifact(jspec,path,CONTEXT)
                assert not result.valid and any(error['code']=='VERDICT_RESULT_CONFLICT' for error in result.errors), result.errors
        ''')

    def test_verdict_criteria_and_positive_subject_evidence_are_complete(self):
        self.run_case('''
            jspec = OutputSpec('verdict','verdict.json','application/json',True)
            CONTEXT.data.update(artifact_kind='verdict',expected_criteria=('fidelity','coverage'),expected_subjects=('origin',))
            document = {'overall_verdict':'PASS','subjects':[{'subject_id':'origin','verdict':'PASS','evidence':'origin: Condition Q is required.'}],'criteria':[{'criterion_id':key,'verdict':'PASS','evidence':'origin: Condition Q is required.'} for key in ('fidelity','coverage')]}
            path = output_file(jspec,json.dumps(document))
            assert validate_artifact(jspec,path,CONTEXT).valid
            for entries in (document['criteria'][:1],document['criteria']+[document['criteria'][0]],[dict(document['criteria'][0],criterion_id='phantom'),document['criteria'][1]],[],[dict(document['criteria'][0],evidence=' '),document['criteria'][1]],[dict(document['criteria'][0],evidence={'span':[]}),document['criteria'][1]]):
                path = output_file(jspec,json.dumps(dict(document,criteria=entries)))
                assert not validate_artifact(jspec,path,CONTEXT).valid, entries
            for evidence in (' ',[],{}, {'span':None}):
                path = output_file(jspec,json.dumps(dict(document,subjects=[{'subject_id':'origin','verdict':'PASS','evidence':evidence}])))
                assert not validate_artifact(jspec,path,CONTEXT).valid
            path = output_file(jspec,json.dumps(dict(document,criteria=[dict(document['criteria'][0],verdict='FAIL'),document['criteria'][1]])))
            assert not validate_artifact(jspec,path,CONTEXT).valid
        ''')

    def test_failed_verdicts_still_require_subject_outcomes_and_evidence(self):
        self.run_case('''
            from wiki.runtime.tasks import VERDICT_CRITERIA
            jspec = OutputSpec('verdict','verdict.json','application/json',True)
            for duty, criteria in VERDICT_CRITERIA.items():
                CONTEXT.data.update(duty=duty,artifact_kind='verdict',expected_subjects=('origin',))
                document = {'overall_verdict':'FAIL','subjects':[{'subject_id':'origin','verdict':'FAIL','evidence':'origin: Condition Q is required, but omitted from the candidate.'}],'criteria':[{'criterion_id':key,'verdict':'FAIL','evidence':'origin: Condition Q is required.'} for key in criteria]}
                path = output_file(jspec,json.dumps(document))
                result = validate_artifact(jspec,path,CONTEXT)
                assert result.valid, (duty,result.errors)
                malformed = [({'subject_id':'origin','evidence':'origin: Condition Q is required.'},'ARTIFACT_SUBJECT_VERDICT_MISSING'),('origin','ARTIFACT_SUBJECT_VERDICT_MISSING')]
                malformed.extend(({'subject_id':'origin','verdict':state,'evidence':evidence},'ARTIFACT_SUBJECT_EVIDENCE_MISSING') for state in ('PASS','FAIL') for evidence in (None,' ',[],{}, {'span':None}))
                for subject, code in malformed:
                    path = output_file(jspec,json.dumps(dict(document,subjects=[subject])))
                    result = validate_artifact(jspec,path,CONTEXT)
                    assert not result.valid and any(error['code']==code for error in result.errors), (duty,subject,result.errors)
        ''')

    def test_verdict_check_projections_are_flat_booleans_and_agree_with_verdict(self):
        self.run_case('''
            jspec = OutputSpec('verdict','verdict.json','application/json',True)
            CONTEXT.data.update(artifact_kind='verdict',expected_criteria=('schema',),expected_subjects=('origin',))
            document = {'overall_verdict':'PASS','fail_count':0,'subjects':[{'subject_id':'origin','verdict':'PASS','evidence':'origin: Condition Q is required.'}],'criteria':[{'criterion_id':'schema','verdict':'PASS','evidence':'origin: Required fields are present.'}]}
            for field in ('frontmatter_checks','structural_integrity'):
                path = output_file(jspec,json.dumps(dict(document,**{field:{'required_fields':True}})))
                result = validate_artifact(jspec,path,CONTEXT)
                assert result.valid, result.errors
                malformed = ([{'check':'required_fields','verdict':'PASS','evidence':'origin: Required fields are present.'}],{'required_fields':'PASS'},{'required_fields':{'verdict':'PASS'}},{'required_fields':1})
                for projection in malformed:
                    path = output_file(jspec,json.dumps(dict(document,**{field:projection})))
                    result = validate_artifact(jspec,path,CONTEXT)
                    assert not result.valid and any(error['code']=='ARTIFACT_VERDICT_INVALID' for error in result.errors), result.errors
                path = output_file(jspec,json.dumps(dict(document,**{field:{'required_fields':False}})))
                result = validate_artifact(jspec,path,CONTEXT)
                assert not result.valid and any(error['code']=='VERDICT_RESULT_CONFLICT' for error in result.errors), result.errors
                failed = dict(document,overall_verdict='FAIL',fail_count=1,subjects=[{'subject_id':'origin','verdict':'FAIL','evidence':'origin: Required field is missing.'}],criteria=[{'criterion_id':'schema','verdict':'FAIL','evidence':'origin: Required field is missing.'}],**{field:{'required_fields':False}})
                path = output_file(jspec,json.dumps(failed))
                result = validate_artifact(jspec,path,CONTEXT)
                assert result.valid, result.errors
        ''')

    def test_positive_verifier_detail_evidence_requires_text_not_nested_records(self):
        self.run_case('''
            jspec = OutputSpec('verdict','verdict.json','application/json',True)
            CONTEXT.data.update(artifact_kind='verdict',expected_criteria=('fidelity',),expected_subjects=('origin',))
            document = {'overall_verdict':'PASS','subjects':[{'subject_id':'origin','verdict':'PASS','evidence':'origin: Condition Q is required.'}],'criteria':[{'criterion_id':'fidelity','verdict':'PASS','evidence':'origin: Condition Q is required.'}]}
            for field, verdict in (('per_claim_verdicts','CONFIRMED'),('per_reasoning_step_verdicts','CONFIRMED'),('strategy_marker_verdicts','GROUNDED')):
                detail = {'verdict':verdict,'evidence':'origin: Condition Q is required.'}
                path = output_file(jspec,json.dumps(dict(document,**{field:[detail]})))
                result = validate_artifact(jspec,path,CONTEXT)
                assert result.valid, result.errors
                for evidence in ({'locator':'origin','span':'Condition Q is required.'},['Condition Q is required.'],' ',None,True,1):
                    path = output_file(jspec,json.dumps(dict(document,**{field:[dict(detail,evidence=evidence)]})))
                    result = validate_artifact(jspec,path,CONTEXT)
                    assert not result.valid and any(error['code']=='ARTIFACT_VERDICT_INVALID' for error in result.errors), (field,evidence,result.errors)
        ''')

    def test_claim_evaluation_is_not_a_positive_native_finding(self):
        self.run_case('''
            jspec = OutputSpec('claim','claim.json','application/json',True)
            claim = 'Condition Q is required.'
            document = {'claim_id':'c1','claim_sha256':sha256(claim.encode()).hexdigest(),'raw_ref':RAW.path,'raw_sha256':RAW.sha256,'claim_verdict':'VERIFIED','claim_severity':None,'evidence':claim,'reason':'Exact supplied passage.'}
            CONTEXT.data.update(artifact_kind='claim_evaluation',claim_id='c1',claim_sha256=document['claim_sha256'])
            path = output_file(jspec,json.dumps(document))
            result = validate_artifact(jspec,path,CONTEXT)
            assert result.valid, result.errors
            path = output_file(jspec,json.dumps(dict(document,evidence='Condition Q is optional.')))
            assert not validate_artifact(jspec,path,CONTEXT).valid
            path = output_file(jspec,json.dumps(dict(document,claim_verdict='INSUFFICIENT_DATA',raw_ref=None,raw_sha256=None,evidence=None)))
            assert validate_artifact(jspec,path,CONTEXT).valid
            path = output_file(jspec,json.dumps(dict(document,native_approved=True)))
            assert not validate_artifact(jspec,path,CONTEXT).valid
        ''')

    def test_capture_and_coverage_use_actual_evidence_validators(self):
        self.run_case('''
            jspec = OutputSpec('capture','capture.json','application/json',True)
            unit = {'unit_id':'u000001','source_order':1,'kind':'section','source_id':SID,'original_locator':{'path':RAW.path},'original_sha256':RAW.sha256,'expected_paths':[RAW.path],'expected_sha256':{RAW.path:RAW.sha256}}
            observed = dict(unit,produced_paths=[RAW.path],produced_sha256={RAW.path:RAW.sha256},coverage={'complete':True})
            inventory = {'schema_version':1,'expected_units':[unit],'units':[observed]}
            CONTEXT.data.update(artifact_kind='capture_inventory',capture_root=VAULT,capture_inventory=inventory)
            path = output_file(jspec,json.dumps(inventory))
            assert validate_artifact(jspec,path,CONTEXT).valid
            broken = dict(inventory,units=[])
            path = output_file(jspec,json.dumps(broken))
            assert not validate_artifact(jspec,path,CONTEXT).valid
            coverage_spec = OutputSpec('coverage','coverage.json','application/json',True)
            CONTEXT.data.update(artifact_kind='coverage',scope={'units':['u000001'],'topic':'conditions','artifact_root':str(VAULT),'capture_root':str(VAULT)})
            document = {'entries':[]}
            path = output_file(coverage_spec,json.dumps(document))
            assert not validate_artifact(coverage_spec,path,CONTEXT).valid
        ''')

    def test_binary_magic_and_independent_expected_bytes_are_checked(self):
        self.run_case('''
            binary = OutputSpec('original','original.pdf','application/pdf',True)
            payload = b'%PDF-1.7 synthetic fixture bytes'
            path = output_file(binary,payload)
            assert not validate_artifact(binary,path,CONTEXT).valid
            CONTEXT.data['expected_sha256'] = sha256(payload).hexdigest()
            assert validate_artifact(binary,path,CONTEXT).valid
            path.write_bytes(b'not a PDF')
            assert not validate_artifact(binary,path,CONTEXT).valid
        ''')

    def test_duplicate_yaml_json_fields_and_placeholders_refuse(self):
        self.run_case('''
            text = compilation().replace('type: compilation','type: compilation\\ntype: entity')
            path = output_file(SPEC,text)
            assert not validate_artifact(SPEC,path,CONTEXT).valid
            path = output_file(SPEC,compilation().replace('Condition Q applies','{unresolved}'))
            assert not validate_artifact(SPEC,path,CONTEXT).valid
            jspec = OutputSpec('report','report.json','application/json',True)
            path = output_file(jspec,'{"count":1,"count":2}')
            assert not validate_artifact(jspec,path,CONTEXT).valid
        ''')

    def test_explicit_note_subjects_and_source_author_are_not_inferred(self):
        self.run_case('''
            path = output_file(SPEC,compilation())
            supports(path)
            CONTEXT.data['expected_metadata'] = {'topic':'conditions','source_ids':[SID]}
            assert validate_artifact(SPEC,path,CONTEXT).valid
            CONTEXT.data['expected_metadata']['topic'] = 'different-topic'
            assert not validate_artifact(SPEC,path,CONTEXT).valid
            CONTEXT.data.pop('expected_metadata')
            CONTEXT.data['generator'] = 'Different Generator'
            assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_a_changed_schema_cannot_be_silently_resolved_again(self):
        self.run_case(r'''
            (VAULT/'.wiki-schema.md').write_text('---\nwiki_name: Changed\ndomains: [science]\n---\n',encoding='utf-8')
            rejected(lambda: prepare())
            path = output_file(SPEC,compilation())
            supports(path)
            assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')

    def test_code_comment_claims_cannot_become_content_check_evidence(self):
        self.run_case(r'''
            note = VAULT/'wiki/science/selected.md'
            note.parent.mkdir(parents=True)
            note.write_text('A visible condition.\n```\nHidden code claim.\n```\n<!-- Hidden comment claim. -->\n',encoding='utf-8')
            jspec = OutputSpec('claims','claims.json','application/json',True)
            CONTEXT.data.update(artifact_kind='claim_extraction',claim_source=ref(note))
            for claim,valid in (('A visible condition.',True),('Hidden code claim.',False),('Hidden comment claim.',False)):
                document = {'claims':[{'claim_id':'c1','text':claim,'claim_sha256':sha256(claim.encode()).hexdigest()}]}
                path = output_file(jspec,json.dumps(document))
                assert validate_artifact(jspec,path,CONTEXT).valid is valid
        ''')

    def test_inconsistent_report_counter_types_and_native_verdict_labels_refuse(self):
        self.run_case('''
            jspec = OutputSpec('report','report.json','application/json',True)
            path = output_file(jspec,json.dumps({'selected_count':True}))
            assert not validate_artifact(jspec,path,CONTEXT).valid
            CONTEXT.data['artifact_kind'] = 'verdict'
            for document in ({'overall_verdict':'approve'},{'overall_verdict':'PASS','fail_count':1},{'overall_verdict':'PASS','frontmatter_checks':{'checked':'true'}}):
                path = output_file(jspec,json.dumps(document))
                assert not validate_artifact(jspec,path,CONTEXT).valid
        ''')

    def test_evidence_maps_bind_tasks_without_impersonating_canonical_notes(self):
        self.run_case(r'''
            map_spec = OutputSpec('map','gathered.md','text/markdown',True)
            task = prepare(duty='gather',outputs=(map_spec,))
            CONTEXT.data.update(task=task,artifact_kind='evidence_map',expected_subjects=('origin',),required_sections=('raws','relevance_summary'))
            metadata = {key:getattr(task,key) for key in ('run_id','step_id','item_id','attempt_id')}
            metadata.update(task_sha256=task.semantic_sha256,subjects=['origin'])
            body = '## raws\n\n| slug | contribution |\n|---|---|\n| origin | Condition Q |\n\n## relevance_summary\n\nOne assigned original was read.\n'
            path = output_file(map_spec,render(metadata,body))
            assert validate_artifact(map_spec,path,CONTEXT).valid
            path = output_file(map_spec,render(dict(metadata,attempt_id='stale'),body))
            assert not validate_artifact(map_spec,path,CONTEXT).valid
            CONTEXT.data['artifact_kind'] = 'note'
            assert not validate_artifact(map_spec,path,CONTEXT).valid
        ''')

    def test_wrapper_generator_does_not_replace_originating_author(self):
        self.run_case(r'''
            CONTEXT.data.update(output_type='source',artifact_role='capture',artifact_tier='A',output_paths={'note':'raw/documents/wrapper.md'},source_snapshot=RAW,author_role='generator',source_author='Original Author',generator='Synthetic Generator')
            metadata = {'type':'source','artifact_role':'capture','source_ids':[SID], 'title':'Original','domain':'science','source':'Supplied original','source_class':'documents','provenance_class':'document','raw_modality':'documents','source_hash':RAW.sha256,'source_hash_kind':'bytes','source_hash_algorithm':'sha256','ingested':'2026-10-16','backfill':False,'source_file':RAW.path,'vault':'SyntheticLib','author':'Synthetic Generator','source_authors':['Original Author'],'pipeline_id':RUN,'status':'draft','last_verified':None,'freshness':'permanent','confidence':'high','human_edited':False,'tags':['domain/science','topic/conditions','type/source','vault/SyntheticLib']}
            metadata['content_hash'] = content_hash(render(metadata,'Faithful source bytes.\n'))
            path = output_file(SPEC,render(metadata,'Faithful source bytes.\n'))
            assert validate_artifact(SPEC,path,CONTEXT).valid
            metadata['source_authors'] = []
            path = output_file(SPEC,render(metadata,'Faithful source bytes.\n'))
            assert not validate_artifact(SPEC,path,CONTEXT).valid
        ''')




SETUP = r'''
from pathlib import Path
from hashlib import sha256
from types import SimpleNamespace
import dataclasses
import json
import yaml
from _workflow.contracts import ContractError, InputRef, OutputSpec, semantic_hash
from wiki.runtime.schema import resolve_schema
from wiki.runtime.tasks import DUTIES, prepare_task, validate_artifact
from wiki.runtime.provenance import content_hash, source_id
from wiki.runtime.citations import extract_citations

RUN = 'wiki-ingest-synthetic-run'
PROJECT = ROOT/'project'
PROJECT.mkdir()
VAULT = ROOT/'vault'
VAULT.mkdir()
(VAULT/'.wiki-schema.md').write_bytes(b'---\nwiki_name: SyntheticLib\nextends: sibling-canonical\nextends_version: "3.1"\ndomains: [science]\ntopics:\n  science: [conditions]\n---\n')
RESOLVED = resolve_schema(VAULT,'PhysicalSelection')
SID = source_id({'kind':'canonical-reference','id':'synthetic://origin'})
CANDIDATE = PROJECT/'docs/tmp/pipeline-runs'/RUN/'attempts'/'attempt-1'/'candidates'
raw_path = VAULT/'raw/documents/origin.md'
raw_path.parent.mkdir(parents=True)
raw_path.write_bytes(b'---\ntype: source\nartifact_role: capture\nauthor: Original Author\n---\nCondition Q is required.\n')

def ref(path):
    return InputRef(str(path),sha256(path.read_bytes()).hexdigest(),'text/markdown')
RAW = ref(raw_path)
SPEC = OutputSpec('note','note.md','text/markdown',True)
CONTEXT = SimpleNamespace(request=SimpleNamespace(project_root=PROJECT,profile=None),run_id=RUN,step_id='L3',item_id='u000001-conditions',attempt_id='attempt-1',candidate_dir=CANDIDATE,skills_root=SKILLS,data={'resolved_vault':RESOLVED,'artifact_role':'topic_distill','output_type':'compilation','artifact_tier':'B','raw_inputs':(RAW,),'evidence_root':VAULT,'output_paths':{'note':f'wiki/science/conditions/sources/{SID}-u000001.md'},'path_values':{'domain':'science','topic-slug':'conditions','source-id':SID,'unit-id':'u000001'}})

def rejected(action):
    try:
        action()
    except (ValueError,OSError,TypeError):
        return
    raise AssertionError('unsafe preparation was accepted')

def prepare(**changes):
    options = dict(duty='distill',instructions='Distill the complete supplied conditions unit and preserve condition Q.',inputs=(RAW,),outputs=(SPEC,),artifact_id='ingest:'+RUN+':note:u000001',review_contract='vault-wiki',artifact_tier='B')
    options.update(changes)
    return prepare_task(CONTEXT,**options)

def render(metadata,body):
    return '---\n'+yaml.safe_dump(metadata,sort_keys=False,allow_unicode=True)+'---\n'+body

def compilation(changes=None):
    metadata = {'type':'compilation','artifact_role':'topic_distill','source_ids':[SID],'title':'Conditions','concept':'Condition requirements','domain':'science','topic':'conditions','sources':1,'contributing_sources':['Original source'],'compiled_from':[{'slug':'origin','source_class':'documents','citation_count':1}],'vault':'SyntheticLib','author':'Synthetic Generator','pipeline_id':RUN,'created':'2026-10-16','updated':'2026-10-16','last_compiled':'2026-10-16','last_verified':None,'status':'draft','contradictions':0,'open_questions':0,'related':[],'freshness':'monthly','confidence':'medium','human_edited':False,'tags':['domain/science','topic/conditions','type/compilation','vault/SyntheticLib']}
    metadata.update(changes or {})
    return render(metadata,'# Conditions\n\n## Overview\n\nCondition Q applies. [PARAPHRASE] ^[[[origin]]]\n')

def output_file(spec,payload):
    path = CANDIDATE/spec.relative_path
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(payload if isinstance(payload,bytes) else payload.encode('utf-8'))
    return path

def supports(path):
    text = path.read_bytes().decode('utf-8')
    raw = Path(RAW.path).read_bytes().decode('utf-8')
    span = 'Condition Q is required.'
    bindings = []
    for citation in extract_citations(text):
        start = text.rfind('\n',0,citation.start)+1
        end = text.find('\n',citation.end)
        if end < 0: end = len(text)
        bindings.append({'citation_start':citation.start,'raw_path':RAW.path,'raw_sha256':RAW.sha256,'start':raw.index(span),'end':raw.index(span)+len(span),'span':span,'claim_start':start,'claim_end':end,'claim_sha256':sha256(text[start:end].encode('utf-8')).hexdigest()})
    CONTEXT.data['citation_bindings'] = bindings
'''


if __name__ == '__main__':
    unittest.main()
