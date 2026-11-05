"""Mechanical annotation regressions; semantic fidelity still requires native verification."""
import unittest

import test_tasks


class CompleteNoteAnnotationTests(unittest.TestCase):
    run_case = test_tasks.TaskPreparationTests.run_case

    def test_repeated_lineage_outside_claim_section_needs_its_own_annotation(self):
        self.run_case(r'''
            raw = Path(RAW.path)
            raw.write_text(raw.read_text(encoding='utf-8')+'Alpha starts at 10 only after Q. Worked cost is 20. Constraint R prevents starting. Observation is 3, not 30.\n',encoding='utf-8')
            RAW = ref(raw)
            CONTEXT.data['raw_inputs'] = (RAW,)
            text = compilation({'compiled_from':[{'slug':'origin','source_class':'documents','citation_count':4}]}).rstrip()+'\n\n## Examples\n\nThe worked cost is 20. [PARAPHRASE] ^[[[origin]]]\n\n## Constraints\n\nConstraint R prevents starting. [PARAPHRASE] ^[[[origin]]]\n\n## Observations\n\nThe observation is 3, not 30. [PARAPHRASE] ^[[[origin]]]\n'
            suffixes = (
                ('\n## Verification Notes\n\nLineage independence is unknown. ^[[[origin]]]\n',False,5),
                ('\n## Verification Notes\n\nLineage independence is unknown. [UNKNOWN]\n',False,4),
                ('\n## Verification Notes\n\nSee Overview, Examples, Constraints and Observations; bindings are in citation-bindings.json.\n',True,4),
            )
            for suffix,valid,count in suffixes:
                body = text.replace('citation_count: 4','citation_count: '+str(count))+suffix
                path = output_file(SPEC,body)
                supports(path)
                result = validate_artifact(SPEC,path,CONTEXT)
                assert result.valid is valid, result.errors
                if valid:
                    assert any(d['code']=='MECHANICAL_VALIDATION_ONLY' for d in result.diagnostics)
        ''')

    def test_compound_coverage_reason_is_not_exempt_from_annotation(self):
        self.run_case(r'''
            for reason,valid,count in (
                ('The unit has unknown lineage and its determination method is absent. ^[[[origin]]]',False,2),
                ('The lineage is unknown. [UNKNOWN] [PARAPHRASE] ^[[[origin]]]',False,2),
                ('See Overview and the declared coverage.json entry; scope_rule is retained there.',True,1),
            ):
                text = compilation({'compiled_from':[{'slug':'origin','source_class':'documents','citation_count':count}]})+'\n## Coverage\n\n'+reason+'\n'
                path = output_file(SPEC,text)
                supports(path)
                result = validate_artifact(SPEC,path,CONTEXT)
                assert result.valid is valid, result.errors
        ''')

    def test_marked_cited_unsupported_lineage_is_not_mechanically_proven_true(self):
        self.run_case(r'''
            text = compilation({'compiled_from':[{'slug':'origin','source_class':'documents','citation_count':2}]})+'\n## Scope\n\nThe supplied work has independent lineage. [PARAPHRASE] ^[[[origin]]]\n'
            path = output_file(SPEC,text)
            supports(path)
            result = validate_artifact(SPEC,path,CONTEXT)
            assert result.valid, result.errors
            assert any(d['code']=='MECHANICAL_VALIDATION_ONLY' for d in result.diagnostics)
            # The exact raw span binding is mechanically genuine but does not establish
            # this extra claim. Native complete-scope verification must assess support.
        ''')


if __name__ == '__main__':
    unittest.main()
