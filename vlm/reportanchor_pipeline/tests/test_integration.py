"""Integration regressions. Use --inputs containing ETRI.pdf and vlm cached results."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import run_pipeline as pipeline

INPUTS=Path(os.environ.get('REPORTANCHOR_TEST_INPUTS','/tmp/integration_inputs'))

class CloudParsing(unittest.TestCase):
    def test_invalid_response_preserved_before_json_decode(self):
        class Response(io.BytesIO):
            pass
        with tempfile.TemporaryDirectory() as temp:
            raw=Path(temp)/'response.json'
            with patch.object(pipeline.urllib.request,'urlopen',return_value=Response(b'not-json')):
                with self.assertRaises(json.JSONDecodeError):
                    pipeline.call_cloud(b'png','prompt','fake-key','model',2,raw)
            self.assertEqual(raw.read_bytes(),b'not-json')

    def test_content_schema_and_numeric_string_retained(self):
        good={'kind':'metric','items':[{'value':'5.3%'}],'relationships':[],'issues':[]}
        self.assertEqual(pipeline.validate_response(json.dumps(good))['items'][0]['value'],'5.3%')
        with self.assertRaises(ValueError):pipeline.validate_response('{"kind":"metric","items":[]}')

@unittest.skipUnless((INPUTS/'ETRI.pdf').exists(),'real PDF/cache fixtures not supplied')
class ResumeAndRouting(unittest.TestCase):
    def run_cli(self,out,*extra):
        args=['pipeline','--pdf',str(INPUTS/'ETRI.pdf'),'--cache-root',str(INPUTS/'vlm'),
              '--output',str(out),'--skip-docling',*extra]
        with patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):pipeline.main()
        return pipeline.read(out/'document_result.json')

    def test_cloud_call_cap_resume_and_page_retention(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            answer={'kind':'diagram','items':[],'relationships':[],'issues':['test double']}
            with patch.dict(pipeline.os.environ,{'OLLAMA_API_KEY':'fake'}),patch.object(pipeline,'call_cloud',return_value=answer) as cloud:
                a=self.run_cli(out,'--start-page','38','--end-page','39','--run-cloud','--max-vlm-calls','1')
                self.assertEqual(cloud.call_count,1)
                self.assertEqual(a['progress']['vlm_attempts_this_run'],1)
                self.assertTrue(a['partial'])
                self.assertEqual([p['pdf_page'] for p in a['pages']],[38,39])
                cloud.reset_mock()
                b=self.run_cli(out,'--start-page','39','--end-page','39','--run-cloud','--max-vlm-calls','1')
                self.assertEqual(cloud.call_count,1)
                self.assertEqual([p['pdf_page'] for p in b['pages']],[38,39])
                cloud.reset_mock()
                self.run_cli(out,'--start-page','38','--end-page','39','--run-cloud','--max-vlm-calls','1')
                self.assertEqual(cloud.call_count,0)

    def test_ocr_route_does_not_call_vlm(self):
        import types
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            reader=types.SimpleNamespace(readtext=lambda *a,**k:[([[0,0],[10,0],[10,10],[0,10]],'제목',.9)])
            fake=types.SimpleNamespace(Reader=lambda *a,**k:reader)
            with patch.dict(sys.modules,{'easyocr':fake,'torch':types.SimpleNamespace(set_num_threads=lambda x:None)}),patch.object(pipeline,'call_cloud') as cloud:
                a=self.run_cli(out,'--start-page','14','--end-page','14','--run-ocr','--max-ocr-calls','1')
                self.assertEqual(cloud.call_count,0)
                self.assertEqual(a['progress']['ocr_attempts_this_run'],1)
                r=next(r for r in a['pages'][0]['selection']['regions'] if r['decision']=='ocr_candidate')
                self.assertEqual(r['ocr']['status'],'extracted_unverified')

                for actual, expected in zip(r['ocr']['records'][0]['polygon_pdf_points'][0],r['input_bbox'][:2]):
                    self.assertAlmostEqual(actual,expected,places=4)

    def test_missing_docling_is_failure_native_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)
            self.run_cli(out,'--start-page','38','--end-page','38')
            (out/'cache/docling/p038.json').unlink()
            # No import source on resume, so deleted cache stays unavailable.
            args=['pipeline','--pdf',str(INPUTS/'ETRI.pdf'),'--output',str(out),'--skip-docling','--start-page','38','--end-page','38']
            with patch.object(sys,'argv',args),contextlib.redirect_stdout(io.StringIO()):pipeline.main()
            result=pipeline.read(out/'document_result.json')
            self.assertEqual(result['progress']['failed_pages'],[38])
            self.assertIn('native',result['pages'][0])
            self.assertIn('previous_page_result',result['pages'][0])

if __name__=='__main__':unittest.main()
