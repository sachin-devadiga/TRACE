/** Plain-language labels for the deterministic escalation types and severities. */

import type { EscalationType, Severity } from "@/api/types";

export interface EscalationCopy {
  label: string;
  explanation: string;
}

export const ESCALATION_COPY: Record<EscalationType, EscalationCopy> = {
  EXTRACTION_RISK: {
    label: "Extraction risk",
    explanation:
      "The extracted number itself may be wrong — for example the row could not be grounded in the filing, the column may be the wrong period, or the two independent readers disagreed.",
  },
  FINANCIAL_ANOMALY: {
    label: "Financial anomaly",
    explanation:
      "The number is mechanically plausible but moves unusually compared with the prior year, so a human should confirm the reported figure.",
  },
};

export const SEVERITY_COPY: Record<Severity, { label: string; explanation: string }> = {
  HIGH: {
    label: "High severity",
    explanation: "Resolve this before approving — the value or the period may be wrong.",
  },
  MEDIUM: {
    label: "Medium severity",
    explanation: "Check this against the filing before approving.",
  },
  LOW: {
    label: "Low severity",
    explanation: "Worth a quick look, but unlikely to change the reported figure.",
  },
};

export function escalationLabel(type: EscalationType): string {
  return ESCALATION_COPY[type]?.label ?? type.replace(/_/g, " ");
}

export function escalationExplanation(type: EscalationType): string {
  return ESCALATION_COPY[type]?.explanation ?? "";
}

export function severityLabel(severity: Severity): string {
  return SEVERITY_COPY[severity]?.label ?? severity;
}

export function severityExplanation(severity: Severity): string {
  return SEVERITY_COPY[severity]?.explanation ?? "";
}
