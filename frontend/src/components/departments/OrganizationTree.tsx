import { memo, useMemo } from "react";
import { ORGANIZATION_LEVEL_LABELS, type OrganizationUnit } from "../../types/organization";

interface OrganizationTreeProps {
  items: OrganizationUnit[];
  query: string;
  currentOrganizationId?: string;
  selectedOrganizationId?: string;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelect: (unit: OrganizationUnit) => void;
}

function filterTree(items: OrganizationUnit[], query: string): OrganizationUnit[] {
  const normalized = query.trim().toLocaleLowerCase("zh-CN");
  if (!normalized) return items;
  return items.flatMap((item) => {
    const children = filterTree(item.children, normalized);
    const matches = item.name.toLocaleLowerCase("zh-CN").includes(normalized)
      || item.path.join(" / ").toLocaleLowerCase("zh-CN").includes(normalized);
    return matches || children.length ? [{ ...item, children }] : [];
  });
}

interface TreeNodeProps {
  item: OrganizationUnit;
  level: number;
  forceExpanded: boolean;
  currentOrganizationId?: string;
  selectedOrganizationId?: string;
  expanded: Set<string>;
  onToggle: (id: string) => void;
  onSelect: (unit: OrganizationUnit) => void;
}

const TreeNode = memo(function TreeNode({
  item, level, forceExpanded, currentOrganizationId, selectedOrganizationId,
  expanded, onToggle, onSelect,
}: TreeNodeProps) {
  const hasChildren = item.children.length > 0;
  const isExpanded = forceExpanded || expanded.has(item.id);
  const isCurrent = item.id === currentOrganizationId;
  const isSelected = item.id === selectedOrganizationId;
  return (
    <li role="treeitem" aria-level={level} aria-expanded={hasChildren ? isExpanded : undefined} aria-current={isCurrent ? "true" : undefined}>
      <div className={`organization-tree-row ${isCurrent ? "current" : ""} ${isSelected ? "selected" : ""} ${!item.active ? "disabled" : ""}`}>
        <button
          type="button"
          className="organization-tree-toggle"
          aria-label={hasChildren ? `${isExpanded ? "折叠" : "展开"}${item.name}` : undefined}
          onClick={() => hasChildren && onToggle(item.id)}
          disabled={!hasChildren}
        >
          {hasChildren ? (isExpanded ? "▾" : "▸") : "·"}
        </button>
        <button type="button" className="organization-tree-select" onClick={() => onSelect(item)}>
          <span className={`organization-level level-${item.level}`}>{ORGANIZATION_LEVEL_LABELS[item.level]}</span>
          <span className="organization-node-name">{item.name}{item.level === "individual" && item.job_title ? `－${item.job_title}` : ""}</span>
          {!item.active && <span className="status-badge failed">已停用</span>}
          {typeof item.member_count === "number" && <small>{item.member_count} 人</small>}
          {isCurrent && <span className="status-badge ready">当前</span>}
        </button>
      </div>
      {hasChildren && isExpanded && (
        <ul role="group">
          {item.children.map((child) => (
            <TreeNode
              key={child.id}
              item={child}
              level={level + 1}
              forceExpanded={forceExpanded}
              currentOrganizationId={currentOrganizationId}
              selectedOrganizationId={selectedOrganizationId}
              expanded={expanded}
              onToggle={onToggle}
              onSelect={onSelect}
            />
          ))}
        </ul>
      )}
    </li>
  );
});

export function OrganizationTree(props: OrganizationTreeProps) {
  const filtered = useMemo(() => filterTree(props.items, props.query), [props.items, props.query]);
  if (!filtered.length) return <div className="log-empty">没有匹配的部门节点</div>;
  return (
    <ul className="organization-tree" role="tree" aria-label="公司、部门、小组和个人组织树">
      {filtered.map((item) => (
        <TreeNode
          key={item.id}
          item={item}
          level={1}
          forceExpanded={!!props.query.trim()}
          currentOrganizationId={props.currentOrganizationId}
          selectedOrganizationId={props.selectedOrganizationId}
          expanded={props.expanded}
          onToggle={props.onToggle}
          onSelect={props.onSelect}
        />
      ))}
    </ul>
  );
}
