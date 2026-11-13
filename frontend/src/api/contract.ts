import type {
  AuditEvent,
  AuditLogFilters,
  AuditLogRow,
  AuditStatus,
  CompanyInfo,
  FilingCandidate,
  HealthResponse,
  ResumePayload,
  StartAuditRequest,
  ThreadSummary,
  VerifyResult,
} from "./types";

export interface TraceApi {
  health(): Promise<HealthResponse>;
  listCompanies(): Promise<CompanyInfo[]>;
  searchCompanies(query: string): Promise<CompanyInfo[]>;
  listFilings(cik: string, form?: string): Promise<FilingCandidate[]>;
  listFilingForms(): Promise<string[]>;
  startAudit(request: StartAuditRequest): Promise<AuditStatus>;
  listThreads(): Promise<ThreadSummary[]>;
  getStatus(threadId: string): Promise<AuditStatus>;
  getTimeline(threadId: string): Promise<AuditEvent[]>;
  resume(threadId: string, payload: ResumePayload): Promise<AuditStatus>;
  listAuditLogs(filters?: AuditLogFilters): Promise<AuditLogRow[]>;
  verifyChain(): Promise<VerifyResult>;
  resetDemo(): Promise<void>;
  tamperWithLastRow(): Promise<VerifyResult>;
  setFailureMode(enabled: boolean): Promise<void>;
}
