import { useState } from "react";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";
import { useToastStore } from "../../stores/toastStore";
import { ProfilePage } from "./ProfilePage";

export function MembershipAccessRecovery() {
  const memberships = useOrganizationStore((state) => state.memberships);
  const currentContext = useOrganizationStore((state) => state.currentContext);
  const switching = useOrganizationStore((state) => state.switching);
  const error = useOrganizationStore((state) => state.error);
  const switchMembership = useOrganizationStore((state) => state.switchMembership);
  const loadMe = useAuthStore((state) => state.loadMe);
  const addToast = useToastStore((state) => state.addToast);
  const [selectedMembershipId, setSelectedMembershipId] = useState("");
  const alternatives = memberships.filter((membership) => (
    membership.active
    && membership.id !== currentContext?.membership_id
    && membership.role !== null
    && membership.role !== "unassigned"
  ));

  const handleSwitch = async () => {
    if (!selectedMembershipId) return;
    if (await switchMembership(selectedMembershipId)) {
      await loadMe();
      addToast({ type: "success", message: "已切换到具备权限的部门任职" });
    }
  };

  return (
    <div>
      <ProfilePage restricted />
      {alternatives.length > 0 && (
        <section className="review-section membership-recovery" aria-labelledby="membership-recovery-title">
          <div className="review-section-title" id="membership-recovery-title">切换其他任职</div>
          <p className="doc-meta">当前任职未分配业务权限。你可以主动切换到其他已授权任职，系统不会合并多个任职的权限。</p>
          {error && <div className="rollback-error" role="alert">{error}</div>}
          <div className="review-state-actions">
            <select
              className="log-filter-select"
              value={selectedMembershipId}
              onChange={(event) => setSelectedMembershipId(event.target.value)}
              aria-label="选择其他有效部门任职"
            >
              <option value="">请选择任职</option>
              {alternatives.map((membership) => (
                <option key={membership.id} value={membership.id}>
                  {membership.organization_path.join(" / ")} · {membership.job_title || "岗位未设置"} · {membership.role}
                </option>
              ))}
            </select>
            <button className="page-btn" disabled={!selectedMembershipId || switching} onClick={() => void handleSwitch()}>
              {switching ? "切换中..." : "确认切换"}
            </button>
          </div>
        </section>
      )}
    </div>
  );
}
