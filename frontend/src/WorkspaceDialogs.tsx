import { useState } from 'react';
import { apiErrorText } from './api';
import { Dialog } from './Controls';
import type { DocumentItem, ModelConfig, ModelRequest, Provider } from './contracts';

export function ModelDialog({ config, onClose, onSave }: {
  config: ModelConfig;
  onClose: () => void;
  onSave: (config: ModelRequest) => Promise<void>;
}) {
  const [provider, setProvider] = useState<Provider>(config.provider);
  const [model, setModel] = useState(config.model ?? '');
  const [addresses, setAddresses] = useState({ local: '', cloud: '', [config.provider]: config.base_url ?? '' });
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  return <Dialog title="설정" onClose={onClose}>
    <form className="dialog-body form-stack" onSubmit={async (event) => {
      event.preventDefault();
      const name = model.trim();
      if (saving || !name) return;
      const address = addresses[provider].trim();
      if (address && !/^https?:\/\//i.test(address)) {
        setError('서버 주소를 http:// 또는 https://로 입력하세요.');
        return;
      }
      setSaving(true);
      setError('');
      try {
        await onSave({ provider, model: name, ...(address ? { base_url: address } : {}) });
        onClose();
      } catch (error) {
        setError(apiErrorText(error, '모델 설정을 적용하지 못했습니다.'));
        setSaving(false);
      }
    }}>
      <fieldset className="mode-field"><legend>Ollama 실행 모드</legend>
        <label><input type="radio" name="provider" checked={provider === 'local'} onChange={() => setProvider('local')} />로컬</label>
        <label><input type="radio" name="provider" checked={provider === 'cloud'} onChange={() => setProvider('cloud')} />클라우드</label>
      </fieldset>
      <label className="field">모델명<input type="text" required maxLength={200} value={model} onChange={(event) => setModel(event.target.value)} /></label>
      <label className="field">서버 주소<input type="url" maxLength={300} value={addresses[provider]}
        placeholder={provider === 'local' ? 'http://127.0.0.1:11434' : 'https://ollama.com'}
        onChange={(event) => setAddresses({ ...addresses, [provider]: event.target.value })} /></label>
      {error && <p role="alert">{error}</p>}
      <div className="dialog-actions"><button className="primary-button" disabled={saving || !model.trim()}>
        {saving ? '적용 중…' : '적용'}</button></div>
    </form>
  </Dialog>;
}

export function RenameDialog({ item, onClose, onSave }: {
  item: DocumentItem;
  onClose: () => void;
  onSave: (title: string) => Promise<void>;
}) {
  const [title, setTitle] = useState(item.title);
  const [saving, setSaving] = useState(false);
  const [failed, setFailed] = useState(false);
  return <Dialog title="자료 이름 변경" onClose={onClose}>
    <form className="dialog-body form-stack" onSubmit={async (event) => {
      event.preventDefault();
      if (saving || !title.trim()) return;
      setSaving(true);
      setFailed(false);
      try {
        await onSave(title.trim());
        onClose();
      } catch {
        setFailed(true);
        setSaving(false);
      }
    }}>
      <label className="field">이름<input type="text" autoFocus required maxLength={200} value={title}
        onChange={(event) => setTitle(event.target.value)} /></label>
      {failed && <p role="alert">이름을 변경하지 못했습니다.</p>}
      <div className="dialog-actions"><button className="primary-button" disabled={saving || !title.trim() || title.trim() === item.title}>
        {saving ? '저장 중…' : '저장'}</button></div>
    </form>
  </Dialog>;
}

export function RestoreDialog({ items, onClose, onRestore }: {
  items: DocumentItem[];
  onClose: () => void;
  onRestore: (item: DocumentItem) => Promise<void>;
}) {
  const [restoring, setRestoring] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  return <Dialog title="제외한 자료" onClose={onClose}>
    <div className="dialog-body">
      {items.length ? <ul className="restore-list">{items.map((item) => <li key={item.document_id}>
        <span>{item.title}</span><button className="tonal-button" disabled={restoring !== null}
          aria-label={`${item.title} 복원`} onClick={async () => {
            setRestoring(item.document_id);
            setFailed(false);
            try { await onRestore(item); } catch { setFailed(true); } finally { setRestoring(null); }
          }}>{restoring === item.document_id ? '복원 중…' : '복원'}</button>
      </li>)}</ul> : <p>제외한 자료가 없습니다.</p>}
      {failed && <p role="alert">자료를 복원하지 못했습니다.</p>}
    </div>
  </Dialog>;
}
