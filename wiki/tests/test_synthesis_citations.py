"""Isolated real workflow consumers; synthetic receipts are NOT native qualification."""
import unittest

from test_citations_provenance import SETUP as RECEIPT_SETUP
import test_workflows as workflow_tests


class SynthesisCitationTests(unittest.TestCase):
    # Reuse the installed-layout subprocess fixture, without inheriting its tests.
    run_case = workflow_tests.WorkflowTests.run_case

    def check_case(self, code):
        self.run_case('RECEIPT_SETUP = ' + repr(RECEIPT_SETUP) + '\n' + SETUP + code)

    def test_qualified_same_basename_records_reach_hydrated_span_validation(self):
        self.check_case('''
first, second = accepted('a'), accepted('b')
result = validate([first, second], ['wiki/a/alpha-compilation', 'wiki/b/alpha-compilation'])
assert result.valid, result.errors
# Both exact supporting spans must remain current, not merely their citation keys.
result = validate([first, second], ['wiki/a/alpha-compilation', 'wiki/b/alpha-compilation'], mutation='span')
assert any(e['code'] == 'CITATION_BINDING_INVALID' for e in result.errors), result.errors
''')

    def test_unique_bare_citation_uses_canonical_binding_map(self):
        self.check_case('''
first = accepted('a')
result = validate([first], ['alpha-compilation'])
assert result.valid, result.errors
''')

    def test_ambiguous_bare_refuses_before_record_selection_or_raw_fallback(self):
        self.check_case('''
first, second = accepted('a'), accepted('b')
# Supply an otherwise usable same-named raw: ambiguity cannot escape into fallback.
raw_path = note('raw/alpha-compilation.md', 'Condition a is required.', {'type':'source'})
raw_ref = w._ref(raw_path)
result = validate([first, second], ['alpha-compilation'], fallback=raw_ref)
assert any(e['code'] == 'CITATION_SYNTHESIS_AMBIGUOUS' for e in result.errors), result.errors
''')

    def test_stale_wrong_role_and_missing_gate_bindings_are_not_accepted(self):
        self.check_case('''
first = accepted('a')
for mutation in ('stale', 'role', 'gate', 'key'):
    result = validate([first], ['wiki/a/alpha-compilation'], mutation=mutation)
    assert not result.valid, mutation
    assert any(e['code'] in ('ARTIFACT_BINDING_INVALID', 'ARTIFACT_INVALID') for e in result.errors), (mutation, result.errors)
''')



SETUP = r'''
from dataclasses import asdict
from _workflow.contracts import InputRef, OutputSpec
from wiki.runtime.citations import extract_citations

# Every gate below is synthetic binding data, not a native review observation.
def accepted(label):
    fixture = VAULT/'fixtures'/label
    fixture.mkdir(parents=True)
    helpers = {'ROOT':fixture}
    exec(RECEIPT_SETUP, helpers)
    record, refs = helpers['artifact_fixture']()
    text = '---\ntype: compilation\nartifact_role: synthesis\n---\nCondition '+label+' is required.\n'
    target = note('wiki/'+label+'/alpha-compilation.md', text)
    record = replace(record, artifact_id='synthetic:synthesis:'+label,
                     artifact_role='synthesis', path=str(target), sha256=w._hash(target))
    return helpers['synthetic_receipts'](record, refs)

def validate(records, slugs, *, mutation=None, fallback=None):
    bindings = {Path(record.path).relative_to(VAULT).with_suffix('').as_posix():
                dict(asdict(record), path=Path(record.path).relative_to(VAULT).as_posix()) for record in records}
    first = next(iter(bindings))
    if mutation == 'stale': bindings[first]['sha256'] = '0'*64
    if mutation == 'role': bindings[first]['artifact_role'] = 'topic_distill'
    if mutation == 'gate': bindings[first]['accepted_receipts'] = bindings[first]['accepted_receipts'][:-1]
    if mutation == 'key': bindings['wiki/forged/alpha-compilation'] = bindings.pop(first)
    goal = 'Choose the condition policy'
    counts = {slug:slugs.count(slug) for slug in dict.fromkeys(slugs)}
    metadata = {'type':'plan', 'title':'Synthetic plan', 'domain':'science',
        'vault':'SyntheticWiki', 'author':'Synthetic Generator', 'pipeline_id':'synthetic-run',
        'status':'draft', 'last_verified':None, 'freshness':'monthly', 'confidence':'low',
        'human_edited':False, 'tags':['domain/science','topic/alpha','type/plan','vault/SyntheticWiki'],
        'goal':goal, 'decision_context':'A synthetic evidence-only consumer fixture.',
        'created':'2026-10-17', 'updated':'2026-10-17',
        'compiled_from':[{'slug':slug,'source_class':'compilation','citation_count':count} for slug,count in counts.items()]}
    reasoning = '\n'.join(str(i+1)+'. The condition applies. [PARAPHRASE] ^[[['+slug+']]]' for i,slug in enumerate(slugs))
    body = ('## Goal\n'+goal+'\n\n## Context\n'+('Synthetic context '*10)+
        '\n\n## Recommendation\nUse the supplied conditions.\n\n## Reasoning Chain\n'+reasoning+
        '\n\n## Evidence Base\nThe supplied accepted syntheses.\n\n## Counter-Considerations\n'+
        ('A different condition may change the proposed policy. '*4)+
        '\n\n## Review Triggers\n- When measured cost exceeds 20 units.\n')
    text = '---\n'+yaml.safe_dump(metadata,sort_keys=False)+'---\n'+body
    c = context('L2.generate','synthesize',data={'output_type':'plan', 'artifact_role':'strategy',
        'artifact_tier':'C', 'evidence_root':str(VAULT), 'synthesis_bindings':bindings,
        'raw_inputs':[fallback] if fallback else [], 'output_paths':{'note':'wiki/science/plan.md'},
        'accepted_target_path':'wiki/science/plan.md'})
    candidate = c.paths['candidate_dir']/'note.md'
    candidate.parent.mkdir(parents=True,exist_ok=True)
    candidate.write_bytes(text.encode('utf-8'))
    supports = []
    for index, citation in enumerate(extract_citations(text)):
        record = records[min(index,len(records)-1)]
        source = Path(record.path).read_text(encoding='utf-8')
        span = 'Condition '+Path(record.path).parent.name+' is required.'
        start = source.index(span)
        claim_start = text.rfind('\n',0,citation.start)+1
        claim_end = text.find('\n',citation.end)
        support = {'citation_start':citation.start, 'raw_path':record.path, 'raw_sha256':record.sha256,
            'start':start, 'end':start+len(span), 'span':span,
            'claim_start':claim_start, 'claim_end':claim_end,
            'claim_sha256':sha256(text[claim_start:claim_end].encode('utf-8')).hexdigest()}
        if fallback:
            source = Path(fallback['path']).read_text(encoding='utf-8')
            support.update(raw_path=fallback['path'],raw_sha256=fallback['sha256'],start=source.index(span),end=source.index(span)+len(span))
        if mutation == 'span': support['span'] = 'An unsupported condition.'
        supports.append(support)
    (candidate.parent/'citation-bindings.json').write_text(json.dumps({'bindings':supports}),encoding='utf-8')
    # Real domain validation hydrates the serialized records and consumes the file.
    return w.validate_artifact(OutputSpec('note','note.md','text/markdown',True), candidate, c)
'''


if __name__ == '__main__':
    unittest.main()
