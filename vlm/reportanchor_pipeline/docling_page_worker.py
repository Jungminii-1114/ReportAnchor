"""Isolated Docling/EasyOCR worker: one page per process to survive native crashes."""
import argparse, json, os, sys, time
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--pdf',required=True);p.add_argument('--page',type=int,required=True);p.add_argument('--dest',required=True);p.add_argument('--threads',type=int,default=2);a=p.parse_args()
 os.environ.setdefault('OMP_NUM_THREADS',str(a.threads));os.environ.setdefault('MKL_NUM_THREADS',str(a.threads))
 import torch
 torch.set_num_threads(a.threads)
 from docling.document_converter import DocumentConverter,PdfFormatOption
 from docling.datamodel.pipeline_options import PdfPipelineOptions,EasyOcrOptions
 from docling.datamodel.base_models import InputFormat
 opt=PdfPipelineOptions();opt.do_ocr=True;opt.do_table_structure=True;opt.do_picture_classification=True;opt.do_picture_description=False;opt.ocr_options=EasyOcrOptions(lang=['ko','en']);opt.generate_page_images=False;opt.generate_picture_images=True
 converter=DocumentConverter(format_options={InputFormat.PDF:PdfFormatOption(pipeline_options=opt)})
 result=converter.convert(a.pdf,page_range=(a.page,a.page))
 if str(result.status).split('.')[-1] != 'SUCCESS':raise RuntimeError('Docling status '+str(result.status))
 data=result.document.export_to_dict()
 def strip(obj):
  if isinstance(obj,dict):return {k:strip(v) for k,v in obj.items() if k!='image'}
  if isinstance(obj,list):return [strip(v) for v in obj]
  return obj
 data=strip(data)
 if str(a.page) not in data.get('pages',{}):raise RuntimeError('Unexpected page index')
 dest=Path(a.dest);dest.parent.mkdir(parents=True,exist_ok=True);tmp=dest.with_suffix('.tmp');tmp.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8');tmp.replace(dest)
 dest.with_suffix('.md').write_text(result.document.export_to_markdown(),encoding='utf-8')
 print('DOC_SUCCESS',a.page,flush=True)
if __name__=='__main__':main()
