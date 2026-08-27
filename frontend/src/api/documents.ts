import { apiGet, apiPost, apiDelete } from "./client";
import type { Document } from "../types";

interface DocListResponse {
  items: Document[];
  total: number;
  page: number;
  page_size: number;
}

export function listDocuments(): Promise<DocListResponse> {
  return apiGet("/api/documents");
}

export function uploadDocument(file: File): Promise<Document> {
  const form = new FormData();
  form.append("file", file);
  return apiPost("/api/documents/upload", form);
}

export function deleteDocument(id: string): Promise<{ ok: boolean }> {
  return apiDelete(`/api/documents/${id}`);
}
