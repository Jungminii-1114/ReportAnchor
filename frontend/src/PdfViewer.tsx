import { useEffect, useState } from 'react';
import { api, isAbort } from './api';

export interface PdfViewerProps {
  documentId: string;
  page: number;
  chunkId?: string;
  highlight: boolean;
}

export function PdfViewer({ documentId, page, chunkId, highlight }: PdfViewerProps) {
  const [attempt, setAttempt] = useState(0);
  const [result, setResult] = useState<{ key: string; url: string | null } | null>(null);
  const key = JSON.stringify([documentId, page, chunkId, highlight, attempt]);

  useEffect(() => {
    const controller = new AbortController();
    let url: string | undefined;
    api.page(documentId, page, chunkId, highlight, controller.signal).then((image) => {
      if (controller.signal.aborted) return;
      url = URL.createObjectURL(image);
      setResult({ key, url });
    }).catch((error: unknown) => {
      if (!isAbort(error) && !controller.signal.aborted) setResult({ key, url: null });
    });
    return () => {
      controller.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [documentId, page, chunkId, highlight, key]);

  if (result?.key !== key) return <div className="paper-placeholder" role="status">지면 불러오는 중…</div>;
  if (!result.url) return <div className="paper-placeholder">
    <p role="status">지면을 불러오지 못했습니다.</p>
    <button className="tonal-button" onClick={() => setAttempt(attempt + 1)}>다시 시도</button>
  </div>;
  return <img className="pdf-page" src={result.url} alt={`${page}쪽 PDF 지면`} />;
}
