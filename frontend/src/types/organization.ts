export type OrganizationLevel = "company" | "department" | "group" | "individual";

export const ORGANIZATION_LEVEL_LABELS: Record<OrganizationLevel, string> = {
  company: "公司",
  department: "部门",
  group: "小组",
  individual: "个人",
};

export type OrganizationRole = "viewer" | "operator" | "approver" | "admin" | string;

export interface OrganizationUnit {
  id: string;
  name: string;
  level: OrganizationLevel;
  parent_id: string | null;
  company_id: string;
  path: string[];
  depth: number;
  active: boolean;
  sort_order: number;
  version?: number;
  member_count?: number;
  job_title?: string | null;
  children: OrganizationUnit[];
  /** True only while the backend still exposes the legacy Tenant contract. */
  compatibility_mode?: boolean;
}

export interface OrganizationMembership {
  id: string;
  user_id: string;
  organization_id: string;
  organization_level: OrganizationLevel;
  organization_name: string;
  organization_path: string[];
  company_id: string;
  role: OrganizationRole | null;
  permissions?: string[] | null;
  job_title?: string | null;
  is_primary: boolean;
  active: boolean;
  valid_from?: string | null;
  valid_to?: string | null;
  version?: number;
  compatibility_mode?: boolean;
}

export interface OrganizationContext {
  organization_id: string;
  membership_id: string;
  company_id: string;
  organization_level: OrganizationLevel;
  organization_path: string[];
  role: OrganizationRole | null;
  permissions: string[];
  context_token?: string;
  expires_at?: string;
  context_version?: string | number;
  compatibility_mode?: boolean;
}

export interface OrganizationTreeResponse {
  items: OrganizationUnit[];
  compatibility_mode?: boolean;
}

export interface MembershipListResponse {
  items: OrganizationMembership[];
  compatibility_mode?: boolean;
}
