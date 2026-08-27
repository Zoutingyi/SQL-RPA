import { apiGet } from "./client";

export interface Invoice {
  id: string; user_id: string | null; period_start: string; period_end: string;
  currency: string; subtotal_usd: number; total_usd: number; status: string;
  issued_at: string; due_at: string; paid_at: string | null;
}

export const listInvoices = (): Promise<{ items: Invoice[] }> => apiGet("/api/billing/invoices");
