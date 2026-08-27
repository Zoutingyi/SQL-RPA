import { apiGet, apiPost, apiPut } from "./client";

export interface Tenant { id: string; name: string; }
export interface TenantMember { user_id: string; role: "viewer" | "operator" | "approver" | "admin"; }

export const listTenants = (): Promise<{ items: Tenant[] }> => apiGet("/api/tenants");
export const createTenant = (name: string): Promise<Tenant> => apiPost("/api/tenants", { name });
export const listTenantMembers = (tenantId: string): Promise<{ items: TenantMember[] }> => apiGet(`/api/tenants/${tenantId}/members`);
export const setTenantMember = (tenantId: string, member: TenantMember): Promise<TenantMember & { tenant_id: string }> => apiPut(`/api/tenants/${tenantId}/members`, member);
