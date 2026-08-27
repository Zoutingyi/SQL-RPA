import { apiGet, apiPut, apiPost } from "./client";

export interface LLMSettings {
  provider: string;
  model: string;
  api_key: string;
  base_url: string;
}

export interface SettingsResponse {
  llm: LLMSettings;
  embedding: LLMSettings;
  web_search_enabled: boolean;
  rerank_enabled: boolean;
  retrieval_top_k: number;
  web_search_max_results: number;
  dedup_enabled: boolean;
  memory_enabled: boolean;
  ocr_enabled: boolean;
}

export interface SettingsUpdate {
  web_search_enabled?: boolean;
  rerank_enabled?: boolean;
  retrieval_top_k?: number;
  web_search_max_results?: number;
  dedup_enabled?: boolean;
  memory_enabled?: boolean;
  ocr_enabled?: boolean;
  llm_provider?: string;
  llm_model?: string;
  llm_api_key?: string;
  llm_base_url?: string;
  embedding_provider?: string;
  embedding_model?: string;
  embedding_api_key?: string;
  embedding_base_url?: string;
}

export function getSettings(): Promise<SettingsResponse> {
  return apiGet("/api/settings");
}

export function saveSettings(body: SettingsUpdate): Promise<{ status: string; updated: string[] }> {
  return apiPut("/api/settings", body);
}

export function testConnection(body: {
  provider: string;
  model: string;
  api_key: string;
  base_url: string;
  kind: string;
}): Promise<{ ok: boolean; latency_ms: number; detail: string }> {
  return apiPost("/api/settings/test-connection", body);
}
