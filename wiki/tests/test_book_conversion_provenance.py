"""Isolated source consumers: no original/native runs, approvals or publications."""
import textwrap
import unittest
import test_workflows as support


LEGACY_SETUP = r'''
from copy import deepcopy

def legacy_book(suffix):
    root=ROOT/('legacy-'+suffix); root.mkdir()
    version=6 if suffix=='mobi' else 8; length=232 if version==6 else 264
    record=bytearray(16+length)
    record[16:20]=b'MOBI'; record[20:24]=length.to_bytes(4,'big');record[36:40]=version.to_bytes(4,'big')
    record[108:112]=(2 if version==6 else 0xffffffff).to_bytes(4,'big')
    record[128:132]=(0x50).to_bytes(4,'big')
    record.extend(b'EXTH'+(12).to_bytes(4,'big')+bytes(4))
    header=bytearray(94);header[60:68]=b'BOOKMOBI';header[76:78]=(2).to_bytes(2,'big')
    header[78:82]=len(header).to_bytes(4,'big');header[86:90]=(len(header)+len(record)).to_bytes(4,'big')
    book=root/('original.'+suffix);book.write_bytes(header+record+b'Original record')
    identity={'kind':'canonical-reference','id':str(ROOT/('source.'+suffix))};sid=provenance.source_id(identity)
    cover=str(ROOT/'ungranted/ash_wiki_book_images_legacy123/cover.jpg')
    metadata={'title':'Atlas','author':'Invented Author','source_reference':identity['id'],
              'snapshot_media_type':w._ref(book)['media_type'],'local_archive_ref':'local:archive/book/'+sid+'/'+w._hash(book)+'.'+suffix,
              'original_format':suffix,'format':'epub','cover_image_path':cover,
              'front_matter':{'content_markdown':'Cover','has_content':True,'sections':['titlepage.xhtml']},
              'metadata':{'conversion_from':suffix,'extraction_method':'epub_zip','file_size_bytes':book.stat().st_size}}
    raws=[];expected=[]
    for ordinal,body in enumerate((b'Cover',b'Chapter IV\nAlpha only after Q.\n'),1):
        uid=f'u{ordinal:06d}';path=root/(uid+'.md');path.write_bytes(body)
        locator={'path':book.name,**({'sections':['titlepage.xhtml']} if ordinal==1 else {'chapter':1,'title':'Chapter IV'})}
        raw={**w._ref(path),'unit_id':uid,'source_id':sid,'source_class':'book','identity':identity,'metadata':deepcopy(metadata),'original_locator':locator}
        unit={'unit_id':uid,'source_order':ordinal,'source_id':sid,'kind':'front-matter' if ordinal==1 else 'chapter',
              'original_locator':locator,'original_sha256':w._hash(book),'original_bytes':book.stat().st_size,
              'expected_paths':[path.name],'expected_sha256':{path.name:w._hash(path)},'expected_bytes':{path.name:len(body)}}
        raws.append(raw);expected.append(unit)
    inventory={'schema_version':1,'capture_scope':'complete','scope':{'selected_units':[r['unit_id'] for r in raws],'exclusions':[]},
               'expected_units':expected,'units':[{**unit,'produced_paths':unit['expected_paths'],
               'produced_sha256':unit['expected_sha256'],'coverage':{'complete':True,'images':[]}} for unit in expected],'diagnostics':{}}
    scope=root/'capture-scope.json';scope.write_text(w._json(inventory),encoding='utf-8')
    rows={'capture.scope':[{'item_id':'main','status':'done','data':{},'artifacts':[w._ref(scope,'capture-scope.json')]}]}
    c=context('L1.extract','extract',item_id='main',data={'capture_root':str(root),'captured_raws':raws,'inventory':inventory,
              'source_type':'book','image_inputs':[],'capture_diagnostics':[],'step_data':rows,'options':{'title':None},
              'planned_date':'2026-10-19','topic':'alpha','domain':'science','topic_slug':'alpha'})
    c=replace(c,request=request('wiki-ingest',identity['id'],'--vault',str(VAULT)))
    c.candidate_dir.mkdir(parents=True, exist_ok=True)
    return c,book,scope,cover
'''


class BookConversionProvenanceTests(unittest.TestCase):
    def run_case(self, code):
        support.WorkflowTests.run_case(self, LEGACY_SETUP + textwrap.dedent(code))

    def test_selected_inventory_and_coverless_provenance_bind_current_extraction(self):
        self.run_case(r'''
            for suffix in ('mobi','azw3'):
                c,book,scope,cover=legacy_book(suffix)
                before=w._plain(c.data)
                snapshots={path:path.read_bytes() for path in Path(c.data['capture_root']).iterdir()}
                prepared=w._extract_handler(c);task=prepared.tasks[0]
                task_data=w._plain(prepared.local_result['data']['task_context'])
                proof=task_data['book_conversion_provenance']
                assert proof['original']==w._ref(book) and proof['source_image_records']==[]
                assert proof['generated_titlepage_units']==['u000001'] and proof['selected_units']==['u000001','u000002']
                plans,units=w._capture_plan(c)
                outputs=(w._output('raw-note-map.json'),w._output('capture-fidelity.json'),
                         *(w.OutputSpec(name,plan['relative_path'],'text/markdown',True) for name,plan in plans.items()))
                assert task.outputs==outputs and task.allowed_actions==('read','write')
                expected=tuple(w._input(ref) for ref in [w._ref(scope),*c.data['captured_raws'],w._ref(book)])
                assert task.inputs==expected
                assert task_data['expected_raw_map']==w._plain(units)
                for output in task_data['output_context'].values():
                    assert output['source_snapshot']==w._ref(book)
                    assert output['expected_metadata']['source_hash']==w._hash(book)
                    assert output['source_author']=='Invented Author'
                assert all(ref.path!=cover for ref in task.inputs+task.resources) and cover not in task.read_roots
                assert not Path(cover).exists()
                scope.write_bytes(b'changed inventory')
                try: w._extract_handler(c)
                except w.DomainError: pass
                else: raise AssertionError('Stale selected inventory consumed')
                scope.write_bytes(snapshots[scope])
                assert w._plain(c.data)==before
                assert {path:path.read_bytes() for path in snapshots}==snapshots
            assert not (ROOT/'project/state.db').exists()
        ''')

    def test_foreign_metadata_real_images_and_changed_original_refuse_extraction(self):
        self.run_case(r'''
            c,book,scope,cover=legacy_book('mobi')
            original=book.read_bytes();before=w._plain(c.data)
            for variant in ('metadata-path','source','foreign-unit','image-marker','image-record',
                            'exth-cover','encrypted','changed-book','partial-scope'):
                data=w._plain(c.data)
                if variant=='metadata-path':
                    for raw in data['captured_raws']: raw['metadata']['cover_image_path']=str(ROOT/'unrelated.jpg')
                elif variant=='source': data['captured_raws'][1]['identity']['id']='synthetic:foreign'
                elif variant=='foreign-unit': data['captured_raws'][1]['unit_id']='u000999'
                elif variant=='image-marker':
                    Path(data['captured_raws'][1]['path']).write_bytes(b'[IMAGE: absent.jpg]')
                elif variant=='image-record':
                    body=bytearray(original);first=int.from_bytes(body[78:82],'big');body[first+108:first+112]=(1).to_bytes(4,'big');book.write_bytes(body)
                elif variant=='exth-cover':
                    body=bytearray(original);first=int.from_bytes(body[78:82],'big');exth=first+16+232
                    body[exth+8:exth+12]=(1).to_bytes(4,'big');book.write_bytes(body)
                elif variant=='encrypted':
                    body=bytearray(original);first=int.from_bytes(body[78:82],'big');body[first+12:first+14]=(1).to_bytes(2,'big');book.write_bytes(body)
                elif variant=='changed-book': book.write_bytes(b'foreign original')
                else: data['inventory']['scope']['selected_units']=['u000002']
                try: w._extract_handler(replace(c,data=data))
                except (w.DomainError,ValueError): pass
                else: raise AssertionError('Unproved book provenance admitted: '+variant)
                book.write_bytes(original)
                Path(c.data['captured_raws'][1]['path']).write_bytes(b'Chapter IV\nAlpha only after Q.\n')
            assert w._plain(c.data)==before
            assert not Path(cover).exists() and not (ROOT/'project/state.db').exists()
        ''')

    def test_real_calibre_coverless_mobi_does_not_invent_source_cover(self):
        self._real_conversion('mobi', False)

    def test_real_calibre_embedded_azw3_cover_remains_authorized_image(self):
        self._real_conversion('azw3', True)

    def _real_conversion(self, suffix, with_cover):
        self.run_case(r'''
            import subprocess,zipfile
            import fitz
            helper=w._module(SKILLS/'wiki/scripts/extract_book.py','book_conversion_test_helper')
            converter=helper.find_calibre_convert()
            if not converter: print('Calibre ebook-convert unavailable');raise SystemExit(77)
            suffix=SUFFIX;with_cover=WITH_COVER
            source=ROOT/'source.epub'
            pix=fitz.Pixmap(fitz.csRGB,fitz.IRect(0,0,80,80),False);pix.clear_with(200)
            jpeg=pix.tobytes('jpeg')
            with zipfile.ZipFile(source,'w') as archive:
                archive.writestr('mimetype','application/epub+zip')
                archive.writestr('META-INF/container.xml','<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
                archive.writestr('content.opf','<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="uid"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="uid">urn:uuid:6b15a747-694b-48e7-b4c2-6cd5601e27b4</dc:identifier><dc:title>Atlas</dc:title><dc:creator>Invented Author</dc:creator><dc:language>en</dc:language>'+('<meta name="cover" content="cover"/>' if with_cover else '')+'</metadata><manifest><item id="body" href="chapter.xhtml" media-type="application/xhtml+xml"/>'+('<item id="cover" href="source-cover.jpg" media-type="image/jpeg"/>' if with_cover else '')+'</manifest><spine><itemref idref="body"/></spine></package>')
                archive.writestr('chapter.xhtml','<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter IV</title></head><body><h1>Chapter IV</h1><p>Alpha only after condition Q; cost exactly20.</p></body></html>')
                if with_cover: archive.writestr('source-cover.jpg',jpeg)
            binary=ROOT/('source.'+suffix)
            converted=subprocess.run([converter,str(source),str(binary)],capture_output=True,text=True,timeout=25)
            assert converted.returncode==0,converted.stderr
            c=context('L1.acquire','acquire',data={'options':{'title':None},'planned_date':'2026-10-19','topic':'alpha','domain':'science','topic_slug':'alpha'})
            inventory,raws,images,diagnostics=w._capture_sources(c,[{'source':str(binary),'source_class':'book'}])
            assert bool(images)==with_cover,(suffix,images)
            if not with_cover:
                assert all(not raw['metadata'].get('cover_image_path') for raw in raws)
                assert all(raw['metadata'].get('front_matter',{}).get('sections')!=['titlepage.xhtml'] for raw in raws)
            c=replace(c,data={**c.data,'capture_root':str(c.candidate_dir),'inventory':inventory,'captured_raws':raws,'image_inputs':images,'source_type':'book'})
            selected=w._capture_scope_handler(c)
            data={**c.data,**w._plain(selected.local_result['data']),'step_data':{'capture.scope':[{'item_id':'main','status':'done','artifacts':w._plain(selected.local_result['artifacts'])}]}}
            current=context('L1.extract','extract',data=data)
            prepared=w._extract_handler(current);task=prepared.tasks[0]
            assert 'book_conversion_provenance' not in prepared.local_result['data']['task_context']
            for image in images:
                assert w._input(image) in task.inputs and image['path'] in task.read_roots
                assert Path(image['path']).read_bytes().startswith(b'\xff\xd8\xff')
            if with_cover:
                ungranted=replace(current,data={**current.data,'image_inputs':[]})
                try: w._extract_handler(ungranted)
                except w.DomainError: pass
                else: raise AssertionError('Real original source cover was waived without an image grant')
            assert not (ROOT/'project/state.db').exists()
        '''.replace('SUFFIX', repr(suffix)).replace('WITH_COVER', repr(with_cover)))


if __name__ == '__main__':
    unittest.main()
