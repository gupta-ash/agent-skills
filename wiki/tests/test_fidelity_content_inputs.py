"""Deterministic source consumers only; no native or original-run effects."""
import textwrap
import unittest
import test_workflows as support


SETUP = r'''
from copy import deepcopy


def fixture(legacy=True):
    c=context('capture.fidelity','capture_fidelity',item_id='main',data={
        'source_type':'book','options':{'title':None},'topic':'alpha','domain':'science','topic_slug':'alpha'})
    c.candidate_dir.mkdir(parents=True)
    root=c.paths['run_root']/'attempts/acquisition/candidate';root.mkdir(parents=True)
    original=root/'original-0001.mobi';original.write_bytes(b'Synthetic original book bytes, not an extraction/native qualification')
    identity={'kind':'canonical-reference','id':'synthetic:atlas'};sid=provenance.source_id(identity)
    raws=[];expected=[]
    for ordinal,body in enumerate((b'Front matter',b'Preface\r\nOnly after Q.\r\n',b'Chapter IV\nCost exactly20.\n'),1):
        uid=f'u{ordinal:06d}';path=root/(uid+'.md');path.write_bytes(body)
        locator={'path':original.name}
        if ordinal>1:
            content=root/(f'chapter-0001-{ordinal-1:06d}.md' if legacy else path.name)
            content.write_bytes(body)
            locator.update(chapter=ordinal-1,title=('Preface' if ordinal==2 else 'Chapter IV'),spine_index=ordinal-1,content_path=str(content))
        else:
            locator['sections']=['front.xhtml']
        raws.append({**w._ref(path),'unit_id':uid,'source_id':sid,'source_class':'book','identity':identity,
                     'original_locator':{key:value for key,value in locator.items() if key!='path'},
                     'metadata':{'title':'Atlas','author':'Invented Author','source_reference':'synthetic:atlas'}})
        expected.append({'unit_id':uid,'source_order':ordinal,'kind':'front-matter' if ordinal==1 else 'chapter',
                         'source_id':sid,'original_locator':locator,'original_sha256':w._hash(original),'original_bytes':original.stat().st_size,
                         'expected_paths':[path.name],'expected_sha256':{path.name:w._hash(path)},'expected_bytes':{path.name:len(body)}})
    inventory={'schema_version':1,'capture_scope':'complete','scope':{'selected_units':[raw['unit_id'] for raw in raws],'exclusions':[]},
               'expected_units':expected,'units':[{**deepcopy(unit),'produced_paths':unit['expected_paths'],
               'produced_sha256':unit['expected_sha256'],'coverage':{'complete':True,'images':[]}} for unit in expected],'diagnostics':{}}
    acquired=root/'capture-inventory.json';acquired.write_text(w._json(inventory),encoding='utf-8')
    scope=c.paths['run_root']/'scope/capture-scope.json';scope.parent.mkdir();scope.write_text(w._json(inventory),encoding='utf-8')
    extraction=c.paths['run_root']/'extraction';extraction.mkdir()
    mapping=extraction/'raw-note-map.json';fidelity=extraction/'capture-fidelity.json'
    fidelity.write_text('{"schema_version":1}',encoding='utf-8')
    rows={'L1.acquire':[{'item_id':'main','status':'done','data':{},'artifacts':[w._ref(acquired,'capture-inventory.json')]}],
          'capture.scope':[{'item_id':'main','status':'done','data':{},'artifacts':[w._ref(scope,'capture-scope.json')]}]}
    c=replace(c,request=request('wiki-ingest','synthetic:atlas','--vault',str(VAULT)),data={**c.data,
              'capture_root':str(root),'captured_raws':raws,'image_inputs':[],'inventory':inventory,'step_data':rows})
    _,units=w._capture_plan(c);mapping.write_text(w._json({'schema_version':1,'units':units}),encoding='utf-8')
    artifacts=[w._ref(mapping,'raw-note-map.json'),w._ref(fidelity,'capture-fidelity.json')]
    for name,plan in w._capture_plan(c)[0].items():
        path=extraction/plan['relative_path'];path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'Captured candidate body')
        artifacts.append(w._ref(path,name))
    rows['L1.extract']=[{'item_id':'main','status':'done','data':{},'artifacts':artifacts}]
    c=replace(c,data={**c.data,'step_data':rows})
    return c,root,scope
'''


class FidelityContentInputTests(unittest.TestCase):
    def run_case(self, code):
        support.WorkflowTests.run_case(self, SETUP + textwrap.dedent(code))

    def test_selected_legacy_chapter_aliases_keep_exact_existing_input_authority(self):
        self.run_case(r'''
            c,root,scope=fixture()
            before=w._plain(c.data)
            frozen={path:path.read_bytes() for path in c.paths['run_root'].rglob('*') if path.is_file()}
            prepared=w._capture_fidelity_handler(c);fresh=prepared.tasks[0]
            proof=w._plain(prepared.local_result['data']['task_context']['capture_content_aliases'])
            refs=[*c.data['captured_raws'],w._ref(root/'original-0001.mobi'),
                  *c.data['step_data']['L1.extract'][0]['artifacts'],w._ref(scope)]
            expected=tuple(dict.fromkeys(w._input(ref) for ref in refs))
            assert fresh.inputs==expected
            assert fresh.outputs==(w._output('capture-verdict.json'),) and fresh.allowed_actions==('read','write')
            assert len(proof)==2 and [entry['unit_id'] for entry in proof]==['u000002','u000003']
            for alias in proof:
                content=Path(alias['original_locator']['content_path'])
                granted=Path(alias['input_ref']['path'])
                assert content!=granted and content.read_bytes()==granted.read_bytes()
                assert alias['content_sha256']==w._hash(granted) and alias['content_bytes']==granted.stat().st_size
                assert content not in [Path(path) for path in fresh.read_roots]
                assert content not in [Path(ref.path) for ref in fresh.inputs+fresh.resources]
                assert w._input(alias['input_ref']) in fresh.inputs and w._input(alias['original']) in fresh.inputs
            assert str(root) not in fresh.read_roots and str(scope.parent) not in fresh.read_roots
            assert w._plain(c.data)==before
            assert {path:path.read_bytes() for path in frozen}==frozen
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_fresh_bound_content_and_backfill_keep_exact_file_authority(self):
        self.run_case(r'''
            c,root,scope=fixture(False)
            prepared=w._capture_fidelity_handler(c);fresh=prepared.tasks[0]
            aliases=prepared.local_result['data']['task_context']['capture_content_aliases']
            assert len(aliases)==2
            for alias in aliases:
                assert Path(alias['original_locator']['content_path'])==Path(alias['input_ref']['path'])
                assert w._input(alias['input_ref']) in fresh.inputs
            raw=c.data['captured_raws'][0]
            legacy=context('legacy.fidelity','capture_fidelity',item_id='legacy-one',data={'item':{'raw':raw}})
            legacy.candidate_dir.mkdir(parents=True)
            prepared=w._capture_fidelity_handler(legacy)
            assert prepared.tasks[0].inputs==(w._input(raw),)
        ''')

    def test_stale_foreign_ambiguous_hash_and_path_variants_refuse(self):
        self.run_case(r'''
            c,root,scope=fixture()
            retained={path:path.read_bytes() for path in c.paths['run_root'].rglob('*') if path.is_file()}
            for variant in ('stale-scope','changed-chapter','normalized-only','changed-unit','changed-original','foreign-source',
                            'foreign-snapshot','duplicate-raw','unselected','foreign-acquisition','changed-map','external-equal',
                            'equal-unrelated','traversal','directory','other-unit'):
                data=w._plain(c.data);rows=data['step_data'];inventory=data['inventory']
                unit=inventory['expected_units'][1];raw=data['captured_raws'][1]
                content=Path(unit['original_locator']['content_path'])
                if variant=='stale-scope': scope.write_bytes(b'{}')
                elif variant=='changed-chapter': content.write_bytes(b'Other body')
                elif variant=='normalized-only': content.write_bytes(content.read_bytes().replace(b'\r\n',b'\n'))
                elif variant=='changed-unit': Path(raw['path']).write_bytes(b'Other unit')
                elif variant=='changed-original': (root/'original-0001.mobi').write_bytes(b'Other snapshot')
                elif variant=='foreign-source': raw['identity']={'kind':'canonical-reference','id':'synthetic:foreign'}
                elif variant=='foreign-snapshot': unit['original_sha256']='f'*64
                elif variant=='duplicate-raw': data['captured_raws'].append(deepcopy(raw))
                elif variant=='unselected': inventory['scope']['selected_units']=['u000001','u000003']
                elif variant=='foreign-acquisition': rows['L1.acquire'][0]['artifacts'][0]['path']=str(scope)
                elif variant=='changed-map': Path(rows['L1.extract'][0]['artifacts'][0]['path']).write_bytes(b'{"units":[]}')
                else:
                    if variant=='external-equal':
                        foreign=ROOT/'equal.md';foreign.write_bytes(content.read_bytes());path=str(foreign)
                    elif variant=='equal-unrelated':
                        foreign=root/'unrelated.md';foreign.write_bytes(content.read_bytes());path=str(foreign)
                    elif variant=='traversal': path=str(root/'..'/'candidate'/content.name)
                    elif variant=='directory': path=str(root)
                    else: path=str(root/'chapter-0001-000002.md')
                    unit['original_locator']['content_path']=path
                    inventory['units'][1]['original_locator']['content_path']=path
                    raw['original_locator']['content_path']=path
                    # Even internally consistent retained metadata is not arbitrary
                    # equal-object authority. Rebind all synthetic owner documents.
                    acquisition=Path(rows['L1.acquire'][0]['artifacts'][0]['path'])
                    acquisition.write_text(w._json(inventory),encoding='utf-8')
                    rows['L1.acquire'][0]['artifacts']=[w._ref(acquisition,'capture-inventory.json')]
                    scope.write_text(w._json(inventory),encoding='utf-8')
                    rows['capture.scope'][0]['artifacts']=[w._ref(scope,'capture-scope.json')]
                    changed=replace(c,data=data)
                    mapping=Path(rows['L1.extract'][0]['artifacts'][0]['path'])
                    mapping.write_text(w._json({'schema_version':1,'units':w._capture_plan(changed)[1]}),encoding='utf-8')
                    rows['L1.extract'][0]['artifacts'][0]=w._ref(mapping,'raw-note-map.json')
                changed=replace(c,data=data)
                try: w._capture_fidelity_handler(changed)
                except (w.DomainError,ValueError): pass
                else: raise AssertionError('Unproved chapter comparison admitted: '+variant)
                for path,body in retained.items(): path.write_bytes(body)
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_selected_scope_excludes_unselected_chapter_context(self):
        self.run_case(r'''
            c,root,scope=fixture()
            data=w._plain(c.data);inventory=data['inventory'];rows=data['step_data']
            inventory['capture_scope']='partial'
            inventory['scope']={'selected_units':['u000001','u000002'],'exclusions':[
                {'unit_id':'u000003','scope_rule':'explicit-selected-scope','reason':'Requested selected prefix only'}]}
            inventory['units'][2].update(status='outside_scope',scope_rule='explicit-selected-scope')
            data['captured_raws']=data['captured_raws'][:2]
            scope.write_text(w._json(inventory),encoding='utf-8')
            rows['capture.scope'][0]['artifacts']=[w._ref(scope,'capture-scope.json')]
            changed=replace(c,data=data)
            mapping=Path(rows['L1.extract'][0]['artifacts'][0]['path'])
            mapping.write_text(w._json({'schema_version':1,'units':w._capture_plan(changed)[1]}),encoding='utf-8')
            rows['L1.extract'][0]['artifacts'][0]=w._ref(mapping,'raw-note-map.json')
            changed=replace(c,data=data)
            prepared=w._capture_fidelity_handler(changed)
            assert [alias['unit_id'] for alias in prepared.local_result['data']['task_context']['capture_content_aliases']]==['u000002']
            data['captured_raws'].append(w._plain(c.data['captured_raws'][2]))
            try: w._capture_fidelity_handler(replace(c,data=data))
            except w.DomainError: pass
            else: raise AssertionError('Unselected chapter admitted as primary body context')
        ''')

    def test_unsettled_or_missing_capture_prefix_refuses_fidelity_preparation(self):
        self.run_case(r'''
            c,root,scope=fixture()
            before=w._plain(c.data)
            for step in ('L1.acquire','capture.scope','L1.extract'):
                for status in ('blocked','skipped','missing'):
                    data=w._plain(c.data)
                    if status=='missing':
                        del data['step_data'][step]
                    else:
                        data['step_data'][step][0]['status']=status
                    try: w._capture_fidelity_handler(replace(c,data=data))
                    except w.DomainError as exc: assert exc.code=='CAPTURE_CONTENT_AUTHORITY_UNAVAILABLE'
                    else: raise AssertionError('Unsettled capture prefix admitted: '+step+' '+status)
            assert w._plain(c.data)==before
            assert not (c.candidate_dir/'capture-verdict.json').exists()
            assert not (ROOT/'project/state.db').exists()
        ''')


if __name__ == '__main__':
    unittest.main()
