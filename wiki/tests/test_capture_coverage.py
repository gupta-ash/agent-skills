"""Source-only behavior regressions; disposable installed layout, no native calls."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


REPO = Path(__file__).resolve().parents[2]


class CaptureCoverageTests(unittest.TestCase):
    def run_case(self, code):
        with tempfile.TemporaryDirectory(prefix="wiki-evidence-test-") as temporary:
            base = Path(temporary)
            export = base / "skills"
            (export / "_workflow").mkdir(parents=True)
            (export / "wiki" / "runtime").mkdir(parents=True)
            shutil.copyfile(REPO / "_shared/workflow/src/contracts.py", export / "_workflow/contracts.py")
            for name in ("capture", "coverage", "provenance"):
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
            if result.returncode == 77:
                self.skipTest(result.stdout.strip())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_current_bytes_and_independent_enumeration_required(self):
        self.run_case(r'''
            inventory, coverage, scope = fixture()
            assert validate_capture(inventory, ROOT).valid
            assert validate_coverage(inventory, coverage, scope).valid
            assert inventory['units'][0]['source_id'] == inventory['units'][1]['source_id']
            # Three chapters of the same book remain one origin.
            assert len({u['source_id'] for u in inventory['units']}) == 1
            worker_only = deepcopy(inventory)
            worker_only.pop('expected_units')
            worker_only['diagnostics'] = {'expected_unit_ids': ['u000001','u000002','u000003']}
            result = validate_capture(worker_only, ROOT)
            assert not result.valid and result.errors[0]['code'] == 'CAPTURE_ENUMERATION_UNAVAILABLE'
            for error in result.errors:
                assert set(error) == {'code','message','details'}
            (ROOT / 'chapter-1.md').write_text('changed', encoding='utf-8')
            assert not validate_capture(inventory, ROOT).valid
            assert not validate_coverage(inventory, coverage, scope).valid
        ''')

    def test_bound_enumeration_reference_and_conflicting_inline(self):
        self.run_case(r'''
            inventory, _, _ = fixture()
            expected = inventory.pop('expected_units')
            reference = write('enumeration.json', json.dumps({'units':expected}))
            inventory['enumeration_ref'] = reference
            assert validate_capture(inventory, ROOT).valid
            inventory['expected_units'] = deepcopy(expected)
            inventory['expected_units'][0]['kind'] = 'invented'
            assert not validate_capture(inventory, ROOT).valid
            inventory.pop('expected_units')
            (ROOT/'enumeration.json').write_text('{}', encoding='utf-8')
            assert not validate_capture(inventory, ROOT).valid
        ''')

    def test_missing_duplicate_phantom_and_source_order(self):
        self.run_case(r'''
            inventory, _, _ = fixture()
            for change in ('missing','duplicate','phantom','order','identity'):
                candidate = deepcopy(inventory)
                if change == 'missing': candidate['units'].pop()
                if change == 'duplicate': candidate['units'].append(deepcopy(candidate['units'][0]))
                if change == 'phantom': candidate['units'][-1]['unit_id'] = 'u999999'
                if change == 'order': candidate['units'].reverse()
                if change == 'identity': candidate['units'][0]['source_id'] = 'f'*64
                assert not validate_capture(candidate, ROOT).valid, change
        ''')

    def test_truncated_output_with_recomputed_worker_hash_is_refused(self):
        self.run_case(r'''
            inventory, _, _ = fixture()
            full = 'Condition Q is required. Example cost is 20. Contrary evidence remains.\n'
            output = write('extracted.md', full)
            expected = inventory['expected_units'][0]
            expected['expected_paths'] = ['extracted.md']
            expected['expected_bytes'] = {'extracted.md':len(full.encode('utf-8'))}
            unit = inventory['units'][0]
            unit['expected_paths'] = ['extracted.md']
            unit['produced_paths'] = ['extracted.md']
            unit['produced_sha256'] = output['sha256']
            assert validate_capture(inventory, ROOT).valid
            shortened = write('extracted.md', 'Condition Q is required.\n')
            unit['produced_sha256'] = shortened['sha256']
            assert not validate_capture(inventory, ROOT).valid
            expected.pop('expected_bytes')
            # A self-consistent worker hash is insufficient for completeness.
            assert not validate_capture(inventory, ROOT).valid
        ''')

    def test_unavailable_truncation_missing_images_and_metadata(self):
        self.run_case(r'''
            inventory, _, _ = fixture()
            for category in ('missing','unavailable','truncation','unprocessed_images'):
                candidate = deepcopy(inventory)
                candidate['units'][0]['coverage'][category] = ['page 2']
                assert not validate_capture(candidate, ROOT).valid, category
            candidate = deepcopy(inventory)
            candidate['units'][0]['coverage']['images'] = [{'path':'chart.png','processed':False}]
            assert not validate_capture(candidate, ROOT).valid
            candidate['units'][0].pop('coverage')
            assert not validate_capture(candidate, ROOT).valid
            candidate = deepcopy(inventory)
            candidate['units'][0]['coverage']['full_body_retained'] = False
            assert not validate_capture(candidate, ROOT).valid
        ''')

    def test_traversal_outside_and_duplicate_paths_refuse(self):
        self.run_case(r'''
            inventory, coverage, scope = fixture()
            for path in ('../chapter-1.md', str(ROOT.parent/'foreign.md')):
                candidate = deepcopy(inventory)
                candidate['units'][0]['produced_paths'] = [path]
                assert not validate_capture(candidate, ROOT).valid
            candidate = deepcopy(inventory)
            candidate['units'][0]['produced_paths'] *= 2
            assert not validate_capture(candidate, ROOT).valid
            coverage['entries'][0]['artifact_refs'].append(dict(coverage['entries'][0]['artifact_refs'][0], path=str(ROOT/'distill-1.md')))
            assert not validate_coverage(inventory, coverage, scope).valid
        ''')

    def test_unit_topic_membership_not_just_file_counts(self):
        self.run_case(r'''
            inventory, coverage, scope = fixture()
            for change in ('missing','duplicate','phantom','wrong_topic','unavailable'):
                candidate = deepcopy(coverage)
                if change == 'missing': candidate['entries'].pop()
                if change == 'duplicate': candidate['entries'].append(deepcopy(candidate['entries'][0]))
                if change == 'phantom': candidate['entries'][0]['unit_id'] = 'u999999'
                if change == 'wrong_topic': candidate['entries'][0]['topic'] = 'beta'
                if change == 'unavailable': candidate['entries'][0]['disposition'] = 'unavailable'
                assert not validate_coverage(inventory, candidate, scope).valid, change
            scope['expected_pairs'] = [{'unit_id':uid,'topic':'alpha'} for uid in scope['units']]
            scope['expected_pairs'].append({'unit_id':'u000001','topic':'beta'})
            assert not validate_coverage(inventory, coverage, scope).valid
            beta = deepcopy(coverage['entries'][0]); beta['topic'] = 'beta'
            coverage['entries'].append(beta)
            assert validate_coverage(inventory, coverage, scope).valid
            scope['expected_pairs'].append(dict(scope['expected_pairs'][0]))
            assert not validate_coverage(inventory, coverage, scope).valid
        ''')

    def test_exclusions_require_exact_explicit_rule_and_empty_refs(self):
        self.run_case(r'''
            inventory, coverage, scope = fixture()
            entry = coverage['entries'][2]
            entry.update(disposition='outside_scope', artifact_refs=[], reason='Requested Alpha excludes appendix.', scope_rule='user:alpha-only')
            assert not validate_coverage(inventory, coverage, scope).valid
            scope['exclusions'] = [{k:entry[k] for k in ('unit_id','topic','reason','scope_rule')}]
            assert validate_coverage(inventory, coverage, scope).valid
            entry['scope_rule'] = 'worker:unrelated'
            assert not validate_coverage(inventory, coverage, scope).valid
        ''')

    def test_partial_capture_is_explicit_not_complete_or_unavailable_covered(self):
        self.run_case(r'''
            inventory, coverage, scope = fixture()
            exclusion = {'unit_id':'u000003','scope_rule':'user:exclude-appendix','reason':'Appendix is unrelated to selected scope.'}
            inventory['capture_scope'] = 'partial'
            inventory['scope'] = {'selected_units':['u000001','u000002'],'exclusions':[exclusion]}
            unit = inventory['units'][2]
            unit.update(status='outside_scope', scope_rule=exclusion['scope_rule'], produced_paths=[], produced_sha256={})
            (ROOT/'chapter-3.md').unlink()
            result = validate_capture(inventory, ROOT)
            assert result.valid
            assert any(d['code']=='PARTIAL_CAPTURE_SCOPE' for d in result.diagnostics)
            assert not validate_coverage(inventory, coverage, scope).valid
            scope['exclusions'] = [dict(exclusion, topic='alpha')]
            coverage['entries'][2].update(disposition='outside_scope', artifact_refs=[], reason=exclusion['reason'], scope_rule=exclusion['scope_rule'])
            assert validate_coverage(inventory, coverage, scope).valid
            inventory['capture_scope'] = 'complete'
            assert not validate_capture(inventory, ROOT).valid
        ''')

    def test_current_coverage_artifact_hashes_and_missing_roots(self):
        self.run_case(r'''
            inventory, coverage, scope = fixture()
            assert validate_coverage(inventory, coverage, scope).valid
            (ROOT/'distill-2.md').write_text('Material qualification dropped.', encoding='utf-8')
            assert not validate_coverage(inventory, coverage, scope).valid
            scope.pop('artifact_root')
            assert not validate_coverage(inventory, coverage, scope).valid
            assert not validate_capture([], ROOT).valid
            assert not validate_coverage(inventory, {'schema_version':True}, {}).valid
        ''')

    def test_symlinked_artifacts_are_not_capture_evidence(self):
        self.run_case(r'''
            inventory, _, _ = fixture()
            try:
                (ROOT/'linked.md').symlink_to(ROOT/'chapter-1.md')
            except (OSError, NotImplementedError) as exc:
                print('Symlink refusal arm unexercised: ' + str(exc))
                raise SystemExit(77)
            inventory['expected_units'][0]['expected_paths'] = ['linked.md']
            inventory['units'][0]['expected_paths'] = ['linked.md']
            inventory['units'][0]['produced_paths'] = ['linked.md']
            assert not validate_capture(inventory, ROOT).valid
        ''')


SETUP = '''
from copy import deepcopy
from hashlib import sha256
import json
from wiki.runtime.capture import enumerate_snapshot_units, validate_capture
from wiki.runtime.coverage import validate_coverage
from wiki.runtime.provenance import source_id

def write(name, text):
    path = ROOT/name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode('utf-8'))
    return {'path':name,'sha256':sha256(path.read_bytes()).hexdigest()}

def fixture():
    sid = source_id({'kind':'isbn-edition','id':'synthetic-book:edition-1'})
    sources = [dict(write(f'chapter-{i}.md', f'---\\ntype: source\\n---\\nChapter {i}: condition Q and cost 20.\\n'), source_id=sid, kind='markdown_document_section') for i in range(1,4)]
    expected = enumerate_snapshot_units(sources, ROOT)
    units = deepcopy(expected)
    for unit in units:
        unit.update(produced_paths=list(unit['expected_paths']), produced_sha256=unit['original_sha256'], coverage={'complete_source_read':True,'full_body_retained':True,'missing':[],'unavailable':[],'truncation':[],'images':[]})
    inventory = {'schema_version':1,'expected_units':expected,'units':units}
    entries = []
    for i, unit in enumerate(units,1):
        ref = write(f'distill-{i}.md', f'---\\ntype: compilation\\nstatus: draft\\nartifact_role: topic_distill\\n---\\nCondition Q and cost 20. ^[[[chapter-{i}]]]\\n')
        entries.append({'unit_id':unit['unit_id'],'topic':'alpha','artifact_refs':[ref],'disposition':'covered','reason':'Mapped draft candidate, not semantic acceptance.','scope_rule':'all supplied units'})
    return inventory, {'schema_version':1,'entries':entries}, {'units':[u['unit_id'] for u in units],'topic':'alpha','artifact_root':str(ROOT)}
'''


if __name__ == '__main__':
    unittest.main()
