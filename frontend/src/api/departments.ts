import { apiGet, apiPost, apiPut } from "./client";
import type {
  MembershipListResponse,
  OrganizationContext,
  OrganizationLevel,
  OrganizationMembership,
  OrganizationTreeResponse,
  OrganizationUnit,
} from "../types/organization";

type RawOrganizationUnit = Omit<Partial<OrganizationUnit>, "path" | "children"> & {
  id: string;
  name: string;
  path?: string[] | string;
  children?: RawOrganizationUnit[];
};

type RawOrganizationMembership = Omit<Partial<OrganizationMembership>, "organization_path"> & {
  id: string;
  user_id: string;
  organization_id: string;
  organization_level: OrganizationLevel;
  company_id: string;
  role: string | null;
  path?: string[] | string;
  organization_path?: string[] | string;
};

export interface DepartmentMember {
  id: string;
  user_id: string;
  organization_id: string;
  organization_name?: string;
  organization_path?: string[];
  role: string | null;
  job_title?: string | null;
  is_primary: boolean;
  active: boolean;
  valid_from?: string | null;
  valid_to?: string | null;
  version?: number;
}

export interface DepartmentMemberInput {
  user_id: string;
  role?: string | null;
  job_title?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
}

export interface DepartmentMemberUpdateInput {
  role: string | null;
  job_title: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  version: number;
}

function normalizePath(path: string[] | string | undefined): string[] {
  if (!path) return [];
  return Array.isArray(path) ? path : path.split("/").filter(Boolean);
}

function flattenUnits(units: RawOrganizationUnit[]): RawOrganizationUnit[] {
  return units.flatMap((unit) => [unit, ...flattenUnits(unit.children || [])]);
}

function normalizeTree(units: RawOrganizationUnit[]): OrganizationUnit[] {
  const nodes = new Map<string, OrganizationUnit>();
  for (const unit of flattenUnits(units)) {
    const level = unit.level || "department";
    nodes.set(unit.id, {
      id: unit.id,
      name: unit.name,
      level,
      parent_id: unit.parent_id ?? null,
      company_id: unit.company_id || (level === "company" ? unit.id : ""),
      path: normalizePath(unit.path),
      depth: unit.depth || 1,
      active: unit.active ?? true,
      sort_order: unit.sort_order ?? 0,
      version: unit.version,
      member_count: unit.member_count,
      job_title: unit.job_title,
      children: [],
    });
  }

  const roots: OrganizationUnit[] = [];
  for (const node of nodes.values()) {
    const parent = node.parent_id ? nodes.get(node.parent_id) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node);
  }

  const applyDisplayPath = (node: OrganizationUnit, parentPath: string[]) => {
    node.path = [...parentPath, node.name];
    node.children.sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, "zh-CN"));
    node.children.forEach((child) => applyDisplayPath(child, node.path));
  };
  roots.sort((a, b) => a.sort_order - b.sort_order || a.name.localeCompare(b.name, "zh-CN"));
  roots.forEach((root) => applyDisplayPath(root, []));
  return roots;
}

function normalizeMembership(membership: RawOrganizationMembership): OrganizationMembership {
  const organizationPath = normalizePath(membership.organization_path || membership.path);
  return {
    id: membership.id,
    user_id: membership.user_id,
    organization_id: membership.organization_id,
    organization_level: membership.organization_level,
    organization_name: membership.organization_name || organizationPath.at(-1) || "",
    organization_path: organizationPath,
    company_id: membership.company_id,
    role: membership.role,
    job_title: membership.job_title,
    is_primary: membership.is_primary ?? false,
    active: membership.active ?? true,
    valid_from: membership.valid_from,
    valid_to: membership.valid_to,
    version: membership.version,
  };
}

function normalizeDepartmentMember(membership: RawOrganizationMembership): DepartmentMember {
  const path = normalizePath(membership.organization_path || membership.path);
  return {
    id: membership.id,
    user_id: membership.user_id,
    organization_id: membership.organization_id,
    organization_name: membership.organization_name || path.at(-1),
    organization_path: path,
    role: membership.role ?? null,
    job_title: membership.job_title,
    is_primary: membership.is_primary ?? false,
    active: membership.active ?? true,
    valid_from: membership.valid_from,
    valid_to: membership.valid_to,
    version: membership.version,
  };
}

export async function getDepartmentTree(): Promise<OrganizationTreeResponse> {
  const response = await apiGet<{ items?: RawOrganizationUnit[]; tree?: RawOrganizationUnit[] }>("/api/departments/tree");
  return { items: normalizeTree(response.items || response.tree || []) };
}

export async function getMyMemberships(): Promise<MembershipListResponse> {
  const response = await apiGet<{ items?: RawOrganizationMembership[] }>("/api/departments/memberships/me");
  return { items: (response.items || []).map(normalizeMembership) };
}

export async function switchDepartmentContext(membership: OrganizationMembership): Promise<OrganizationContext> {
  const context = await apiPost<Omit<OrganizationContext, "organization_path"> & {
    organization_path: string[] | string;
  }>("/api/departments/context/switch", { membership_id: membership.id });
  return { ...context, organization_path: normalizePath(context.organization_path) };
}

export async function createDepartment(input: {
  name: string;
  level: OrganizationLevel;
  parent_id?: string | null;
}): Promise<OrganizationUnit> {
  const unit = await apiPost<RawOrganizationUnit>("/api/departments", input);
  return normalizeTree([unit])[0];
}

export const updateDepartment = async (
  id: string,
  input: { name: string; sort_order: number; version: number },
): Promise<OrganizationUnit> => {
  const unit = await apiPut<RawOrganizationUnit>(`/api/departments/${id}`, input);
  return normalizeTree([unit])[0];
};

export const disableDepartment = (id: string): Promise<{ id: string; active: false }> =>
  apiPost(`/api/departments/${id}/disable`);

export const moveDepartment = async (
  id: string,
  parentId: string,
  version: number,
): Promise<OrganizationUnit> => {
  const unit = await apiPost<RawOrganizationUnit>(`/api/departments/${id}/move`, {
    parent_id: parentId,
    version,
  });
  return normalizeTree([unit])[0];
};

export async function getDepartmentMembers(organizationId: string): Promise<{ items: DepartmentMember[] }> {
  const response = await apiGet<{ items?: RawOrganizationMembership[] }>(
    `/api/departments/${organizationId}/memberships`,
  );
  return { items: (response.items || []).map(normalizeDepartmentMember) };
}

export async function saveDepartmentMember(
  organizationId: string,
  member: DepartmentMemberInput,
): Promise<DepartmentMember> {
  return normalizeDepartmentMember(await apiPost<RawOrganizationMembership>(
    `/api/departments/${organizationId}/memberships`, member,
  ));
}

export async function updateDepartmentMember(
  membershipId: string,
  member: DepartmentMemberUpdateInput,
): Promise<DepartmentMember> {
  return normalizeDepartmentMember(await apiPut<RawOrganizationMembership>(
    `/api/departments/memberships/${membershipId}`, member,
  ));
}

export function setPrimaryDepartmentMember(
  membershipId: string,
  reason: string,
): Promise<{ old_membership_id: string | null; new_membership_id: string }> {
  return apiPost(`/api/departments/memberships/${membershipId}/set-primary`, { reason });
}

export function disableDepartmentMember(
  membershipId: string,
  reason: string,
  replacementPrimaryId?: string | null,
): Promise<{ id: string; active: false }> {
  return apiPost(`/api/departments/memberships/${membershipId}/disable`, {
    replacement_primary_id: replacementPrimaryId || null,
    reason,
  });
}
