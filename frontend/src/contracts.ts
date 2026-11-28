export type Provider = 'local' | 'cloud';
export type QueryMode = 'qa' | 'search';
export type EvidenceLimit = 3 | 5 | 10;
export const maxDocumentScope = 50;

export interface ModelConfig {
  provider: Provider;
  model: string | null;
  base_url?: string | null;
  enabled?: boolean;
  api_key_configured?: boolean;
}

export interface ModelRequest { provider: Provider; model: string; base_url?: string }

export interface DocumentItem {
  document_id: string;
  title: string;
  active: boolean;
  page_count: number;
  document_version?: string;
  chunk_count?: number;
  created_at?: string;
}

export interface DocumentDetail extends DocumentItem {
  filename?: string;
  block_count?: number;
  pdf_available?: boolean;
  text?: string;
  pages?: Array<{ page: number; text: string }>;
}

export interface DocumentsResponse {
  documents: DocumentItem[];
  model: ModelConfig;
}

export interface SourceSpan {
  block_id: string;
  page?: number;
  pdf_page?: number;
  start_offset?: number;
  end_offset?: number;
  text?: string;
  bbox?: number[];
}

export interface TableContext {
  table_title?: string;
  row_headers?: string[];
  column_headers?: string[];
  unit?: string;
}

export interface SourceReference {
  document_id: string;
  document_version?: string;
  chunk_id: string;
  document_title?: string;
  pages?: number[];
  expanded_pages?: number[];
  page?: number;
  pdf_page?: number;
  quote?: string;
  excerpt?: string;
  source_refs?: SourceSpan[];
  expanded_source_refs?: SourceSpan[];
  table_context?: TableContext | null;
}

export interface ChunkDetail {
  chunk_id: string;
  document_id?: string;
  document_version?: string;
  pages?: number[];
  text?: string;
  raw_content?: string;
  context_text?: string;
  quote?: string;
  source_refs?: SourceSpan[];
  expanded_source_refs?: SourceSpan[];
  table_context?: TableContext | null;
}

export interface VerifiedClaim {
  claim_id: string;
  claim_text: string;
  verdict?: 'supported';
  reason?: string | null;
  evidence_refs?: string[];
}

export interface UnconfirmedItem {
  claim_id?: string;
  claim_text?: string | null;
  status?: 'contradicted' | 'insufficient' | 'unsupported' | 'insufficient_evidence' | 'unverifiable';
  verdict?: UnconfirmedItem['status'];
  reason?: string | null;
  evidence_refs?: string[];
}

export const threadLabels = {
  complete: '답변 완료',
  partial: '일부 확인',
  insufficient: '근거 부족',
  clarification: '확인 필요',
  unsupported: '미지원',
  error: '오류',
} as const;

export type ThreadStatus = keyof typeof threadLabels;

export interface FinalReview {
  schema_version: string;
  request_ref: string;
  mode: QueryMode;
  thread_status: ThreadStatus;
  status_label: string;
  summary_text: string | null;
  evidence_incomplete: boolean;
  evidence_notice?: string | null;
  elapsed_ms: number;
  verified_claims: VerifiedClaim[];
  unconfirmed_items: UnconfirmedItem[];
  sources: SourceReference[];
  error_message?: string | null;
}

export interface QueryRequest {
  question: string;
  document_ids: string[];
  mode: QueryMode;
  top_k: EvidenceLimit;
  request_ref: string;
}

export interface JobAccepted { job_id: string }

export interface Job<T> {
  job_id: string;
  kind: 'upload' | 'query';
  status: 'queued' | 'running' | 'completed' | 'error' | 'failed' | 'cancelled';
  stage?: string;
  progress?: number | null;
  result?: T | null;
  error?: string | null;
}
