import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { createTraceApi } from "../api/client";
import type { AuditLogFilters, ResumePayload, StartAuditRequest } from "../api/types";

export const api = createTraceApi();

export function useHealth() {
  return useQuery({
    queryKey: ["health"],
    queryFn: () => api.health(),
  });
}

export function useDataMode() {
  const mode = (import.meta.env.VITE_API_MODE as "mock" | "http" | undefined) ?? "mock";
  const { data } = useHealth();
  return { mode, healthStatus: data?.status ?? "unknown" };
}

export function useCompanies() {
  return useQuery({
    queryKey: ["companies"],
    queryFn: () => api.listCompanies(),
  });
}

export function useCompanySearch(query: string) {
  const trimmed = query.trim();
  return useQuery({
    queryKey: ["companies", "search", trimmed],
    queryFn: () => api.searchCompanies(trimmed),
    enabled: trimmed.length >= 2,
    staleTime: 60_000,
    retry: false,
  });
}

export function useFilings(cik: string | null | undefined, form?: string) {
  return useQuery({
    queryKey: ["filings", cik, form ?? ""],
    queryFn: () => api.listFilings(cik as string, form),
    enabled: Boolean(cik),
    staleTime: 60_000,
    retry: false,
  });
}

export function useFilingForms() {
  return useQuery({
    queryKey: ["filings", "forms"],
    queryFn: () => api.listFilingForms(),
    staleTime: 24 * 60 * 60 * 1000,
  });
}

export function useThreads() {
  return useQuery({
    queryKey: ["threads"],
    queryFn: () => api.listThreads(),
  });
}

export function useThreadStatus(threadId: string | null | undefined) {
  return useQuery({
    queryKey: ["thread", threadId],
    queryFn: () => api.getStatus(threadId as string),
    enabled: Boolean(threadId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      // Only poll while the backend is still working. A paused thread waits
      // for a human decision, and terminal states never change by themselves.
      const inFlight = ["STARTED", "FETCHED", "EXTRACTED", "VALIDATED"];
      return status && inFlight.includes(status) ? 2_000 : false;
    },
  });
}

export function useThreadTimeline(threadId: string | null | undefined) {
  return useQuery({
    queryKey: ["thread-timeline", threadId],
    queryFn: () => api.getTimeline(threadId as string),
    enabled: Boolean(threadId),
  });
}

export function useAuditLogs(filters?: AuditLogFilters) {
  return useQuery({
    queryKey: ["audit-logs", filters ?? {}],
    queryFn: () => api.listAuditLogs(filters),
  });
}

export function useVerifyChain() {
  return useQuery({
    queryKey: ["audit-logs", "verify"],
    queryFn: () => api.verifyChain(),
  });
}

export function useStartAudit() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: StartAuditRequest) => api.startAudit(request),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["threads"] });
      queryClient.invalidateQueries({ queryKey: ["audit-logs"] });
    },
  });
}

export function useResume() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ threadId, payload }: { threadId: string; payload: ResumePayload }) =>
      api.resume(threadId, payload),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["threads"] });
      queryClient.invalidateQueries({ queryKey: ["thread"] });
      queryClient.invalidateQueries({ queryKey: ["audit-logs"] });
    },
  });
}

export function useResetDemo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.resetDemo(),
    onSuccess: () => {
      queryClient.clear();
    },
  });
}

export function useTamperWithLastRow() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api.tamperWithLastRow(),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["audit-logs"] });
      queryClient.invalidateQueries({ queryKey: ["audit-logs", "verify"] });
    },
  });
}

export function useSetFailureMode() {
  return useMutation({
    mutationFn: (enabled: boolean) => api.setFailureMode(enabled),
  });
}
