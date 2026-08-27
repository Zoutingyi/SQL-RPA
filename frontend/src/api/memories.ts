import { apiGet, apiPut, apiDelete, apiPost } from "./client";

export interface MemoryItem {
  id: string;
  content: string;
  memory_type: string;
  deprecated: boolean;
  created_at: string;
  updated_at: string;
}

export interface MemoriesResponse {
  count: number;
  memories: MemoryItem[];
}

export interface ProfileResponse {
  profile: {
    id: number;
    profile_data: Record<string, unknown>;
    version: number;
    generated_at: string;
  } | null;
  message?: string;
}

export function listMemories(): Promise<MemoriesResponse> {
  return apiGet("/api/memories");
}

export function updateMemory(id: string, body: { content?: string; deprecated?: boolean }): Promise<{ ok: boolean }> {
  return apiPut(`/api/memories/${id}`, body);
}

export function deleteMemory(id: string): Promise<{ ok: boolean }> {
  return apiDelete(`/api/memories/${id}`);
}

export function clearAllMemories(): Promise<{ ok: boolean; deleted_count: number }> {
  return apiDelete("/api/memories");
}

export function getProfile(): Promise<ProfileResponse> {
  return apiGet("/api/memories/profile");
}

export function generateProfile(): Promise<ProfileResponse> {
  return apiPost("/api/memories/profile/generate");
}
