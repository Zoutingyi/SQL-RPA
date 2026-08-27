import { Outlet } from "react-router-dom";
import { useOrganizationStore } from "../../stores/organizationStore";

export function RequireDepartmentContext() {
  const context = useOrganizationStore((state) => state.currentContext);

  if (!context) {
    return (
      <div className="db-empty-state" role="alert">
        <div>
          <h2>请先切换部门</h2>
          <p>此页面只处理当前部门的数据；未选择有效任职时不会发起业务请求。</p>
        </div>
      </div>
    );
  }

  return <Outlet />;
}
