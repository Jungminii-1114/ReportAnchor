"""Standard Docling + OCR + picture classification; no picture-description VLM."""
import argparse,json,time,importlib.metadata,hashlib
from pathlib import Path

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--config',required=True);ap.add_argument('--all-pages',action='store_true');ap.add_argument('--resume',action='store_true');ap.add_argument('--document',action='append');ap.add_argument('--threads',type=int,default=2);a=ap.parse_args();cfgpath=Path(a.config).resolve();root=cfgpath.parent;cfg=json.loads(cfgpath.read_text(encoding='utf-8'))
 import torch
 torch.set_num_threads(a.threads)
 from docling.document_converter import DocumentConverter,PdfFormatOption
 from docling.datamodel.pipeline_options import PdfPipelineOptions,EasyOcrOptions
 from docling.datamodel.base_models import InputFormat
 opt=PdfPipelineOptions();opt.do_ocr=True;opt.do_table_structure=True;opt.do_picture_classification=True;opt.do_picture_description=False;opt.ocr_options=EasyOcrOptions(lang=['ko','en']);opt.generate_page_images=False;opt.generate_picture_images=True
 converter=DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=opt)})
 settings={'pipeline':'standard','ocr':'easyocr','languages':['ko','en'],'picture_classification':True,'picture_description':False,'docling_version':importlib.metadata.version('docling'),'page_scope':'all' if a.all_pages else 'fixed_review_pages'}
 for spec in cfg['documents']:
  if a.document and spec['id'] not in a.document:continue
  pdf=(root/spec['pdf']).resolve();folder=root/cfg['output']/spec['id']/'docling';folder.mkdir(parents=True,exist_ok=True);(folder/'settings.json').write_text(json.dumps({**settings,'sha256':hashlib.sha256(pdf.read_bytes()).hexdigest()},indent=2));rows=[]
  if a.all_pages:
   import pymupdf
   with pymupdf.open(pdf) as d:pages=range(1,len(d)+1)
  else:pages=spec['review_pages']
  for pn in pages:
   dest=folder/f'p{pn:03d}.json';start=time.perf_counter()
   if a.resume and dest.exists():
    try:
     existing=json.loads(dest.read_text(encoding='utf-8'));assert str(pn) in existing.get('pages',{})
     rows.append(dict(pdf_page=pn,status='reused_valid_json',json_file=dest.name,seconds=0));print(spec['id'],pn,'CACHED',flush=True);(folder/'run_summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8');continue
    except (ValueError,AssertionError):pass
   print(spec['id'],pn,'START',flush=True)
   try:
    result=converter.convert(str(pdf),page_range=(pn,pn));
    if str(result.status).lower() not in ['success','conversionstatus.success']:raise RuntimeError('Docling conversion status: '+str(result.status))
    data=result.document.export_to_dict();
    def strip_images(obj):
     if isinstance(obj,dict):return {k:strip_images(v) for k,v in obj.items() if k not in ['image']}
     if isinstance(obj,list):return [strip_images(v) for v in obj]
     return obj
    data=strip_images(data);dest.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8');(folder/f'p{pn:03d}.md').write_text(result.document.export_to_markdown(),encoding='utf-8')
    rows.append(dict(pdf_page=pn,status=str(result.status),json_file=dest.name,seconds=time.perf_counter()-start));print(spec['id'],pn,result.status,flush=True)
   except Exception as e:rows.append(dict(pdf_page=pn,status='failed',error=str(e),seconds=time.perf_counter()-start));print(spec['id'],pn,'FAILED',str(e),flush=True)
   (folder/'run_summary.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf-8')
if __name__=='__main__':main()
