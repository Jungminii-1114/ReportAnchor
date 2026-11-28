import { useRef, useState } from 'react';
import { ArchiveRestore, FileText, FolderOpen, PanelLeftClose, PanelLeftOpen, Pencil, Plus, Search, Settings, X } from 'lucide-react';
import { AnchorTile, IconButton, Popup } from './Controls';
import type { DocumentItem } from './contracts';

interface ExplorerProps {
  collapsed: boolean;
  documents: DocumentItem[];
  openId: string | null;
  selected: ReadonlySet<string>;
  uploading: boolean;
  onCollapse: (collapsed: boolean) => void;
  onPick: () => void;
  onUpload: (files: File[]) => void;
  onOpen: (document: DocumentItem) => void;
  onSelect: (documentId: string, checked: boolean) => void;
  onRename: (document: DocumentItem) => void;
  onArchive: (document: DocumentItem) => void;
  onRestore: () => void;
  onSettings: () => void;
}

export function Explorer(props: ExplorerProps) {
  const [filter, setFilter] = useState('');
  const [dragging, setDragging] = useState(false);
  const searchRef = useRef<HTMLInputElement>(null);
  const dragDepth = useRef(0);
  const { collapsed, documents, onCollapse, uploading } = props;
  const filtered = documents.filter((item) => item.active && item.title.toLocaleLowerCase().includes(filter.trim().toLocaleLowerCase()));

  function openSearch() {
    onCollapse(false);
    requestAnimationFrame(() => searchRef.current?.focus());
  }

  return <aside className={`explorer ${dragging ? 'is-drop-target' : ''}`} aria-label="자료 탐색기"
    onDragEnter={(event) => {
      if (!event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      dragDepth.current += 1;
      if (!uploading) setDragging(true);
    }}
    onDragOver={(event) => {
      if (!event.dataTransfer.types.includes('Files')) return;
      event.preventDefault();
      event.dataTransfer.dropEffect = uploading ? 'none' : 'copy';
    }}
    onDragLeave={() => {
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragging(false);
    }}
    onDrop={(event) => {
      event.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      if (!uploading) props.onUpload(Array.from(event.dataTransfer.files));
    }}>
    <header className="pane-header explorer-header">
      <div className="brand" title="ReportAnchor">{collapsed ? <span className="brand-dot" aria-label="ReportAnchor" /> : <>
        <AnchorTile /><span>ReportAnchor</span>
      </>}</div>
      <IconButton label={collapsed ? '탐색기 펼치기' : '탐색기 접기'} aria-expanded={!collapsed}
        onClick={() => onCollapse(!collapsed)}>
        {collapsed ? <PanelLeftOpen size={17} /> : <PanelLeftClose size={17} />}
      </IconButton>
    </header>
    {collapsed ? <div className="rail">
      <IconButton label="자료 검색" onClick={openSearch}><Search size={18} /></IconButton>
      <IconButton label="자료 보기" onClick={() => onCollapse(false)}><FolderOpen size={18} /></IconButton>
      <IconButton label="자료 추가" onClick={props.onPick} disabled={uploading}><Plus size={19} /></IconButton>
      {props.openId && <IconButton label="열린 자료" className="rail-current" onClick={() => onCollapse(false)}>
        <FileText size={18} />
      </IconButton>}
    </div> : <>
      <div className="material-filter">
        <Search size={15} aria-hidden="true" />
        <input ref={searchRef} type="search" aria-label="자료 이름 필터" placeholder="자료 검색"
          value={filter} onChange={(event) => setFilter(event.target.value)} />
      </div>
      <div className="materials-header">
        <h2>자료</h2>
        <button className="text-button" onClick={props.onPick} disabled={uploading}><Plus size={17} />자료 추가</button>
      </div>
      <ul className="document-list">
        {filtered.map((item) => <li className={`document-row ${item.document_id === props.openId ? 'is-current' : ''}`}
          key={item.document_id}>
          <label className="scope-check" title="검색 범위에 포함">
            <input type="checkbox" aria-label={`${item.title} 검색 범위에 포함`}
              checked={props.selected.has(item.document_id)}
              onChange={(event) => props.onSelect(item.document_id, event.target.checked)} />
          </label>
          <button className="document-open" type="button" title={item.title}
            aria-current={item.document_id === props.openId ? 'true' : undefined} onClick={() => props.onOpen(item)}>
            <FileText size={15} aria-hidden="true" /><span>{item.title}</span>
          </button>
          <Popup label={`${item.title} 자료 메뉴`} compact>{(close) => <>
            <button onClick={() => { close(); props.onRename(item); }}><Pencil size={17} />이름 변경</button>
            <button onClick={() => { close(); props.onArchive(item); }}><X size={17} />제외</button>
          </>}</Popup>
        </li>)}
        {filter.trim() && filtered.length === 0 && <li className="filter-empty">일치하는 자료가 없습니다.</li>}
      </ul>
    </>}
    <footer className="explorer-footer">
      {documents.some((item) => !item.active) && (collapsed ?
        <IconButton label="제외한 자료" onClick={props.onRestore}><ArchiveRestore size={18} /></IconButton> :
        <button className="text-button" onClick={props.onRestore}><ArchiveRestore size={17} />제외한 자료</button>)}
      {collapsed ? <IconButton label="설정" onClick={props.onSettings}><Settings size={18} /></IconButton> :
        <button className="text-button" onClick={props.onSettings}><Settings size={17} />설정</button>}
    </footer>
  </aside>;
}
