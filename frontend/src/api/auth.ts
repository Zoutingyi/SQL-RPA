import { apiGet, apiPatch, apiPost } from "./client";
import type { OrganizationLevel, OrganizationRole } from "../types/organization";
import { USER_MANAGEMENT_V1_ENABLED } from "../config/features";

export interface UserMembership {
  id: string;
  organization_id: string;
  company_id: string;
  organization_level: OrganizationLevel;
  organization_name?: string | null;
  organization_path: string[] | string;
  job_title?: string | null;
  role: OrganizationRole | null;
  permissions?: string[] | null;
  is_primary: boolean;
  active: boolean;
  valid_from?: string | null;
  valid_to?: string | null;
  version?: number;
}

export interface AuthUser {
  id: string;
  username: string;
  display_name?: string | null;
  phone?: string | null;
  is_active?: boolean;
  is_platform_admin?: boolean;
  must_change_password?: boolean;
  password_changed_at?: string | null;
  current_membership?: UserMembership | null;
  organization_memberships?: UserMembership[];
  /** @deprecated V1 authorization comes from is_platform_admin or a membership. */
  role?: "viewer" | "operator" | "approver" | "admin" | string | null;
  auth_type?: string;
  /** @deprecated Compatibility field until the department context API is enabled. */
  tenant_id?: string;
  /** @deprecated Compatibility fields returned by the current /me endpoint. */
  company_id?: string | null;
  organization_id?: string | null;
  membership_id?: string | null;
  organization_level?: OrganizationLevel | null;
}

export class UserContractError extends Error {
  readonly missingFields: string[];
  constructor(missingFields: string[]) {
    super(`用户身份契约缺少字段：${missingFields.join(", ")}`);
    this.name = "UserContractError";
    this.missingFields = missingFields;
  }
}

export function assertV1AuthUser(user: AuthUser): AuthUser {
  const required = ["display_name", "phone", "is_active", "is_platform_admin", "must_change_password", "password_changed_at", "current_membership", "organization_memberships"] as const;
  const missing = required.filter((field) => !Object.prototype.hasOwnProperty.call(user, field));
  if (missing.length) throw new UserContractError([...missing]);
  if (typeof user.is_active !== "boolean" || typeof user.is_platform_admin !== "boolean" || typeof user.must_change_password !== "boolean" || !Array.isArray(user.organization_memberships)) {
    throw new UserContractError(["invalid_identity_field_type"]);
  }
  return user;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  /** @deprecated Compatibility field until the department context API is enabled. */
  tenant_id?: string;
  /** @deprecated Compatibility list used only by the legacy adapter. */
  tenants?: Array<{ id: string; name: string; role: string }>;
  organization: {
    company_id: string;
    organization_id: string;
    membership_id: string;
    organization_level: OrganizationLevel;
    organization_path: string[] | string;
    role: OrganizationRole | null;
    permissions?: string[] | null;
    context_token?: string;
    context_version?: string | number;
    expires_at?: string;
    is_primary: boolean;
  } | null;
  organization_memberships?: Array<{
    company_id: string;
    organization_id: string;
    membership_id: string;
    organization_level: OrganizationLevel;
    organization_path: string[] | string;
    organization_name?: string;
    role: OrganizationRole | null;
    permissions?: string[] | null;
    job_title?: string | null;
    is_primary: boolean;
    active?: boolean;
    valid_from?: string | null;
    valid_to?: string | null;
    version?: number;
  }>;
  user: AuthUser;
}

export function login(username: string, password: string): Promise<LoginResponse> {
  return apiPost("/api/auth/login", { username, password });
}

export async function getMe(): Promise<AuthUser> {
  const user = await apiGet<AuthUser>("/api/auth/me");
  return USER_MANAGEMENT_V1_ENABLED ? assertV1AuthUser(user) : user;
}

export interface ManagedUser extends AuthUser {
  is_active: boolean;
  created_at?: string;
}

export interface UserListResponse {
  items: ManagedUser[];
  page?: number;
  page_size?: number;
  total?: number;
}

export interface CreateUserInput {
  username: string;
  display_name: string;
  organization_id: string;
  job_title: string;
  phone: string;
  password?: string | null;
  role: OrganizationRole | null;
}

export interface CreateUserResponse {
  user: ManagedUser;
  primary_membership: UserMembership;
  used_default_password: boolean;
  must_change_password: boolean;
}

export interface ChangePasswordInput {
  current_password: string;
  new_password: string;
  confirm_password: string;
}

export async function listUsers(params?: { page?: number; page_size?: number; query?: string }): Promise<UserListResponse> {
  const search = new URLSearchParams();
  if (params?.page) search.set("page", String(params.page));
  if (params?.page_size) search.set("page_size", String(params.page_size));
  if (params?.query) search.set("query", params.query);
  const response = await apiGet<UserListResponse>(`/api/auth/users${search.size ? `?${search}` : ""}`);
  if (USER_MANAGEMENT_V1_ENABLED) {
    const invalid = response.items.some((user) => !Object.prototype.hasOwnProperty.call(user, "current_membership"));
    if (invalid) throw new UserContractError(["items[].current_membership"]);
  }
  return response;
}

export function createUser(input: CreateUserInput, idempotencyKey: string): Promise<CreateUserResponse> {
  return apiPost("/api/auth/users", input, { "Idempotency-Key": idempotencyKey });
}

export function updateUser(
  userId: string,
  input: { display_name?: string; phone?: string | null; is_active?: boolean; version?: number }
): Promise<ManagedUser> {
  return apiPatch(`/api/auth/users/${userId}`, input);
}

export interface UserDetailResponse {
  user: ManagedUser;
  organization_memberships: UserMembership[];
}

export function getUser(userId: string): Promise<UserDetailResponse> {
  return apiGet(`/api/auth/users/${userId}`);
}

export function updateUserMembership(
  userId: string,
  membershipId: string,
  input: { organization_id?: string; job_title?: string | null; role?: OrganizationRole | null; permissions?: string[] | null; is_primary?: boolean; version: number },
): Promise<UserMembership> {
  return apiPatch(`/api/auth/users/${userId}/memberships/${membershipId}`, input);
}

export function disableUserMembership(userId: string, membershipId: string, version: number): Promise<UserMembership> {
  return apiPost(`/api/auth/users/${userId}/memberships/${membershipId}/disable`, { version });
}

export function changePassword(input: ChangePasswordInput): Promise<void> {
  return apiPost("/api/auth/change-password", input);
}
