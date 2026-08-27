import { useCallback, useEffect, useMemo, useState } from "react";
import {
  createDepartment,
  disableDepartment,
  disableDepartmentMember,
  getDepartmentMembers,
  moveDepartment,
  saveDepartmentMember,
  setPrimaryDepartmentMember,
  updateDepartment,
  updateDepartmentMember,
  type DepartmentMember,
} from "../../api/departments";
import { formatApiError } from "../../api/client";
import { useOrganizationStore } from "../../stores/organizationStore";
import { useAuthStore } from "../../stores/authStore";
import { hasPlatformAdminAccess } from "../auth/userAccess";
import { useToastStore } from "../../stores/toastStore";
import { useConfirm } from "../shared/confirmContext";
import { ORGANIZATION_LEVEL_LABELS, type OrganizationLevel, type OrganizationUnit } from "../../types/organization";
import { OrganizationTree } from "./OrganizationTree";

const NEXT_LEVEL: Partial<Record<OrganizationLevel, OrganizationLevel>> = {
  company: "department",
  department: "group",
  group: "individual",
};

const PARENT_LEVEL: Partial<Record<OrganizationLevel, OrganizationLevel>> = {
  department: "company",
  group: "department",
  individual: "group",
};

function collectOrganizationIds(items: OrganizationUnit[]): string[] {
  return items.flatMap((item) => [item.id, ...collectOrganizationIds(item.children)]);
}

function flattenOrganizations(items: OrganizationUnit[]): OrganizationUnit[] {
  return items.flatMap((item) => [item, ...flattenOrganizations(item.children)]);
}

export function DepartmentManagementPage() {
  const {
    tree, memberships, currentContext, loading, switching, error,
    loadOrganization, switchMembership,
  } = useOrganizationStore();
  const user = useAuthStore((state) => state.user);
  const loadMe = useAuthStore((state) => state.loadMe);
  const addToast = useToastStore((state) => state.addToast);
  const confirm = useConfirm();
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [selected, setSelected] = useState<OrganizationUnit | null>(null);
  const [members, setMembers] = useState<DepartmentMember[]>([]);
  const [memberError, setMemberError] = useState("");
  const [newName, setNewName] = useState("");
  const [userId, setUserId] = useState("");
  const [jobTitle, setJobTitle] = useState("");
  const [role, setRole] = useState("viewer");
  const [editName, setEditName] = useState("");
  const [editSortOrder, setEditSortOrder] = useState(0);
  const [moveParentId, setMoveParentId] = useState("");
  const [editingMemberId, setEditingMemberId] = useState<string | null>(null);
  const [editingMemberRole, setEditingMemberRole] = useState("viewer");
  const [editingMemberJobTitle, setEditingMemberJobTitle] = useState("");
  const [actionReason, setActionReason] = useState("");

  const platformAdmin = hasPlatformAdminAccess(user);

  useEffect(() => {
    if (!tree.length && user && !(platformAdmin && !currentContext)) {
      void loadOrganization(typeof user.role === "string" ? user.role : undefined);
    }
  }, [tree.length, user, loadOrganization, platformAdmin, currentContext]);
  useEffect(() => {
    if (!selected && currentContext) {
      const find = (items: OrganizationUnit[]): OrganizationUnit | undefined => {
        for (const item of items) {
          if (item.id === currentContext.organization_id) return item;
          const nested = find(item.children);
          if (nested) return nested;
        }
      };
      setSelected(find(tree) || null);
    }
  }, [tree, currentContext, selected]);

  useEffect(() => {
    setEditName(selected?.name || "");
    setEditSortOrder(selected?.sort_order || 0);
    setMoveParentId("");
  }, [selected]);

  const canManage = platformAdmin
    || currentContext?.role === "admin"
    || currentContext?.permissions.includes("organization.manage") === true;
  const selectedIsCurrent = selected?.id === currentContext?.organization_id;
  const nextLevel = selected ? NEXT_LEVEL[selected.level] : undefined;
  const allOrganizations = useMemo(() => flattenOrganizations(tree), [tree]);
  const descendantIds = useMemo(
    () => new Set(selected ? collectOrganizationIds(selected.children) : []),
    [selected],
  );
  const moveTargets = selected ? allOrganizations.filter((item) => (
    item.active
    && item.company_id === selected.company_id
    && item.level === PARENT_LEVEL[selected.level]
    && item.id !== selected.id
    && !descendantIds.has(item.id)
  )) : [];

  const loadMembers = useCallback(async () => {
    if (!selected || !canManage) { setMembers([]); return; }
    try {
      setMembers((await getDepartmentMembers(selected.id)).items);
      setMemberError("");
    } catch (caught) {
      setMemberError(formatApiError(caught, "加载部门成员失败"));
    }
  }, [selected, canManage]);
  useEffect(() => { void loadMembers(); }, [loadMembers]);

  const currentMembership = useMemo(
    () => memberships.find((item) => item.id === currentContext?.membership_id),
    [memberships, currentContext?.membership_id],
  );

  const handleSwitch = async (membershipId: string) => {
    const membership = memberships.find((item) => item.id === membershipId);
    if (!membership || membership.id === currentContext?.membership_id) return;
    const ok = await confirm({
      title: `切换到${membership.is_primary ? "主职" : "兼职"}部门`,
      message: `${membership.organization_path.join(" / ")}\n岗位：${membership.job_title || "未设置"}\n角色：${membership.role}\n切换后将清空当前部门的会话、文档、数据库、审核、通知、用量和账单页面状态。`,
      confirmLabel: "确认切换",
    });
    if (!ok) return;
    if (await switchMembership(membershipId)) {
      await loadMe();
      addToast({ type: "success", message: "部门身份已切换" });
    }
  };

  const reloadOrganization = async () => {
    setSelected(null);
    await loadOrganization(typeof user?.role === "string" ? user.role : undefined);
  };

  return (
    <div className="page-content department-page">
      <div className="usage-header">
        <div>
          <h2>部门管理</h2>
          <p className="organization-breadcrumb" aria-label="当前组织路径">
            {currentContext?.organization_path.join(" / ") || "尚未选择有效部门"}
          </p>
        </div>
        <button className="page-btn" onClick={() => void loadOrganization(typeof user?.role === "string" ? user.role : undefined)} disabled={loading || switching}>
          {loading ? "加载中..." : "刷新组织"}
        </button>
      </div>

      {error && <div className="rollback-error" role="alert">{error}</div>}

      {platformAdmin && !currentContext && <section className="review-section">
        <div className="review-section-title">平台管理：创建公司</div>
        <p className="doc-meta">平台管理员当前没有部门身份，只能创建公司节点；进入具体公司管理前仍需后端分配并验证任职。</p>
        <div className="review-state-actions">
          <input className="review-input" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder="公司名称" />
          <button className="page-btn" disabled={!newName.trim()} onClick={async () => {
            try {
              await createDepartment({ name: newName.trim(), level: "company", parent_id: null });
              setNewName("");
              addToast({ type: "success", message: "公司已创建，请继续配置管理员任职" });
            } catch (caught) { setMemberError(formatApiError(caught, "创建公司失败")); }
          }}>创建公司</button>
        </div>
      </section>}

      {currentContext && <div className="department-layout">
        <section className="department-tree-panel">
          <div className="department-panel-title">
            <strong>组织结构</strong>
            <button className="page-btn" onClick={() => setExpanded(new Set(collectOrganizationIds(tree)))}>展开</button>
          </div>
          <input className="review-input department-search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索公司、部门、小组或个人" aria-label="搜索部门组织树" />
          <OrganizationTree
            items={tree}
            query={query}
            expanded={expanded}
            currentOrganizationId={currentContext?.organization_id}
            selectedOrganizationId={selected?.id}
            onToggle={(id) => setExpanded((current) => { const next = new Set(current); if (next.has(id)) next.delete(id); else next.add(id); return next; })}
            onSelect={setSelected}
          />
        </section>

        <section className="department-detail-panel">
          {selected ? <>
            <div className="department-node-header">
              <div><span className={`organization-level level-${selected.level}`}>{ORGANIZATION_LEVEL_LABELS[selected.level]}</span><h3>{selected.name}</h3></div>
              <span className={`status-badge ${selected.active ? "ready" : "failed"}`}>{selected.active ? "启用" : "停用"}</span>
            </div>
            <p className="organization-breadcrumb">{selected.path.join(" / ")}</p>
            <div className="department-meta-grid">
              <span>层级<strong>{selected.depth}</strong></span>
              <span>成员<strong>{selected.member_count ?? "—"}</strong></span>
              <span>当前部门<strong>{selectedIsCurrent ? "是" : "否"}</strong></span>
            </div>

            {canManage && <div className="review-section">
              <div className="review-section-title">节点维护</div>
              <div className="review-state-actions">
                <input className="review-input" value={editName} onChange={(event) => setEditName(event.target.value)} aria-label="组织节点名称" />
                <input className="review-input" type="number" value={editSortOrder} onChange={(event) => setEditSortOrder(Number(event.target.value))} aria-label="组织节点排序" />
                <button className="page-btn" disabled={!editName.trim() || selected.version === undefined} onClick={async () => {
                  if (selected.version === undefined) return;
                  try {
                    await updateDepartment(selected.id, { name: editName.trim(), sort_order: editSortOrder, version: selected.version });
                    await reloadOrganization();
                    addToast({ type: "success", message: "组织节点已更新" });
                  } catch (caught) { setMemberError(formatApiError(caught, "更新组织节点失败")); }
                }}>保存节点</button>
              </div>
              {selected.version === undefined && <p className="doc-meta">后端未返回并发版本，编辑和移动操作已禁用。</p>}
              {moveTargets.length > 0 && <div className="review-state-actions">
                <select className="log-filter-select" value={moveParentId} onChange={(event) => setMoveParentId(event.target.value)} aria-label="目标上级组织">
                  <option value="">选择新的上级节点</option>
                  {moveTargets.map((item) => <option key={item.id} value={item.id}>{item.path.join(" / ")}</option>)}
                </select>
                <button className="page-btn" disabled={!moveParentId || selected.version === undefined} onClick={async () => {
                  if (selected.version === undefined) return;
                  const target = moveTargets.find((item) => item.id === moveParentId);
                  if (!target) return;
                  const approved = await confirm({
                    title: "移动组织节点",
                    message: `原路径：${selected.path.join(" / ")}\n目标上级：${target.path.join(" / ")}\n移动后路径、下级节点及其业务作用域将由后端重新计算。`,
                    confirmLabel: "确认移动",
                    variant: "danger",
                  });
                  if (!approved) return;
                  try {
                    await moveDepartment(selected.id, target.id, selected.version);
                    await reloadOrganization();
                    addToast({ type: "success", message: "组织节点已移动" });
                  } catch (caught) { setMemberError(formatApiError(caught, "移动组织节点失败")); }
                }}>移动</button>
              </div>}
              <button className="page-btn" disabled={selectedIsCurrent || selected.children.length > 0 || (selected.member_count || 0) > 0} onClick={async () => {
                const approved = await confirm({
                  title: "停用组织节点",
                  message: `将停用：${selected.path.join(" / ")}\n当前可见下级：${selected.children.length} 个，活动成员：${selected.member_count ?? "未知"}。后端会再次校验完整影响范围，条件不满足时不会停用。`,
                  confirmLabel: "确认停用",
                  variant: "danger",
                });
                if (!approved) return;
                try {
                  await disableDepartment(selected.id);
                  await reloadOrganization();
                  addToast({ type: "success", message: "组织节点已停用" });
                } catch (caught) { setMemberError(formatApiError(caught, "停用组织节点失败")); }
              }}>停用节点</button>
            </div>}

            {canManage && nextLevel && <div className="review-section">
              <div className="review-section-title">创建{ORGANIZATION_LEVEL_LABELS[nextLevel]}</div>
              <div className="review-state-actions">
                <input className="review-input" value={newName} onChange={(event) => setNewName(event.target.value)} placeholder={`${ORGANIZATION_LEVEL_LABELS[nextLevel]}名称`} />
                <button className="page-btn" disabled={!newName.trim()} onClick={async () => {
                  try {
                    await createDepartment({ name: newName.trim(), level: nextLevel, parent_id: selected.id });
                    setNewName("");
                    await loadOrganization(typeof user?.role === "string" ? user.role : undefined);
                    addToast({ type: "success", message: `${ORGANIZATION_LEVEL_LABELS[nextLevel]}已创建` });
                  } catch (caught) { setMemberError(formatApiError(caught, "创建部门节点失败")); }
                }}>创建</button>
              </div>
            </div>}

            {canManage && <div className="review-section">
              <div className="review-section-title">部门成员</div>
              {memberError && <div className="rollback-error" role="alert">{memberError}</div>}
              <div className="review-state-actions">
                <input className="review-input" value={userId} onChange={(event) => setUserId(event.target.value)} placeholder="用户 ID" />
                <input className="review-input" value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} placeholder="岗位" />
                <select className="log-filter-select" value={role} onChange={(event) => setRole(event.target.value)}>
                  {(["viewer", "operator", "approver", "admin"] as const).map((item) => <option value={item} key={item}>{item}</option>)}
                </select>
                <button className="page-btn" disabled={!userId.trim() || !jobTitle.trim()} onClick={async () => {
                  try {
                    await saveDepartmentMember(selected.id, { user_id: userId.trim(), role, job_title: jobTitle.trim() });
                    setUserId("");
                    setJobTitle("");
                    await loadMembers();
                    addToast({ type: "success", message: "部门成员已保存" });
                  } catch (caught) { setMemberError(formatApiError(caught, "保存部门成员失败")); }
                }}>保存成员</button>
              </div>
              <p className="doc-meta">同一层级的首条有效任职可能由后端自动设为主职；后续任职默认作为兼职。主职调整必须填写原因。</p>
              <input className="review-input" value={actionReason} onChange={(event) => setActionReason(event.target.value)} placeholder="主职调整或停用原因" aria-label="任职调整原因" />
              <div className="doc-table-wrap"><table className="doc-table"><thead><tr><th>用户 ID</th><th>岗位</th><th>部门角色</th><th>任职</th><th>操作</th></tr></thead><tbody>{members.length ? members.map((member) => <tr key={member.id}>
                <td className="doc-meta">{member.user_id}</td>
                <td>{editingMemberId === member.id ? <input className="review-input" value={editingMemberJobTitle} onChange={(event) => setEditingMemberJobTitle(event.target.value)} aria-label={`${member.user_id}岗位`} /> : (member.job_title || "岗位未设置")}</td>
                <td>{editingMemberId === member.id ? <select className="log-filter-select" value={editingMemberRole} onChange={(event) => setEditingMemberRole(event.target.value)}>{(["viewer", "operator", "approver", "admin"] as const).map((item) => <option key={item} value={item}>{item}</option>)}</select> : <span className="status-badge ready">{member.role || "未分配"}</span>}</td>
                <td><span className={`status-badge ${member.is_primary ? "ready" : "processing"}`}>{member.is_primary ? "主职" : "兼职"}</span></td>
                <td><div className="review-state-actions">
                  {editingMemberId === member.id ? <button className="page-btn" disabled={member.version === undefined || !editingMemberJobTitle.trim()} onClick={async () => {
                    if (member.version === undefined) return;
                    try {
                      await updateDepartmentMember(member.id, { role: editingMemberRole, job_title: editingMemberJobTitle.trim(), valid_from: member.valid_from, valid_to: member.valid_to, version: member.version });
                      setEditingMemberId(null);
                      await loadMembers();
                      addToast({ type: "success", message: "任职信息已更新" });
                    } catch (caught) { setMemberError(formatApiError(caught, "更新任职失败")); }
                  }}>保存</button> : <button className="page-btn" disabled={member.version === undefined} onClick={() => {
                    setEditingMemberId(member.id);
                    setEditingMemberRole(member.role || "viewer");
                    setEditingMemberJobTitle(member.job_title || "");
                  }}>编辑</button>}
                  {!member.is_primary && <button className="page-btn" disabled={!actionReason.trim()} onClick={async () => {
                    try {
                      await setPrimaryDepartmentMember(member.id, actionReason.trim());
                      setActionReason("");
                      await loadMembers();
                      await loadOrganization(typeof user?.role === "string" ? user.role : undefined);
                      addToast({ type: "success", message: "主职已调整" });
                    } catch (caught) { setMemberError(formatApiError(caught, "调整主职失败")); }
                  }}>设为主职</button>}
                  <button className="page-btn" disabled={member.is_primary || !actionReason.trim()} title={member.is_primary ? "请先为该用户设置新的主职" : undefined} onClick={async () => {
                    const approved = await confirm({ title: "停用任职", message: `用户：${member.user_id}\n部门：${selected.path.join(" / ")}\n原因：${actionReason.trim()}`, confirmLabel: "确认停用", variant: "danger" });
                    if (!approved) return;
                    try {
                      await disableDepartmentMember(member.id, actionReason.trim());
                      setActionReason("");
                      await loadMembers();
                      addToast({ type: "success", message: "任职已停用" });
                    } catch (caught) { setMemberError(formatApiError(caught, "停用任职失败")); }
                  }}>停用</button>
                </div></td>
              </tr>) : <tr><td colSpan={5} className="log-empty">暂无成员数据</td></tr>}</tbody></table></div>
            </div>}
          </> : <div className="log-empty">请选择一个组织节点查看详情</div>}
        </section>
      </div>}

      <section className="review-section">
        <div className="review-section-title">我的任职</div>
        <div className="membership-list">
          {memberships.length === 0 && <div className="log-empty">没有有效任职，请联系管理员配置主职。</div>}
          {memberships.map((membership) => {
            const current = membership.id === currentMembership?.id;
            return <div className={`membership-card ${current ? "current" : ""}`} key={membership.id}>
              <div><strong>{membership.organization_path.join(" / ")}</strong><small>{membership.job_title || "岗位未设置"} · {membership.role}</small></div>
              <div className="membership-actions"><span className={`status-badge ${membership.is_primary ? "ready" : "processing"}`}>{membership.is_primary ? "主职" : "兼职"}</span>{current ? <span className="status-badge ready">当前生效</span> : <button className="page-btn" disabled={switching} onClick={() => void handleSwitch(membership.id)}>切换</button>}</div>
            </div>;
          })}
        </div>
      </section>
    </div>
  );
}
