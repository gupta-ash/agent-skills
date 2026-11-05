"""Isolated byte/identity regressions; synthetic receipts are NOT native proof."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[2]


class CitationProvenanceTests(unittest.TestCase):
    def run_case(self, code):
        with tempfile.TemporaryDirectory(prefix="wiki-citation-test-") as temporary:
            base = Path(temporary)
            export = base / "skills"
            (export / "_workflow").mkdir(parents=True)
            (export / "wiki" / "runtime").mkdir(parents=True)
            shutil.copyfile(REPO / "_shared/workflow/src/contracts.py", export / "_workflow/contracts.py")
            for name in ("store", "publication"):
                shutil.copyfile(REPO / f"_shared/workflow/src/{name}.py", export / f"_workflow/{name}.py")
            for name in ("capture", "citations", "provenance"):
                shutil.copyfile(REPO / f"wiki/runtime/{name}.py", export / f"wiki/runtime/{name}.py")
            env = dict(os.environ)
            for key in list(env):
                if key.startswith("VAULT_"):
                    del env[key]
            for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TMP", "TEMP", "TMPDIR"):
                destination = base / key.lower()
                destination.mkdir()
                env[key] = str(destination)
            fixture = base / "fixture"
            fixture.mkdir()
            prelude = f"import sys\nsys.path.insert(0, {str(export)!r})\nROOT = __import__('pathlib').Path({str(fixture)!r})\n"
            result = subprocess.run([sys.executable, "-I", "-B", "-c", prelude + SETUP + textwrap.dedent(code)], env=env, cwd=fixture, capture_output=True, text=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_only_real_markers_outside_code_comments_frontmatter(self):
        self.run_case(r'''
            text = '\n'.join(['---','title: ^[[[hidden]]]','---',
                'A claim ^[[[origin-1]]]. Legacy ^[[origin-2]].',
                'Navigation [[origin-3]] and [link](origin-4.md).',
                '`inline ^[[[hidden]]]`',
                '````python','^[[[hidden]]]','```','^[[[hidden]]]','````',
                '~~~','^[[[hidden]]]','~~~',
                '    indented ^[[[hidden]]]',
                '<!-- multiline','^[[[hidden]]]','-->',
                '<pre>^[[[hidden]]]</pre>', '<code>^[[[hidden]]]</code>',
                r'Escaped \^[[[hidden]]] and aliases ^[[[origin|alias]]].'])
            citations = extract_citations(text)
            assert [(c.slug,c.legacy) for c in citations] == [('origin-1',False),('origin-2',True)]
            for citation in citations:
                assert text[citation.start:citation.end].startswith('^[[')
        ''')

    def test_unique_actual_raw_not_navigation_or_generated_note(self):
        self.run_case(r'''
            first = raw('raw/documents/shared.md', 'Condition Q is required.\n')
            second = raw('raw/books/shared.md', 'A different original.\n')
            try:
                resolve_raw_reference('shared', [first,second], ROOT)
                raise AssertionError('ambiguous stem accepted')
            except CitationError: pass
            qualified = resolve_raw_reference('raw/documents/shared', [first,second], ROOT)
            assert qualified.path == first.path
            assert resolve_raw_reference('shared', [first,first], ROOT).path == first.path
            for name, text in [('raw/nav.md','---\ntype: moc\n---\ncondition Q'),('raw/bom.md','\ufeff---\ntype: moc\n---\ncondition Q'),('raw/duplicate.md','---\ntype: moc\ntype: source\n---\ncondition Q'),('raw/derived.md','---\ntype: compilation\nartifact_role: topic_distill\n---\ncondition Q')]:
                ref = input_ref(write(name,text))
                try:
                    resolve_raw_reference(Path(name).stem, [ref], ROOT)
                    raise AssertionError('navigation/derived raw accepted')
                except CitationError: pass
            (ROOT/'raw/documents/shared.md').write_text('changed',encoding='utf-8')
            try:
                resolve_raw_reference('shared',[first],ROOT)
                raise AssertionError('stale raw hash accepted')
            except ValueError: pass
        ''')

    def test_canonical_book_citations_bind_private_raw_members_without_stem_collision(self):
        self.run_case(r'''
            first = raw('private/one/u000001.md','Condition Q is required.\n')
            second = raw('private/two/u000001.md','Condition Q is required.\n')
            targets = {'raw/books/one/ch01-intro':first,'raw/books/two/ch01-intro':second}
            assert resolve_raw_reference('raw/books/one/ch01-intro',[first,second],ROOT,citation_targets=targets).path == first.path
            try:
                resolve_raw_reference('ch01-intro',[first,second],ROOT,citation_targets=targets)
            except CitationError: pass
            else: raise AssertionError('distinct book chapters collapsed by leaf name')
            text = 'A condition applies. ^[[[raw/books/one/ch01-intro]]]'
            binding = support(text,first,'Condition Q is required.')
            assert validate_citations(text,[first,second],ROOT,[binding],citation_targets=targets).valid
            assert not validate_citations(text,[first,second],ROOT,[binding]).valid
            Path(first.path).write_bytes(b'changed native raw bytes')
            assert not validate_citations(text,[first,second],ROOT,[binding],citation_targets=targets).valid
        ''')

    def test_citation_aliases_cannot_grant_roots_forge_membership_or_shadow_physical_raw(self):
        self.run_case(r'''
            first = raw('private/capture.md','Condition Q is required.\n')
            foreign = raw('private/foreign.md','A different condition.\n')
            key = 'raw/books/one/ch01-intro'
            for targets in ({key:foreign}, {key:InputRef(first.path,'0'*64,first.media_type)},
                            {key:InputRef(first.path,first.sha256,'application/json')},
                            {'raw/../private/capture':first}, {'private/capture':first}):
                try:
                    resolve_raw_reference(key,[first],ROOT,citation_targets=targets)
                except ValueError: pass
                else: raise AssertionError('forged/unsafe canonical alias accepted')
            physical = raw(key+'.md','Different canonical evidence.\n')
            try:
                resolve_raw_reference(key,[first,physical],ROOT,citation_targets={key:first})
            except CitationError: pass
            else: raise AssertionError('alias shadowed another actual canonical raw')
            outside = ROOT.parent/'outside.md'
            outside.write_bytes(b'Original outside text')
            ref = input_ref({'path':str(outside),'sha256':sha256(outside.read_bytes()).hexdigest()})
            try:
                resolve_raw_reference(key,[ref],ROOT,citation_targets={key:ref})
            except ValueError: pass
            else: raise AssertionError('an alias granted a new evidence root')
        ''')

    def test_exact_raw_spans_reject_changed_text_offsets_and_navigation(self):
        self.run_case(r'''
            ref = raw('raw/origin.md','Évidence: Condition Q is required.\n[[navigation-target]]\n')
            text = Path(ref.path).read_text(encoding='utf-8')
            span = 'Condition Q is required.'
            start = text.index(span)
            assert validate_raw_span(ref,ROOT,span,start,start+len(span)).valid
            assert not validate_raw_span(ref,ROOT,'Condition Q is optional.',start,start+len(span)).valid
            assert not validate_raw_span(ref,ROOT,span,start+1,start+1+len(span)).valid
            span = '[[navigation-target]]'
            start = text.index(span)
            assert not validate_raw_span(ref,ROOT,span,start,start+len(span)).valid
            assert not validate_raw_span(ref,ROOT,' ',0,1).valid
        ''')

    def test_source_metadata_and_code_examples_are_citable(self):
        self.run_case(r'''
            ref = input_ref(write('raw/index.md','---\ntype: source\nauthor: Original Author\n---\n```python\nif condition_Q:\n    cost = 20\n```\n'))
            assert resolve_raw_reference('index',[ref],ROOT).path == ref.path
            text = Path(ref.path).read_text(encoding='utf-8')
            for span in ('author: Original Author','if condition_Q:\n    cost = 20'):
                start = text.index(span)
                assert validate_raw_span(ref,ROOT,span,start,start+len(span)).valid
        ''')

    def test_claim_and_raw_hash_bound_support_is_exact_not_truth(self):
        self.run_case(r'''
            ref = raw('raw/origin.md','Condition Q is required.\n')
            text = 'A condition applies. ^[[[origin]]]'
            binding = support(text,ref,'Condition Q is required.')
            assert validate_citations(text,[ref],ROOT,[binding]).valid
            assert not validate_citations(text,[ref],ROOT,[]).valid
            assert not validate_citations(text,[ref],ROOT,[binding,binding]).valid
            stale = dict(binding,claim_sha256='0'*64)
            assert not validate_citations(text,[ref],ROOT,[stale]).valid
            stale = dict(binding,raw_sha256='0'*64)
            assert not validate_citations(text,[ref],ROOT,[stale]).valid
            assert not validate_citations(text.replace('condition','requirement'),[ref],ROOT,[binding]).valid
            phantom = dict(binding,citation_start=binding['citation_start']+1)
            assert not validate_citations(text,[ref],ROOT,[phantom]).valid
            assert not validate_citations('Navigation [[origin]]',[ref],ROOT,[binding]).valid
        ''')

    def test_source_identity_and_lineage_never_count_chapters_as_origins(self):
        self.run_case(r'''
            material = {'kind':'canonical-reference','id':'https://example.invalid/é?edition=1'}
            sid = source_id(material)
            expected = sha256(json.dumps(material,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode('utf-8')).hexdigest()
            assert sid == expected and len(sid) == 64
            assert sid == source_id(dict(reversed(list(material.items()))))
            assert sid != source_id(dict(material,id=material['id']+'2'))
            assert validate_source_identity(material,sid).valid
            assert not validate_source_identity(dict(material,id='different-origin'),sid).valid
            assert not validate_source_identity(material,'chapter-1').valid
            summary = lineage_summary([sid,sid,sid])
            assert summary['origin_count'] == 1 and summary['independent_lineage_count'] is None
            assert summary['synthesis_scope'] == 'single_origin'
            other = source_id({'kind':'provider','id':'second-source'})
            summary = lineage_summary([sid,other],{sid:'upstream-shared',other:'upstream-shared'})
            assert summary['origin_count'] == 2 and summary['independent_lineage_count'] == 1
            assert lineage_summary([sid,other])['independent_lineage_count'] is None
            assert lineage_summary([sid,other],{sid:'independent-a',other:'independent-b'})['independent_lineage_count'] == 2
            for material in ({'kind':'chapter','id':'x','chapter':1},{'kind':'','id':'x'},{}):
                try: source_id(material)
                except ValueError: pass
                else: raise AssertionError('malformed identity accepted')
        ''')

    def test_body_transport_and_authority_hash_domains_do_not_mix(self):
        self.run_case(r'''
            one = '---\ntitle: One\n---\nBody\n'
            two = '---\r\ntitle: Two\r\n---\r\nBody\r\n'
            assert content_hash(one) == content_hash(two)
            assert content_hash(one) == 'sha256:'+sha256(b'Body\n').hexdigest()
            assert source_hash(one.encode())['source_hash'] != source_hash(two.encode())['source_hash']
            assert source_hash(b'A\r\n','text-lf') == source_hash(b'A\n','text-lf')
            assert not compare_source_hashes(source_hash(b'A\n'),source_hash(b'A\n','text-lf')).valid
            authority = {'source_sha256':'a'*64,'source_hash_authority':'corpus:MANIFEST.md','content_hash':content_hash(one)}
            result = compare_source_hashes(authority,source_hash(b'A\n'))
            assert not result.valid and result.errors[0]['code'] == 'BASELINE_UNAVAILABLE'
            assert authority['source_sha256'] == 'a'*64
            assert compare_source_hashes(source_hash(b'A'),source_hash(b'A')).valid
            assert not compare_source_hashes(source_hash(b'A'),source_hash(b'B')).valid
        ''')

    def test_lifecycle_projection_preserves_native_bytes_and_requires_explicit_evidence_scope(self):
        self.run_case(r'''
            for date_only in (False, True):
                accepted, private, native, candidate = projection_fixture(date_only=date_only)
                original = native.read_bytes()
                result = validate_artifact_record(accepted, ROOT, require_acceptance=True, evidence_roots=(private,))
                assert result.valid, result.errors
                assert native.read_bytes() == original
                assert not validate_artifact_record(accepted, ROOT, require_acceptance=True).valid
                candidate.unlink() # Actual canonical H1 suffices after private duplicate cleanup.
                assert validate_artifact_record(accepted, ROOT, require_acceptance=True, evidence_roots=(private,)).valid
                native.write_bytes(original + b'changed source')
                assert not validate_artifact_record(accepted, ROOT, require_acceptance=True, evidence_roots=(private,)).valid
        ''')

    def test_lifecycle_projection_rejects_unauthorized_derivation_and_malformed_gate_scope(self):
        self.run_case(r'''
            for mutation in ('metadata', 'wide-span', 'body', 'time', 'status', 'gate-scope', 'malformed-ref'):
                accepted, private, native, candidate = projection_fixture(mutation=mutation)
                result = validate_artifact_record(accepted, ROOT, require_acceptance=True, evidence_roots=(private,))
                assert not result.valid, mutation
        ''')

    def test_draft_is_not_accepted_and_frozen_inputs_are_immutable(self):
        self.run_case(r'''
            record, refs = artifact_fixture()
            assert validate_artifact_record(record,ROOT).valid
            assert not validate_artifact_record(record,ROOT,require_acceptance=True).valid
            assert record.accepted_receipts == ()
            try: record.source_ids += ('x',)
            except FrozenInstanceError: pass
            else: raise AssertionError('mutable artifact')
            (ROOT/'raw/origin.md').write_text('changed',encoding='utf-8')
            assert not validate_artifact_record(record,ROOT).valid
        ''')

    def test_synthetic_receipts_only_check_current_binding_not_native_authenticity(self):
        self.run_case(r'''
            record, refs = artifact_fixture()
            accepted = synthetic_receipts(record,refs)
            result = validate_artifact_record(accepted,ROOT,require_acceptance=True)
            assert result.valid # Structure-only unit fixture, NOT genuine native acceptance.
            assert any(d['code']=='PROVENANCE_BINDING_ONLY' for d in result.diagnostics)
            stale_output = replace(accepted,sha256='0'*64)
            assert not validate_artifact_record(stale_output,ROOT,require_acceptance=True).valid
            changed = input_ref(write('raw/origin.md','---\ntype: source\n---\nNew evidence.\n'))
            stale_input = replace(accepted,raw_inputs=(changed,))
            assert not validate_artifact_record(stale_input,ROOT,require_acceptance=True).valid
        ''')

    def test_missing_stale_and_duplicate_receipt_membership_refused(self):
        self.run_case(r'''
            record, refs = artifact_fixture()
            accepted = synthetic_receipts(record,refs)
            for mutation in ('missing','duplicate','coverage','inputs','wrong_output','origins'):
                if mutation == 'missing':
                    changed = replace(accepted,accepted_receipts=accepted.accepted_receipts[:-1])
                elif mutation == 'duplicate':
                    changed = replace(accepted,accepted_receipts=accepted.accepted_receipts+(accepted.accepted_receipts[0],))
                else:
                    receipts = list(accepted.accepted_receipts)
                    data = json.loads(Path(receipts[0].path).read_text(encoding='utf-8'))
                    if mutation == 'coverage': data['coverage_ref'] = None
                    if mutation == 'inputs': data['inputs'] = []
                    if mutation == 'wrong_output': data['output_sha256'] = 'f'*64
                    if mutation == 'origins': data['source_ids'] = ['f'*64]
                    receipts[0] = input_ref(write('receipts/changed.json',json.dumps(data)))
                    changed = replace(accepted,accepted_receipts=tuple(receipts))
                assert not validate_artifact_record(changed,ROOT,require_acceptance=True).valid, mutation
        ''')

    def test_delivered_fail_no_review_wrong_contract_or_unreadable_evidence_refuse(self):
        self.run_case(r'''
            record, refs = artifact_fixture()
            for change in ('delivered_fail','no_review','wrong_status','wrong_contract','loop','missing_evidence','stale_evidence'):
                accepted = synthetic_receipts(record,refs,change)
                assert not validate_artifact_record(accepted,ROOT,require_acceptance=True).valid, change
            accepted = synthetic_receipts(record,refs,'nonblocking')
            assert validate_artifact_record(accepted,ROOT,require_acceptance=True).valid
            # Empty severities never override delivered fail, and native producer
            # fields are consumed unchanged rather than a nested review.verdict.
        ''')

    def test_private_receipts_require_explicit_root_without_expanding_raw_or_output_authority(self):
        self.run_case(r'''
            record, refs = artifact_fixture()
            accepted = synthetic_receipts(record, refs)
            private = ROOT.parent / 'private-evidence'
            private.mkdir()
            def preserve(ref, name):
                destination = private / name
                destination.write_bytes(Path(ref['path']).read_bytes())
                return {'path': str(destination), 'sha256': ref['sha256']}
            coverage = preserve(refs['coverage'], 'coverage.json')
            receipts = []
            native = None
            for original in accepted.accepted_receipts:
                receipt = json.loads(Path(original.path).read_text(encoding='utf-8'))
                receipt['coverage_ref'] = coverage
                if receipt['gate'] == 'review':
                    evidence = [preserve(ref, Path(ref['path']).name) for ref in receipt['producer_evidence']]
                    receipt['producer_evidence'] = evidence
                    receipt['producer']['result_path'] = evidence[0]['path']
                    receipt['producer']['receipt_path'] = evidence[1]['path']
                    native = Path(evidence[0]['path'])
                path = private / (receipt['gate'] + '-gate.json')
                path.write_text(json.dumps(receipt), encoding='utf-8')
                receipts.append(InputRef(str(path), sha256(path.read_bytes()).hexdigest(), 'application/json'))
            accepted = replace(accepted, accepted_receipts=receipts, coverage_ref=input_ref(coverage))
            assert not validate_artifact_record(accepted, ROOT, require_acceptance=True).valid
            validation = validate_artifact_record(accepted, ROOT, require_acceptance=True, evidence_roots=(private,))
            assert validation.valid # Byte binding only; synthetic receipts are NOT native authenticity.
            output = preserve({'path': accepted.path, 'sha256': accepted.sha256}, 'outside-output.md')
            assert not validate_artifact_record(replace(accepted, path=output['path']), ROOT,
                require_acceptance=True, evidence_roots=(private,)).valid
            outside_raw = input_ref(preserve(refs['raw'], 'outside-raw.md'))
            assert not validate_artifact_record(replace(accepted, raw_inputs=(outside_raw,)), ROOT,
                require_acceptance=True, evidence_roots=(private,)).valid
            native.write_text('changed retained evidence', encoding='utf-8')
            assert not validate_artifact_record(accepted, ROOT, require_acceptance=True,
                evidence_roots=(private,)).valid
        ''')


    def test_native_compact_alias_uses_exact_retained_copy_without_private_root_read(self):
        self.run_case(r'''
            record, refs = artifact_fixture()
            accepted = synthetic_receipts(record, refs)
            aliases, receipts = {}, []
            for ref in accepted.accepted_receipts:
                path = Path(ref.path)
                receipt = json.loads(path.read_text(encoding='utf-8'))
                if receipt['gate'] == 'review':
                    for field, evidence in zip(('result_path','receipt_path'), receipt['producer_evidence']):
                        original = str(ROOT.parent / 'unavailable-native-home' / Path(evidence['path']).name)
                        assert not Path(original).exists()
                        receipt['producer'][field] = original
                        aliases[original] = input_ref(evidence)
                    path.write_text(json.dumps(receipt), encoding='utf-8')
                receipts.append(InputRef(str(path), sha256(path.read_bytes()).hexdigest(), 'application/json'))
            accepted = replace(accepted, accepted_receipts=receipts)
            assert not validate_artifact_record(accepted,ROOT,require_acceptance=True).valid
            assert validate_artifact_record(accepted,ROOT,require_acceptance=True,
                native_evidence_aliases=aliases).valid # Synthetic byte binding, not native authenticity.
            altered = dict(aliases)
            key = next(iter(altered))
            altered[key] = replace(altered[key],sha256='0'*64)
            assert not validate_artifact_record(accepted,ROOT,require_acceptance=True,
                native_evidence_aliases=altered).valid
        ''')

SETUP = r'''
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
from hashlib import sha256
import json
from pathlib import Path
from _workflow.contracts import InputRef
from wiki.runtime.citations import CitationError, extract_citations, resolve_raw_reference, validate_raw_span, validate_citations
from wiki.runtime.provenance import WikiArtifactRecord, source_id, validate_source_identity, lineage_summary, content_hash, source_hash, compare_source_hashes, validate_artifact_record

def write(name,text):
    path = ROOT/name
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_bytes(text.encode('utf-8'))
    return {'path':str(path),'sha256':sha256(path.read_bytes()).hexdigest()}

def input_ref(ref):
    return InputRef(ref['path'],ref['sha256'],'text/markdown' if ref['path'].endswith('.md') else 'application/json')

def raw(name,body):
    return input_ref(write(name,'---\ntype: source\n---\n'+body))

def support(text,ref,span):
    raw_text = Path(ref.path).read_text(encoding='utf-8')
    start = raw_text.index(span)
    citation = extract_citations(text)[0]
    return {'citation_start':citation.start,'raw_path':ref.path,'raw_sha256':ref.sha256,'start':start,'end':start+len(span),'span':span,'claim_start':0,'claim_end':len(text),'claim_sha256':sha256(text.encode('utf-8')).hexdigest()}

def artifact_fixture():
    original = raw('raw/origin.md','Condition Q is required.\n')
    output = write('wiki/distill.md','---\ntype: compilation\nartifact_role: topic_distill\nstatus: draft\n---\nCondition Q applies. ^[[[origin]]]\n')
    coverage = input_ref(write('coverage.json',json.dumps({'scope':'synthetic unit fixture'})))
    sid = source_id({'kind':'canonical-reference','id':'local:synthetic-origin'})
    record = WikiArtifactRecord('ingest:test:note:a','topic_distill',output['path'],output['sha256'],[sid],'alpha',[original],[],coverage)
    return record, {'raw':{'path':original.path,'sha256':original.sha256},'coverage':{'path':coverage.path,'sha256':coverage.sha256}}

def synthetic_receipts(record,refs,change=None):
    native_result = write('native/result.json','{"synthetic":true,"native_authenticity":false}')
    native_receipt = write('native/receipt.json','{"synthetic":true,"native_authenticity":false}')
    producer = {'status':'reviewed','verdict':'approve','exitCode':0,'loop_should_continue':False,'result_path':native_result['path'],'receipt_path':native_receipt['path'],'counts':{'critical':0,'high':0,'medium':0}}
    if change=='delivered_fail': producer.update(verdict='fail',exitCode=4,review={'verdict':'approve'})
    if change=='no_review': producer.update(status='tooling-failure',verdict=None,exitCode=1)
    if change=='wrong_status': producer['status']='completed'
    if change=='loop': producer['loop_should_continue']=True
    if change=='nonblocking': producer.update(verdict='needs-attention',exitCode=2)
    receipts=[]
    for gate in ('capture','fidelity','coverage','review'):
        data={'gate':gate,'artifact_id':record.artifact_id,'output_sha256':record.sha256,'source_ids':list(record.source_ids),'inputs':[refs['raw']],'coverage_ref':refs['coverage'],'status':'completed','verification':'PASS'}
        if gate=='review':
            data.update(contract='wrong' if change=='wrong_contract' else 'vault-wiki',producer=producer,producer_evidence=[native_result,native_receipt])
            if change=='missing_evidence': data.pop('producer_evidence')
            if change=='stale_evidence': data['producer_evidence'][0]=dict(native_result,sha256='0'*64)
        receipts.append(input_ref(write(f'receipts/{gate}.json',json.dumps(data))))
    return replace(record,accepted_receipts=receipts)

def projection_fixture(*, date_only=False, mutation=None):
    record, refs = artifact_fixture()
    private = ROOT.parent / 'private-projection'
    private.mkdir(exist_ok=True)
    native, candidate = private/'native.md', private/'projected.md'
    original = ('\ufeff---\r\ntype: compilation\r\nartifact_role: topic_distill\r\ntitle: Évidence\r\n'
                'status: draft\r\nlast_verified: null\r\n---\r\nCondition Q applies. ^[[[origin]]]\r\n').encode('utf-8')
    native.write_bytes(original)
    stamp = '2026-10-17' if date_only else '2026-10-17T03:00:00Z'
    replacements = [(b'draft', b'draft' if mutation == 'status' else b'published'),
                    (b'null', ('2020-01-01' if mutation == 'time' else stamp).encode())]
    if mutation == 'metadata':
        replacements[0] = ('title: Évidence\r\nstatus: draft'.encode(), b'title: Forged\r\nstatus: published')
    if mutation == 'wide-span':
        replacements[0] = (b'status: draft', b'status: published')
    edits = [{'start': original.index(before), 'end': original.index(before) + len(before),
              'before_hex': before.hex(), 'after_hex': after.hex()} for before, after in replacements]
    payload, previous = b'', 0
    for edit in edits:
        payload += original[previous:edit['start']] + bytes.fromhex(edit['after_hex'])
        previous = edit['end']
    payload += original[previous:]
    if mutation == 'body': payload += b'unsupported body change'
    candidate.write_bytes(payload)
    Path(record.path).write_bytes(payload)
    record = replace(record, sha256=sha256(payload).hexdigest())
    def full(path):
        return {'path':str(path), 'sha256':sha256(path.read_bytes()).hexdigest(),
                'media_type':'text/markdown' if path.suffix == '.md' else 'application/json'}
    proof = {'schema_version':1, 'projection_id':'synthetic-projection', 'producer_attempt_id':'synthetic-native',
             'source':dict(full(native), name='note'), 'candidate':dict(full(candidate), name='projected-note'),
             'edits':edits, 'kind':'wiki-lifecycle', 'changed_fields':['status','last_verified'],
             'accepted_at':'2026-10-17T03:00:00Z', 'gate_refs':[dict(refs['coverage'],media_type='application/json')],
             'native_scope':'original candidate H0; H1 has deterministic earned lifecycle metadata only'}
    proof_path = private/'projection.json'
    proof_path.write_bytes(json.dumps(proof, ensure_ascii=False).encode())
    accepted = synthetic_receipts(record, refs)
    receipts = []
    for index, ref in enumerate(accepted.accepted_receipts):
        data = json.loads(Path(ref.path).read_bytes())
        data.update(native_producer_ref=full(native), projection_id=proof['projection_id'], projection_ref=full(proof_path))
        if index == 1 and mutation == 'gate-scope': data['projection_id'] = 'different-projection'
        if index == 1 and mutation == 'malformed-ref': data['native_producer_ref'] = 'not-an-input-ref'
        receipts.append(input_ref(write(f'receipts/projected-{index}.json',json.dumps(data))))
    return replace(accepted, accepted_receipts=receipts), private, native, candidate
'''


if __name__ == '__main__':
    unittest.main()
