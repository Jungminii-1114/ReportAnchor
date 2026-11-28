import type {
  ChunkDetail, DocumentDetail, DocumentItem, DocumentsResponse,
  Job, JobAccepted, ModelConfig, ModelRequest, QueryRequest,
} from './contracts';

class ApiError extends Error {
  constructor(readonly status: number, readonly detail?: string) {
    super('서버 요청에 실패했습니다.');
  }
}

async function errorDetail(response: Response) {
  try {
    const body: unknown = await response.json();
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (typeof detail === 'string' && detail.trim() && detail.length <= 200) return detail.trim();
  } catch {
    // 본문이 JSON이 아니면 상태 코드만 사용한다.
  }
  return undefined;
}

class JobError extends Error {}

export function isAbort(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError';
}

export function apiErrorText(error: unknown, fallback: string) {
  if (error instanceof JobError && error.message) return error.message;
  if (!(error instanceof ApiError)) return fallback;
  if (error.status === 413) return '파일이 업로드 허용 크기를 초과했습니다.';
  if (error.status === 429) return '작업이 많습니다. 잠시 후 다시 시도하세요.';
  if (error.status === 400 && error.detail) return error.detail;
  return fallback;
}

async function checkedFetch(path: string, options?: RequestInit) {
  const response = await fetch(`/api${path}`, options);
  if (!response.ok) throw new ApiError(response.status, await errorDetail(response));
  return response;
}

async function json<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await checkedFetch(path, options);
  return response.json() as Promise<T>;
}

function jsonBody(method: 'POST' | 'PATCH', value: unknown, signal?: AbortSignal): RequestInit {
  return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value), signal };
}

function documentPath(documentId: string) {
  return `/documents/${encodeURIComponent(documentId)}`;
}

export const api = {
  health: (signal?: AbortSignal) => checkedFetch('/health', { signal }),
  documents: (signal?: AbortSignal) => json<DocumentsResponse>('/documents', { signal }),
  document: (id: string, signal?: AbortSignal) => json<DocumentDetail>(documentPath(id), { signal }),
  updateDocument: (id: string, patch: { title?: string; active?: boolean }) =>
    json<DocumentItem>(documentPath(id), jsonBody('PATCH', patch)),
  upload(file: File, signal?: AbortSignal) {
    const body = new FormData();
    body.append('file', file);
    return json<JobAccepted>('/documents/upload', { method: 'POST', body, signal });
  },
  async page(id: string, page: number, chunkId: string | undefined, highlight: boolean, signal: AbortSignal) {
    const params = new URLSearchParams({ highlight: highlight ? '1' : '0', scale: '1.5' });
    if (chunkId) params.set('chunk_id', chunkId);
    const response = await checkedFetch(`${documentPath(id)}/pages/${page}?${params}`, { signal });
    const image = await response.blob();
    if (image.type !== 'image/png' || image.size === 0) throw new Error('Invalid page image');
    return image;
  },
  pageText: (id: string, page: number, signal?: AbortSignal) =>
    json<{ document_id: string; page: number; text: string }>(`${documentPath(id)}/pages/${page}/text`, { signal }),
  chunk: (id: string, chunkId: string, signal?: AbortSignal) =>
    json<ChunkDetail>(`${documentPath(id)}/chunks/${encodeURIComponent(chunkId)}`, { signal }),
  model: (settings: ModelRequest) => json<ModelConfig>('/model', jsonBody('POST', settings)),
  query: (request: QueryRequest, signal?: AbortSignal) =>
    json<JobAccepted>('/queries', jsonBody('POST', request, signal)),
  job: <T>(id: string, signal: AbortSignal) => json<Job<T>>(`/jobs/${encodeURIComponent(id)}`, { signal }),
};

function waitForPoll(signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    signal.throwIfAborted();
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, 600);
    function abort() {
      window.clearTimeout(timer);
      reject(signal.reason);
    }
    signal.addEventListener('abort', abort, { once: true });
  });
}

export async function pollJob<T>(id: string, signal: AbortSignal, onUpdate?: (job: Job<T>) => void) {
  if (!id) throw new Error('Missing job id');
  while (true) {
    await waitForPoll(signal);
    const job = await api.job<T>(id, signal);
    onUpdate?.(job);
    if (job.status === 'completed') return job.result;
    if (job.status !== 'queued' && job.status !== 'running') throw new JobError(job.error ?? '');
  }
}

export type UploadResult = Partial<Pick<DocumentItem, 'document_id'>>;
