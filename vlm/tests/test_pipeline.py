"""Integration check with a new synthetic PDF and explicit mocked Docling/Ollama.
No real Docling inference or real VLM inference is represented by this test.
"""
import hashlib,json,subprocess,sys,tempfile,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pymupdf
ROOT=Path(__file__).resolve().parents[1]
class Handler(BaseHTTPRequestHandler):
    calls=0
    def log_message(self,*args):pass
    def do_GET(self):
        self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'models':[{'name':'test-model'}]}).encode())
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert body['images'] and body['model']=='test-model'
        Handler.calls+=1
        result={'response':json.dumps({'title':'mock','texts':['mock text'],'relations':[],'issues':[]})}
        self.send_response(200);self.end_headers();self.wfile.write(json.dumps(result).encode())
class Integration(unittest.TestCase):
    def test_new_pdf_through_all_connected_stages(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp=Path(tmp);pdf=tmp/'arbitrary_user_upload.pdf';cache=tmp/'docling';cache.mkdir()
            with pymupdf.open() as doc:
                p=doc.new_page();p.insert_text((50,50),'An arbitrary uploaded document, not one of the three experiments. '*2);doc.save(pdf)
            sha=hashlib.sha256(pdf.read_bytes()).hexdigest()
            (cache/'settings.json').write_text(json.dumps({'sha256':sha}))
            (cache/'p001.json').write_text(json.dumps({'pages':{'1':{}},'texts':[],'tables':[],
                'pictures':[{'self_ref':'#/pictures/0','prov':[{'page_no':1,'bbox':{'l':50,'t':100,'r':250,'b':250,'coord_origin':'TOPLEFT'}}]}]}))
            server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                cmd=[sys.executable,str(ROOT/'run_pipeline.py'),'--pdf',str(pdf),'--docling-cache',str(cache),
                    '--include-review','--run','--model','test-model','--ollama-url',f'http://127.0.0.1:{server.server_port}',
                    '--output',str(tmp/'output')]
                result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                self.assertEqual(result.returncode,0,result.stdout)
                path=next((tmp/'output').glob('*/document_result.json'));d=json.loads(path.read_text())
                self.assertEqual(d['document']['file_name'],pdf.name)
                self.assertEqual(d['status'],'completed')
                self.assertEqual(d['pages'][0]['vlm_results'][0]['status'],'schema_valid_content_unverified')
                self.assertEqual(Handler.calls,1)
                # Resume reuses signature-matched saved inference; it must not call mock again.
                result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                self.assertEqual(result.returncode,0,result.stdout)
                self.assertEqual(Handler.calls,1)
                (cache/'settings.json').write_text(json.dumps({'sha256':'wrong'}))
                result=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                self.assertNotEqual(result.returncode,0);self.assertIn('hash does not match',result.stdout)
                self.assertEqual(Handler.calls,1)
            finally:
                server.shutdown();server.server_close();thread.join()
if __name__=='__main__':unittest.main()
