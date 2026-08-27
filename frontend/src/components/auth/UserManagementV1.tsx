import { useEffect, useRef, useState } from "react";
import { ApiError, formatApiError } from "../../api/client";
import { createUser, listUsers, UserContractError, type CreateUserResponse, type ManagedUser } from "../../api/auth";
import { useOrganizationStore } from "../../stores/organizationStore";
import { useToastStore } from "../../stores/toastStore";
import type { OrganizationUnit } from "../../types/organization";
import { maskPhone } from "./userAccess";

const ROLES = [
  { value: "", label: "未分配" },
  { value: "viewer", label: "查看者" },
  { value: "operator", label: "操作者" },
  { value: "approver", label: "审批人" },
  { value: "admin", label: "部门管理员" },
];

function flattenActiveUnits(units: OrganizationUnit[]): OrganizationUnit[] {
  return units.flatMap((unit) => [unit, ...flattenActiveUnits(unit.children)]).filter((unit) => unit.active);
}

function newIdempotencyKey(): string {
  return `user-create-${crypto.randomUUID()}`;
}

export function UserManagementV1() {
  const tree = useOrganizationStore((state) => state.tree);
  const loadOrganization = useOrganizationStore((state) => state.loadOrganization);
  const addToast = useToastStore((state) => state.addToast);
  const [users, setUsers] = useState<ManagedUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [createdResult, setCreatedResult] = useState<CreateUserResponse | null>(null);
  const [contractError, setContractError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string[]>>({});
  const [form, setForm] = useState({
    username: "", display_name: "", organization_id: "", job_title: "", phone: "", password: "", role: "",
  });
  const idempotencyKey = useRef(newIdempotencyKey());

  const units = flattenActiveUnits(tree).filter((unit) => unit.level !== "company");

  const load = async () => {
    setLoading(true);
    try {
      const response = await listUsers({ page: 1, page_size: 20 });
      setUsers(response.items);
      setContractError(null);
    } catch (error) {
      if (error instanceof UserContractError) setContractError(error.message);
      addToast({ type: "error", message: formatApiError(error, "加载用户失败") });
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!tree.length) void loadOrganization();
    void load();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const changeField = (field: keyof typeof form, value: string) => {
    setForm((current) => ({ ...current, [field]: value }));
    setFieldErrors((current) => {
      const next = { ...current };
      delete next[field];
      return next;
    });
    idempotencyKey.current = newIdempotencyKey();
  };

  const validate = (): Record<string, string[]> => {
    const errors: Record<string, string[]> = {};
    const username = form.username.trim();
    if (!/^[A-Za-z0-9_.-]{3,64}$/.test(username)) errors.username = ["账号需为 3～64 位字母、数字、点、下划线或连字符"];
    if (!form.display_name.trim()) errors.display_name = ["请输入用户姓名"];
    if (!form.organization_id) errors.organization_id = ["请选择用户部门"];
    if (!form.job_title.trim()) errors.job_title = ["请输入职位"];
    if (!/^\+?[0-9 -]{6,32}$/.test(form.phone.trim())) errors.phone = ["请输入 6～32 位有效联系电话"];
    if (form.password && (form.password.length < 10 || !/[A-Za-z]/.test(form.password) || !/\d/.test(form.password))) {
      errors.password = ["显式密码至少 10 位，且必须同时包含字母和数字"];
    }
    return errors;
  };

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (creating) return;
    const errors = validate();
    if (Object.keys(errors).length) {
      setFieldErrors(errors);
      document.getElementById(`create-user-${Object.keys(errors)[0]}`)?.focus();
      return;
    }
    setCreating(true);
    setCreatedResult(null);
    try {
      const result = await createUser({
        username: form.username.trim(),
        display_name: form.display_name.trim(),
        organization_id: form.organization_id,
        job_title: form.job_title.trim(),
        phone: form.phone.trim(),
        password: form.password || null,
        role: form.role || null,
      }, idempotencyKey.current);
      setCreatedResult(result);
      setForm({ username: "", display_name: "", organization_id: "", job_title: "", phone: "", password: "", role: "" });
      idempotencyKey.current = newIdempotencyKey();
      addToast({ type: "success", message: "用户已创建" });
      await load();
    } catch (error) {
      if (form.password) idempotencyKey.current = newIdempotencyKey();
      setForm((current) => ({ ...current, password: "" }));
      if (error instanceof ApiError && error.fieldErrors) setFieldErrors(error.fieldErrors);
      addToast({ type: "error", message: formatApiError(error, "创建用户失败") });
    } finally {
      setCreating(false);
    }
  };

  const field = (name: keyof typeof form, label: string, input: React.ReactNode) => (
    <div className="settings-field">
      <label htmlFor={`create-user-${name}`}>{label}</label>
      {input}
      {fieldErrors[name]?.map((message) => <small key={message} className="field-error" id={`create-user-${name}-error`}>{message}</small>)}
    </div>
  );

  return (
    <div className="user-page">
      <div className="user-page-header"><div><h2>用户管理</h2><p>账号资料与部门任职分开管理。</p></div></div>
      <form className="user-create-card user-create-v1" onSubmit={submit} noValidate>
        {field("username", "登录账号", <input id="create-user-username" value={form.username} onChange={(event) => changeField("username", event.target.value)} aria-invalid={Boolean(fieldErrors.username)} aria-describedby={fieldErrors.username ? "create-user-username-error" : undefined} />)}
        {field("display_name", "用户姓名", <input id="create-user-display_name" value={form.display_name} onChange={(event) => changeField("display_name", event.target.value)} aria-invalid={Boolean(fieldErrors.display_name)} aria-describedby={fieldErrors.display_name ? "create-user-display_name-error" : undefined} />)}
        {field("organization_id", "用户部门", <select id="create-user-organization_id" value={form.organization_id} onChange={(event) => changeField("organization_id", event.target.value)} aria-invalid={Boolean(fieldErrors.organization_id)} aria-describedby={fieldErrors.organization_id ? "create-user-organization_id-error" : undefined}><option value="">请选择</option>{units.map((unit) => <option key={unit.id} value={unit.id}>{unit.path.join(" / ")}</option>)}</select>)}
        {field("job_title", "职位", <input id="create-user-job_title" value={form.job_title} onChange={(event) => changeField("job_title", event.target.value)} aria-invalid={Boolean(fieldErrors.job_title)} aria-describedby={fieldErrors.job_title ? "create-user-job_title-error" : undefined} />)}
        {field("phone", "联系电话", <input id="create-user-phone" inputMode="tel" value={form.phone} onChange={(event) => changeField("phone", event.target.value)} aria-invalid={Boolean(fieldErrors.phone)} aria-describedby={fieldErrors.phone ? "create-user-phone-error" : undefined} />)}
        {field("password", "登录密码（选填）", <><input id="create-user-password" type="password" autoComplete="new-password" value={form.password} onChange={(event) => changeField("password", event.target.value)} aria-invalid={Boolean(fieldErrors.password)} aria-describedby={fieldErrors.password ? "create-user-password-error" : undefined} /><small>不填才使用默认密码 111111；显式密码至少 10 位并同时包含字母和数字</small></>)}
        {field("role", "用户权限（选填）", <><select id="create-user-role" value={form.role} onChange={(event) => changeField("role", event.target.value)}>{ROLES.map((role) => <option key={role.value} value={role.value}>{role.label}</option>)}</select><small>未分配权限时，仅可登录、查看个人设置和修改密码</small></>)}
        <button className="confirm-primary" disabled={creating}>{creating ? "创建中..." : "创建用户"}</button>
      </form>

      {createdResult && <section className="creation-result" aria-live="polite"><strong>创建完成：{createdResult.user.display_name || createdResult.user.username}</strong><span>账号 {createdResult.user.username} · {createdResult.primary_membership.organization_name || "部门名称待后端返回"} · {createdResult.primary_membership.job_title} · 权限 {createdResult.primary_membership.role || "未分配"}</span><small>{createdResult.used_default_password ? "已使用默认密码，用户首次登录必须修改。" : "已使用管理员设置的密码。"} 本页面不会回显密码。</small></section>}

      {contractError ? <div className="form-error" role="alert">用户列表部署契约错误：后端未返回 current_membership，已停止渲染任职数据。</div> : loading ? <div className="log-loading">加载中...</div> : (
        <div className="doc-table-wrap"><table className="doc-table"><thead><tr><th>登录账号</th><th>姓名</th><th>电话</th><th>主职部门</th><th>职位</th><th>权限</th><th>状态</th></tr></thead><tbody>
          {users.map((user) => <tr key={user.id}><td>{user.username}</td><td>{user.display_name || "资料待完善"}</td><td>{maskPhone(user.phone)}</td><td>{user.current_membership ? (Array.isArray(user.current_membership.organization_path) ? user.current_membership.organization_path.join(" / ") : user.current_membership.organization_path) : "无有效主职"}</td><td>{user.current_membership?.job_title || "—"}</td><td>{user.current_membership?.role || "未分配"}</td><td>{user.is_active ? "启用" : "停用"}</td></tr>)}
        </tbody></table></div>
      )}
    </div>
  );
}
