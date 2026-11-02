"""Full native parsing and auditable candidate signals. No OCR/VLM calls."""
import argparse,hashlib,json,time,subprocess,sys,html
from pathlib import Path
import pymupdf
import numpy as np
from scipy import ndimage as ndi
from PIL import Image,ImageDraw
VERSION='parser-python-signals/1'
def write(path,obj):
 path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
def table_worker(pdf,pn,dest):
 with pymupdf.open(pdf) as d:
  p=d[pn-1];tables=[]
  for t in p.find_tables().tables:
   rows=t.extract();tables.append(dict(bbox=list(t.bbox),row_count=t.row_count,column_count=t.col_count,rows=rows,cells=[list(c) if c is not None else None for c in t.cells]))
  write(dest,tables)
def grid(p):
 pix=p.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5),colorspace=pymupdf.csGRAY,alpha=False);ink=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width)<170;h,w=ink.shape
 hor=ndi.binary_opening(ink,structure=np.ones((1,max(25,int(w*.04))),bool));ver=ndi.binary_opening(ink,structure=np.ones((max(20,int(h*.015)),1),bool));lab,_=ndi.label(ndi.binary_dilation(hor|ver,iterations=3));out=[]
 for sl in ndi.find_objects(lab):
  if sl is None:continue
  ys,xs=sl
  if xs.stop-xs.start<w*.2 or ys.stop-ys.start<h*.04:continue
  _,cross=ndi.label(ndi.binary_dilation(hor[sl],iterations=2)&ndi.binary_dilation(ver[sl],iterations=2))
  if cross>=6:out.append(dict(bbox=[xs.start/1.5,ys.start/1.5,xs.stop/1.5,ys.stop/1.5],signals=['legacy_line_grid'],intersections=cross,decision='review_required'))
 return out
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--config');ap.add_argument('--table-timeout',type=float,default=6);ap.add_argument('--worker',nargs=3);a=ap.parse_args()
 if a.worker:table_worker(a.worker[0],int(a.worker[1]),a.worker[2]);return
 config=Path(a.config).resolve();cfg=json.loads(config.read_text(encoding='utf-8'));root=config.parent;out=root/cfg.get('output','results');summ=[]
 for spec in cfg['documents']:
  pdf=(root/spec['pdf']).resolve();key=spec['id'];folder=out/key;folder.mkdir(parents=True,exist_ok=True);sha=hashlib.sha256(pdf.read_bytes()).hexdigest();docid='doc_'+sha[:16];pages=[];start=time.perf_counter()
  with pymupdf.open(pdf) as doc:
   for i,p in enumerate(doc,1):
    text=p.get_text('text',sort=True);blocks=[dict(bbox=list(v[:4]),text=v[4]) for v in p.get_text('blocks',sort=True) if v[6]==0];cache=folder/'tables'/f'p{i:03d}.json';cache.parent.mkdir(exist_ok=True)
    try:
     run=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',str(pdf),str(i),str(cache)],capture_output=True,text=True,timeout=a.table_timeout,encoding='utf-8',errors='replace')
     if run.returncode:tables=[];status='error';error=run.stderr[-800:]
     else:tables=json.loads(cache.read_text());status='completed';error=None
    except subprocess.TimeoutExpired:tables=[];status='timeout';error=f'table detection exceeded {a.table_timeout}s'
    pix=p.get_pixmap(matrix=pymupdf.Matrix(.5,.5),colorspace=pymupdf.csGRAY,alpha=False);arr=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width);ink=float((arr<220).mean());h,w=arr.shape;inner=arr[int(h*.1):int(h*.9),int(w*.05):int(w*.95)];blank=ink<.001 and float((inner<220).mean())==0
    candidates=[]
    for t in tables:
     rows=t['rows'];signals=[]
     if any(v is None for row in rows for v in row):signals.append('merged_or_missing_cells_not_confirmed_error')
     if rows and any(v is None or not str(v).strip() for v in rows[0]):signals.append('empty_header')
     if len({len(row) for row in rows})>1:signals.append('unequal_row_lengths')
     if signals:candidates.append(dict(bbox=t['bbox'],signals=signals,decision='review_required'))
    if len(text.strip())<50 and not blank:
     imgs=[]
     for info in p.get_image_info():
      q=pymupdf.Rect(info['bbox'])&p.rect
      if not q.is_empty and q.get_area()/p.rect.get_area()>=.02 and min(q.width,q.height)>=40 and len(p.get_text('text',clip=q).strip())<50:
       if not any(sum(abs(x-y) for x,y in zip(list(q),b))<1 for b in imgs):imgs.append(list(q))
     for q in imgs or [list(p.rect)]:candidates.append(dict(bbox=q,signals=['content_present_native_text_low'],decision='review_required'))
    record=dict(doc_id=docid,version_sha256=sha,pdf_page=i,page_size=[p.rect.width,p.rect.height],text=text,blocks=blocks,text_characters=len(text.strip()),near_blank=blank,ink_ratio=ink,table_detection_status=status,table_error=error,tables=tables,candidates=candidates,verification_status='unverified')
    if i in spec['review_pages']:
     record['legacy_grid']=grid(p)
     for method,boxes,color in [('parser_tables',tables,'blue'),('python_signals',candidates,'red'),('legacy_grid',record['legacy_grid'],'red')]:
      pix=p.get_pixmap(matrix=pymupdf.Matrix(1,1),colorspace=pymupdf.csRGB,alpha=False);im=Image.frombytes('RGB',[pix.width,pix.height],pix.samples);dr=ImageDraw.Draw(im)
      for j,c in enumerate(boxes,1):dr.rectangle(c['bbox'],outline=color,width=2);dr.text((c['bbox'][0]+2,c['bbox'][1]+2),str(j),fill=color)
      dest=folder/'images'/f'p{i:03d}_{method}.png';dest.parent.mkdir(exist_ok=True);im.save(dest)
    pages.append(record)
    print(key,i,'chars',record['text_characters'],'tables',len(tables),status,flush=True)
  write(folder/'parser_python.json',dict(rule_version=VERSION,parser_version=pymupdf.VersionBind,parameters=dict(low_native_text_characters=50,image_area_ratio=.02,min_image_side_pt=40,table_timeout_seconds=a.table_timeout),document_id=docid,sha256=sha,file_name=pdf.name,pages=pages,coordinate_system='PyMuPDF unrotated page pt',notes=['Candidates are signals, not confirmed parsing errors or VLM requirements.']))
  summ.append(dict(document=key,pages=len(pages),no_native_text_pages=sum(x['text_characters']==0 for x in pages),near_blank_pages=[x['pdf_page'] for x in pages if x['near_blank']],table_detection_completed=sum(x['table_detection_status']=='completed' for x in pages),table_detection_timeout=sum(x['table_detection_status']=='timeout' for x in pages),table_detection_errors=sum(x['table_detection_status']=='error' for x in pages),tables=sum(len(x['tables']) for x in pages),candidate_regions=sum(len(x['candidates']) for x in pages),seconds=time.perf_counter()-start))
  write(out/'parser_summary.json',summ)
if __name__=='__main__':main()
