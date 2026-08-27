export function hasPlatformAdminAccess(user: { is_platform_admin?: boolean; role?: string | null } | null): boolean {
  if (!user) return false;
  if (user.is_platform_admin !== undefined) return user.is_platform_admin;
  // Explicit legacy compatibility only. V1 never derives platform access from membership role.
  return user.role === "admin";
}

export function maskPhone(phone?: string | null): string {
  if (!phone) return "资料待完善";
  const compact = phone.replace(/\s+/g, "");
  if (compact.length <= 5) return `${compact.slice(0, 1)}***${compact.slice(-1)}`;
  return `${compact.slice(0, 3)}****${compact.slice(-4)}`;
}
