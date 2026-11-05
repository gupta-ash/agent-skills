"""Real local extractor CLI contracts; synthetic documents, no native model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


EXTRACTOR = Path(__file__).resolve().parents[1] / 'scripts/extract_document.py'


class DocumentExtractionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='wiki-document-contract-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.environment = {key: value for key, value in os.environ.items()
                            if key.upper() in {'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'PATH', 'PATHEXT'}}
        for key in ('HOME', 'USERPROFILE', 'APPDATA', 'LOCALAPPDATA', 'TEMP', 'TMP', 'TMPDIR'):
            directory = self.root / key.lower()
            directory.mkdir()
            self.environment[key] = str(directory)

    def extract(self, path):
        result = subprocess.run([sys.executable, '-I', '-B', str(EXTRACTOR), '--extract-images', str(path)],
                                cwd=self.root, env=self.environment, capture_output=True, text=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def html(self, marker):
        path = self.root / 'source.html'
        path.write_text('<html ' + ('xmlns:w="urn:schemas-microsoft-com:office:word"' if marker == 'namespace' else '') +
                        '><head><title>Generic title</title>' + ('' if marker == 'namespace' else marker) +
                        '<o:DocumentProperties><o:Title>Word document title</o:Title>'
                        '<o:Author>Invented author</o:Author></o:DocumentProperties></head>'
                        '<body><p>Alpha requires condition Q; cost 20. Constraint R prevents Alpha.</p></body></html>',
                        encoding='utf-8')
        return path

    def test_word_metadata_survives_standard_html_attribute_spellings(self):
        for marker in ('<meta name="Generator" content="Microsoft Word 16">',
                       "<META CONTENT='Microsoft Word 16' NAME='generator'>",
                       '<meta name=Generator content="Microsoft Word 16">',
                       '<meta name="ProgId" content="Word.Document">', 'namespace'):
            with self.subTest(marker=marker):
                output = self.extract(self.html(marker))
                self.assertEqual(output['title'], 'Word document title')
                self.assertEqual(output['author'], 'Invented author')
                self.assertIn('Constraint R prevents Alpha.', output['content_text'])

    def test_non_word_generator_keeps_ordinary_html_metadata(self):
        output = self.extract(self.html('<meta name="Generator" content="Example static site">'))
        self.assertEqual(output['title'], 'Generic title')
        self.assertEqual(output['author'], '')
        self.assertIn('cost 20', output['content_text'])

    def test_pdf_cli_emits_one_json_document_with_both_pages(self):
        try:
            import fitz
        except ImportError:
            self.skipTest('PyMuPDF is required for the real PDF extraction contract')
        path = self.root / 'source.pdf'
        with fitz.open() as document:
            document.new_page().insert_text((72, 72), 'Alpha starts at 10 only after condition Q.')
            document.new_page().insert_text((72, 72), 'Worked cost 20; constraint R prevents Alpha; 3 not 30.')
            document.save(path)
        output = self.extract(path)
        self.assertEqual(output['page_count'], 2)
        self.assertIn('10 only after condition Q', output['content_text'])
        self.assertIn('constraint R prevents Alpha; 3 not 30', output['content_text'])


if __name__ == '__main__':
    unittest.main()
