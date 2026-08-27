import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "../../stores/authStore";
import { useOrganizationStore } from "../../stores/organizationStore";

const STORAGE_KEY = "sql_rpa_onboarding_completed_v1";

const STEPS = [
  { title: "连接模型", description: "配置并测试 LLM 与 Embedding。模型凭据仅由管理员维护。", path: "/settings", roles: ["admin"] },
  { title: "连接数据库", description: "确认目标数据库状态并浏览 Schema，写操作会进入审核流程。", path: "/database", roles: ["operator", "approver", "admin"] },
  { title: "准备知识库", description: "上传业务说明或数据字典，用于检索增强和 SQL 生成。", path: "/documents", roles: ["operator", "approver", "admin"] },
  { title: "理解用量", description: "查看模型请求、Token、成本和配额预警；账单以服务端数据为准。", path: "/usage", roles: ["approver", "admin"] },
];

export function OnboardingGuide() {
  const user = useAuthStore((state) => state.user);
  const currentContext = useOrganizationStore((state) => state.currentContext);
  const effectiveRole = currentContext ? currentContext.role : user?.role;
  const navigate = useNavigate();
  const [open, setOpen] = useState(() => localStorage.getItem(STORAGE_KEY) !== "true");
  const [step, setStep] = useState(0);
  const visibleSteps = STEPS.filter((item) => effectiveRole && item.roles.includes(effectiveRole));

  if (!open || visibleSteps.length === 0) return null;
  const current = visibleSteps[Math.min(step, visibleSteps.length - 1)];
  const finish = () => {
    localStorage.setItem(STORAGE_KEY, "true");
    setOpen(false);
  };

  return (
    <div className="modal-overlay onboarding-overlay">
      <div className="modal onboarding-dialog" role="dialog" aria-modal="true" aria-label="首次配置向导">
        <div className="modal-header">
          <div>
            <div className="modal-title">首次配置向导</div>
            <div className="onboarding-progress">第 {step + 1} / {visibleSteps.length} 步</div>
          </div>
          <button className="modal-close" onClick={finish} aria-label="跳过向导">×</button>
        </div>
        <div className="modal-body onboarding-body">
          <div className="onboarding-step-number">{step + 1}</div>
          <h3>{current.title}</h3>
          <p>{current.description}</p>
          <button className="btn-secondary" onClick={() => { finish(); navigate(current.path); }}>
            前往{current.title}
          </button>
        </div>
        <div className="confirm-footer">
          <button className="confirm-cancel" onClick={finish}>稍后再说</button>
          {step > 0 && <button className="confirm-cancel" onClick={() => setStep(step - 1)}>上一步</button>}
          <button className="confirm-primary" onClick={() => step + 1 < visibleSteps.length ? setStep(step + 1) : finish()}>
            {step + 1 < visibleSteps.length ? "下一步" : "完成"}
          </button>
        </div>
      </div>
    </div>
  );
}
