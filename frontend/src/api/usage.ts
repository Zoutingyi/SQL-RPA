import { apiGet } from "./client";

export interface UsageSummaryItem {
  model: string;
  requests: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  cost_usd?: number;
  provider?: string;
}

export interface UsageSummary {
  days: number;
  items: UsageSummaryItem[];
}

export interface UsageOverview {
  days: number; total_cost_usd: number; total_tokens: number;
  by_model: UsageSummaryItem[];
  by_user: Array<{ user_id: string; requests: number; total_tokens: number; cost_usd: number }>;
  quotas: Array<{ user_id: string; monthly_token_limit: number; monthly_cost_limit_usd: number; enabled: boolean }>;
  tenant_scope: string;
}

export function getUsageSummary(days = 30): Promise<UsageSummary> {
  return apiGet(`/api/usage/summary?days=${days}`);
}

export function getUsageOverview(days = 30): Promise<UsageOverview> {
  return apiGet(`/api/usage/overview?days=${days}`);
}
