"""Frozen source-evidence-policy-revision-2. Selection only; no inference."""

import collections, math, re, statistics

ACTIVE = {"vlm_candidate", "review_required"}

VISUAL = {"flow_chart","bar_chart","line_chart","pie_chart","scatter_plot","box_plot","other_chart","table"}

DECOR = {"logo","icon","signature","stamp","qr_code","bar_code"}

def area(b): return max(0,b[2]-b[0])*max(0,b[3]-b[1])

def inter(a,b): return max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))

def coverage(a,b): return inter(a,b)/max(area(a),1e-9)

def union(boxes): return [min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]

def pad(b,n,w,h): return [max(0,b[0]-n),max(0,b[1]-n),min(w,b[2]+n),min(h,b[3]+n)]

def center(b): return ((b[0]+b[2])/2,(b[1]+b[3])/2)

def norm(s): return re.sub(r'\s+','',str(s))

def to_box(p,h):
 b=p['bbox'];return [b['l'],h-b['t'],b['r'],h-b['b']] if b.get('coord_origin')=='BOTTOMLEFT' else [b['l'],b['t'],b['r'],b['b']]

def number_token(s):
 s=norm(s)
 if re.fullmatch(r'(?:19|20)\d{2}(?:[.년/-]\d{1,2})*(?:[.월/-]\d{1,2})?[.일]?',s): return False
 return bool(re.fullmatch(r'(?:세계|국내|글로벌)?(?:개|명|건|조원|억원|백만원|천원|원)?[+-]?\d[\d,.]*(?:%|위|개|명|건|조원|억원|백만원|천원|원)?',s))

def numeric_tokens(s): return collections.Counter(norm(t) for t in re.findall(r'[+-]?\d+(?:[,.]\d+)*\s*%?',str(s)))

def same_x(a,b): return min(a[2],b[2])-max(a[0],b[0])>0

def source_canonical(text):
 import unicodedata
 return re.sub(r'\s+','',unicodedata.normalize('NFKC',str(text)).translate(str.maketrans({'’':"'",'‘':"'",'“':'"','”':'"','‐':'-','‑':'-','–':'-','—':'-','･':'·','・':'·'})))

def audit_source_table(obj,page):
 words=page.get_text('words');h=page.rect.height;data=obj['data'];cells=data.get('table_cells',[]);checked=[];covered=set();occupied=set();struct=[]
 rows,cols=data.get('num_rows',0),data.get('num_cols',0)
 grid=data.get('grid',[])
 if len(grid)!=rows or any(len(r)!=cols for r in grid):struct.append('grid_shape_mismatch')
 for cell in cells:
  r0,r1=cell['start_row_offset_idx'],cell['end_row_offset_idx'];c0,c1=cell['start_col_offset_idx'],cell['end_col_offset_idx']
  if not (0<=r0<r1<=rows and 0<=c0<c1<=cols):struct.append('cell_index_out_of_bounds')
  slots={(r,c) for r in range(r0,r1) for c in range(c0,c1)}
  if occupied&slots:struct.append('overlapping_cell_slots')
  occupied|=slots
  if not str(cell.get('text','')).strip():continue
  if not cell.get('bbox'):checked.append({'match':False,'reason':'cell_bbox_missing','docling':cell['text']});continue
  b=to_box({'bbox':cell['bbox']},h);b=[b[0]-.6,b[1]-.6,b[2]+.6,b[3]+.6]
  ids=[i for i,w in enumerate(words) if b[0]<=(w[0]+w[2])/2<=b[2] and b[1]<=(w[1]+w[3])/2<=b[3]]
  text=' '.join(words[i][4] for i in ids);covered.update(ids)
  checked.append({'match':source_canonical(text)==source_canonical(cell['text']),'bbox':b,'native':text,'docling':cell['text']})
 prov=next(p for p in obj['prov'] if int(p['page_no'])==page.number+1);b=to_box(prov,h)
 inside={i for i,w in enumerate(words) if b[0]<=(w[0]+w[2])/2<=b[2] and b[1]<=(w[1]+w[3])/2<=b[3]}
 misses=[{'text':words[i][4],'bbox':list(words[i][:4])} for i in sorted(inside-covered)]
 mismatches=[c for c in checked if not c['match']]
 headers=[c for c in cells if c.get('column_header') and str(c.get('text','')).strip()]
 if headers:
  header_cols={col for c in headers for col in range(c['start_col_offset_idx'],c['end_col_offset_idx'])}
  body_cols={col for c in cells if not c.get('column_header') and str(c.get('text','')).strip() for col in range(c['start_col_offset_idx'],c['end_col_offset_idx'])}
  if body_cols-header_cols:struct.append('body_columns_without_header_coverage')
 keep=bool(inside and checked and not mismatches and not misses and not struct)
 return {'reuse_candidate':keep,'checked_nonempty_cells':len(checked),'native_words_in_table':len(inside),'cell_mismatches':mismatches,'native_words_not_assigned_to_any_cell':misses,'structure_flags':sorted(set(struct)),'native_text_unavailable':not inside,'method':'native_words_at_docling_cell_coordinates_and_grid_consistency','verification_status':'source_text_agreement_not_gold_semantics'}

def select_source_policy(raw,native,page):
 """Complete-page policy trial. Uses source evidence; no OCR or VLM inference."""
 w,h=page.rect.width,page.rect.height;pn=page.number+1
 items=[]
 for kind in ['texts','tables','pictures']:
  for obj in raw.get(kind,[]):
   for p in obj.get('prov',[]):
    if int(p['page_no'])==pn:items.append({'kind':kind,'ref':obj['self_ref'],'obj':obj,'bbox':to_box(p,h),'text':obj.get('text',''),'label':obj.get('label')})
 texts=[t for t in items if t['kind']=='texts' and t['text'].strip() and t['label'] not in {'page_header','page_footer'}]
 line=statistics.median([t['bbox'][3]-t['bbox'][1] for t in texts]) if texts else 12
 regions=[];table_audits=[];native_available=bool(page.get_text().strip())
 def add(b,kind,decision,reason,refs,evidence=None):
  target=pad(b,0,w,h);r={'region_id':f'p{pn:04d}_policy_{len(regions)+1:03d}','kind':kind,'target_bbox':target,'input_bbox':pad(target,4,w,h),'decision':decision,'reason':[reason],'source_refs':list(dict.fromkeys(refs)),'evidence':evidence or {},'verification_status':'unverified','context_candidates':[],'input_padding_pt':4};regions.append(r);return r
 for x in items:
  if x['kind']!='tables':continue
  evidence=audit_source_table(x['obj'],page);table_audits.append({'source_ref':x['ref'],'bbox':x['bbox'],**evidence})
  add(x['bbox'],'table','parser_only' if evidence['reuse_candidate'] else 'review_required','cell_source_and_grid_agree' if evidence['reuse_candidate'] else 'table_source_or_structure_not_sufficient', [x['ref']],evidence)
 # Text on a raster page is retained even when it is not selected for supplementation.
 text_policy={'native_text':'retain_original_native_text' if native_available else 'unavailable','docling_text':'retain_with_source_and_unverified_state','all_ocr_text_auto_selected':False}
 outside=[t for t in texts if not any(coverage(t['bbox'],r['target_bbox'])>.8 for r in regions if r['kind']=='table')]
 years=[t for t in outside if re.fullmatch(r'(?:18|19|20)\d{2}',t['text'].strip())]
 decades=[t for t in outside if re.fullmatch(r"(?:18|19|20)\d0['’]?s",norm(t['text']),re.I)]
 dated=[t for t in outside if re.match(r'^\s*(?:18|19|20)\d{2}(?:\s*[./년-]\s*|\s+)\w',t['text']) and t not in decades]
 date_figures=[]
 if len(dated)>=3 and len(decades)>=2 and len({re.search(r'(?:18|19|20)\d{2}',t['text']).group() for t in dated})>=3:
  for x in items:
   if x['kind']!='pictures' or area(x['bbox'])<.1*w*h:continue
   anchors_in=[t for t in dated if coverage(t['bbox'],x['bbox'])>=.8]
   decades_in=[t for t in decades if coverage(t['bbox'],x['bbox'])>=.8]
   if len(anchors_in)>=3 and len(decades_in)>=2:date_figures.append((x,anchors_in,decades_in))
 if date_figures:
  x,anchors_in,decades_in=min(date_figures,key=lambda row:area(row[0]['bbox']))
  add(x['bbox'],'dated_timeline_figure','vlm_candidate','dated_event_layout_relationships_unresolved',[x['ref']]+[t['ref'] for t in anchors_in+decades_in],{'date_prefix_blocks':len(anchors_in),'decade_markers':len(decades_in),'dates_not_corrected_or_filled':True})
 timeline=bool(len(years)>=6 and len(decades)>=2) and not date_figures
 if timeline:
  bands=[]
  for t in sorted(years,key=lambda t:center(t['bbox'])[1]):
   cy=center(t['bbox'])[1]
   band=next((g for g in bands if abs(g['cy']-cy)<=10),None)
   if band:band['years'].append(t);band['cy']=statistics.median(center(y['bbox'])[1] for y in band['years'])
   else:bands.append({'cy':cy,'years':[t]})
  if len(bands)<2:timeline=False
  else:
   content=[t for t in outside if t not in decades and t['label'] not in {'page_header','page_footer'}]
   xmin=min(t['bbox'][0] for t in content);xmax=max(t['bbox'][2] for t in content)
   for j,band in enumerate(bands):
    top=min(t['bbox'][1] for t in band['years'])-3
    nexttop=min(t['bbox'][1] for t in bands[j+1]['years']) if j+1<len(bands) else h-.04*h
    bottom=min(nexttop-8,top+max(75,.6*(nexttop-top)))
    members=[t for t in content if top-2<=t['bbox'][1]<bottom]
    bottom=max([t['bbox'][3] for t in members]+[top+20])
    sy=sorted(band['years'],key=lambda t:center(t['bbox'])[0]);chunks=[sy[i:i+3] for i in range(0,len(sy),3)]
    for k,chunk in enumerate(chunks):
     left=xmin if k==0 else (center(chunks[k-1][-1]['bbox'])[0]+center(chunk[0]['bbox'])[0])/2
     right=xmax if k==len(chunks)-1 else (center(chunk[-1]['bbox'])[0]+center(chunks[k+1][0]['bbox'])[0])/2
     add([left,top,right,bottom],'timeline_band','vlm_candidate','multirow_year_layout_relationships_unresolved',[t['ref'] for t in members if left<=center(t['bbox'])[0]<=right],{'year_anchors':len(chunk),'band_anchors':len(sy),'missing_year_labels_can_exist':'unknown_not_filled_from_a_year_sequence'})
 # Vertically aligned small graphics plus numeric neighbor rows form a table-like visual panel.
 small=[x for x in items if x['kind']=='pictures' and area(x['bbox'])<.005*w*h and x['bbox'][2]-x['bbox'][0]<.1*w]
 columns=[]
 for x in sorted(small,key=lambda t:center(t['bbox'])[0]):
  cx=center(x['bbox'])[0];col=next((g for g in columns if abs(g['cx']-cx)<=max(4,(x['bbox'][2]-x['bbox'][0])*.4)),None)
  if col:col['items'].append(x)
  else:columns.append({'cx':cx,'items':[x]})
 for col in columns:
  icons=sorted(col['items'],key=lambda x:x['bbox'][1]);groups=[]
  for x in icons:
   if groups and x['bbox'][1]-groups[-1][-1]['bbox'][3]<=max(30,4*(x['bbox'][3]-x['bbox'][1])):groups[-1].append(x)
   else:groups.append([x])
  for icons in groups:
   if len(icons)<3:continue
   neighbors=[]
   for x in icons:
    b=x['bbox'];near=[t for t in outside if t['bbox'][0]>=b[2]-4 and t['bbox'][0]-b[2]<=max(25,3*(b[2]-b[0])) and abs(center(t['bbox'])[1]-center(b)[1])<=max(b[3]-b[1],t['bbox'][3]-t['bbox'][1])*.7]
    neighbors.extend(near)
   if sum(bool(numeric_tokens(t['text'])) for t in neighbors)<2:continue
   b=union([x['bbox'] for x in icons]+[t['bbox'] for t in neighbors])
   above=[t for t in outside if same_x(t['bbox'],b) and 0<=b[1]-t['bbox'][3]<=max(80,8*line) and (number_token(t['text']) or t['label']=='section_header' or t['bbox'][3]-t['bbox'][1]>line*1.2)]
   b=union([b]+[t['bbox'] for t in above])
   notes=[t for t in outside if same_x(t['bbox'],b) and 0<=t['bbox'][1]-b[3]<=2*line and re.search('기준|출처|단위|^주[:：]',t['text'])]
   add(union([b]+[t['bbox'] for t in notes]),'visual_numeric_panel','vlm_candidate','aligned_icons_with_numeric_rows_ocr_completeness_unresolved',[x['ref'] for x in icons]+[t['ref'] for t in neighbors+above+notes],{'icon_rows':len(icons),'numeric_rows_detected':sum(bool(numeric_tokens(t['text'])) for t in neighbors)})
 for x in items:
  if x['kind']!='pictures':continue
  obj=x['obj'];pred=obj.get('meta',{}).get('classification',{}).get('predictions',[])
  if not pred:pred=[p for a in obj.get('annotations',[]) if a.get('kind')=='classification' for p in a.get('predicted_classes',a.get('predictions',[]))]
  top=max(pred,key=lambda p:p.get('confidence',0),default={});label=top.get('class_name',top.get('predicted_class'));conf=top.get('confidence',0)
  if any(coverage(x['bbox'],r['target_bbox'])>.9 for r in regions):continue
  inside=[t for t in texts if coverage(t['bbox'],x['bbox'])>.8]
  visual_mass=sum(v.get('confidence',0) for v in pred if v.get('class_name',v.get('predicted_class')) in VISUAL)
  distinct_labels={source_canonical(t['text']) for t in inside if len(source_canonical(t['text']))>=2 and re.search(r'[A-Za-z가-힣]',t['text'])}
  weak_structure=(label in VISUAL and visual_mass>=.5 and len(distinct_labels)>=6 and area(x['bbox'])>=.02*w*h)
  photo_family=label in {'photograph','full_page_image','screenshot_from_computer','screenshot_from_manual'}
  large_photo=photo_family and area(x['bbox'])>=.12*w*h and x['bbox'][3]-x['bbox'][1]>=.3*h
  prominent_inside=[t for t in inside if number_token(t['text']) and t['bbox'][3]-t['bbox'][1]>=18]
  ambiguous_quant_panel=(label in {'full_page_image','screenshot_from_computer','screenshot_from_manual'} and len(prominent_inside)>=1 and sum(number_token(t['text']) for t in inside)>=2)
  if ambiguous_quant_panel:
   add(x['bbox'],'quantitative_picture','review_required','numeric_layout_in_ambiguous_raster_picture',[x['ref']],{'class':label,'prominent_values':len(prominent_inside),'retained_numeric_anchors':sum(number_token(t['text']) for t in inside)})
  elif large_photo:
   b=x['bbox'];ph=b[3]-b[1];margin=min(40,max(12,.06*ph));header=[b[0],max(0,b[1]-margin),b[2],min(h,b[1]+.28*ph)]
   nearby=[t for t in texts if coverage(t['bbox'],header)>=.75 and t['bbox'][3]-t['bbox'][1]<=max(65,.18*ph)]
   if nearby:
    header=pad(union([header]+[t['bbox'] for t in nearby]),0,w,h)
    native_header=source_canonical(page.get_text('text',clip=tuple(header)))
    native_supported=bool(native_header and all(source_canonical(t['text']) in native_header for t in nearby))
    if not native_supported:
     add(header,'photo_text_band','ocr_candidate','photo_text_source_quality_requires_ocr_review',[x['ref']]+[t['ref'] for t in nearby],{'photo_bbox':b,'recognized_text_blocks_near_header':len(nearby),'native_support_for_header':False,'header_band_is_a_detection_guard_not_exact_glyph_bounds':True,'ocr_text_is_not_assumed_wrong':True})
   add(x['bbox'],'picture','exclude_vlm','photo_body_preserved_ocr_text_route_is_separate',[x['ref']],{'class':label,'confidence':conf})
  elif label in VISUAL and conf>=.6 or weak_structure:
   add(x['bbox'],'diagram','vlm_candidate','diagram_relationships_not_available_as_structured_evidence',[x['ref']],{'class':label,'confidence':conf,'retained_text_items':len(inside),'visual_class_score_sum':visual_mass,'distinct_label_blocks':len(distinct_labels),'single_classifier_cutoff_bypassed':weak_structure and conf<.6})
  elif label=='photograph' and conf>=.9 or label in DECOR and conf>=.9 and area(x['bbox'])<.01*w*h:
   add(x['bbox'],'picture','exclude_vlm','photo_or_decoration_preserved',[x['ref']],{'class':label,'confidence':conf})
  elif not native_available and sum(number_token(t['text']) for t in inside)>=2:
   add(x['bbox'],'quantitative_picture','review_required','picture_with_multiple_numeric_anchors_needs_review',[x['ref']],{'retained_numeric_anchors':sum(number_token(t['text']) for t in inside)})
  else:add(x['bbox'],'picture','review_unresolved','visual_type_or_text_completeness_unknown',[x['ref']],{'class':label,'confidence':conf})
 if not timeline:
  anchors=[t for t in outside if number_token(t['text']) and t['bbox'][3]-t['bbox'][1]>=max(18,line*1.5) and not any(coverage(t['bbox'],r['target_bbox'])>.8 for r in regions if r['decision'] in ACTIVE or r['decision']=='parser_only')]
  for t in anchors:
   b=t['bbox'];neighbors=[u for u in outside if u is not t and not number_token(u['text']) and same_x(u['bbox'],b) and max(0,u['bbox'][1]-b[3],b[1]-u['bbox'][3])<=max(24,(b[3]-b[1])*.7)]
   label=min(neighbors,key=lambda u:max(0,u['bbox'][1]-b[3],b[1]-u['bbox'][3]),default=None)
   add(union([b,label['bbox']]) if label else b,'metric','review_required','prominent_value_label_link_not_verified',[t['ref']]+([label['ref']] if label else []))
 # Recover context from aligned source raster strips for diagrams, not full-page scans.
 raster_groups=[]
 raster_boxes=sorted([list(i['bbox']) for i in page.get_image_info() if i['bbox'][2]-i['bbox'][0]>=.15*w],key=lambda b:(round(b[0]),b[1]))
 for b in raster_boxes:
  group=next((g for g in reversed(raster_groups) if abs(g[0]-b[0])<=2 and abs(g[2]-b[2])<=2 and -.5<=b[1]-g[3]<=2),None)
  if group:group[1]=min(group[1],b[1]);group[3]=max(group[3],b[3])
  else:raster_groups.append(list(b))
 for r in regions:
  if r['kind']!='diagram' or r['decision'] not in ACTIVE:continue
  candidates=[g for g in raster_groups if area(g)<.85*w*h and coverage(r['target_bbox'],g)>=.85 and area(g)<=3*area(r['target_bbox'])]
  if candidates:
   g=min(candidates,key=area);r['input_bbox']=pad(union([r['target_bbox'],g]),4,w,h);r['evidence']['input_source_raster_group']=g
 # Explicit captions/footnotes and one nearby section heading are context candidates.
 byref={x['ref']:x for x in items}
 for r in regions:
  linked=[byref[ref]['obj'] for ref in r['source_refs'] if ref in byref];explicit={c['$ref'] for o in linked for key in ['captions','footnotes'] for c in o.get(key,[]) if '$ref' in c}
  ctx=[{'source_ref':t['ref'],'text':t['text'],'bbox':t['bbox'],'semantic_link':'explicit_reference_unverified'} for t in texts if t['ref'] in explicit]
  if r['kind'] in {'diagram','dated_timeline_figure','visual_numeric_panel','quantitative_picture'} and r['decision'] in ACTIVE:
   base=r['input_bbox'];headings=[t for t in texts if t['label']=='section_header' and same_x(t['bbox'],base) and base[0]<=center(t['bbox'])[0]<=base[2] and 0<=base[1]-t['bbox'][3]<=max(16,min(40,2*line))]
   if headings:
    t=min(headings,key=lambda t:base[1]-t['bbox'][3]);ctx.append({'source_ref':t['ref'],'text':t['text'],'bbox':t['bbox'],'semantic_link':'nearby_heading_candidate_unverified'})
  r['context_candidates']=ctx
  if ctx:
   r['input_bbox']=pad(union([r['target_bbox']]+[c['bbox'] for c in ctx]),4,w,h) if r['kind']=='table' else pad(union([r['input_bbox']]+[c['bbox'] for c in ctx]),2,w,h)
  r.update(doc_id=native['doc_id'],version_sha256=native['version_sha256'],pdf_page=pn,rule_version='source-evidence-policy-revision-2',source_locator={'type':'pdf_page_bbox','pdf_page':pn,'bbox':r['target_bbox'],'units':'pt','coordinate_space':'pymupdf_unrotated_page'},next_action='targeted_ocr_then_reassess' if r['decision']=='ocr_candidate' else 'use_existing_source_candidate' if r['decision']=='parser_only' else 'preserve_source_no_vlm' if r['decision']=='exclude_vlm' else 'supplement_candidate' if r['decision'] in ACTIVE else 'review_unknown_do_not_mark_success')
 return {'pdf_page':pn,'page_size':[w,h],'regions':regions,'table_audits':table_audits,'text_policy':text_policy,'page_completeness':'unverified','unknown_source_content':'cannot_be_proved_absent_by_ocr_alone'}
