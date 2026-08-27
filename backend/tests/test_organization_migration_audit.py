import uuid


def test_migration_preflight_quarantines_orphans_and_post_checks_conservation(tmp_path):
    from sqlalchemy import create_engine, text
    from scripts.organization_migration_check import audit

    path = tmp_path / "production-copy.db"
    url = f"sqlite:///{path.as_posix()}"
    engine = create_engine(url)
    tenant_id, user_id = str(uuid.uuid4()), str(uuid.uuid4())
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE tenants(id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE users(id TEXT PRIMARY KEY)"))
        conn.execute(text("CREATE TABLE memberships(id TEXT PRIMARY KEY, tenant_id TEXT, user_id TEXT)"))
        conn.execute(text("CREATE TABLE documents(id TEXT PRIMARY KEY, tenant_id TEXT, company_id TEXT, organization_id TEXT, membership_id TEXT)"))
        conn.execute(text("CREATE TABLE tenant_organization_mappings(tenant_id TEXT PRIMARY KEY, organization_id TEXT)"))
        conn.execute(text("CREATE TABLE organization_memberships(id TEXT PRIMARY KEY, user_id TEXT, organization_id TEXT, organization_level TEXT, active INTEGER, is_primary INTEGER)"))
        conn.execute(text("INSERT INTO tenants VALUES (:id)"), {"id": tenant_id})
        conn.execute(text("INSERT INTO users VALUES (:id)"), {"id": user_id})
        conn.execute(text("INSERT INTO memberships VALUES ('valid',:tenant,:user),('orphan','missing',:user)"),
                     {"tenant": tenant_id, "user": user_id})
        conn.execute(text("INSERT INTO documents VALUES ('doc',:tenant,:tenant,:tenant,NULL)"),
                     {"tenant": tenant_id})
        conn.execute(text("INSERT INTO tenant_organization_mappings VALUES (:tenant,:tenant)"),
                     {"tenant": tenant_id})
        conn.execute(text("INSERT INTO organization_memberships VALUES ('valid',:user,:tenant,'company',1,1),('orphan',:user,'missing','company',0,0)"),
                     {"tenant": tenant_id, "user": user_id})
    engine.dispose()

    pre = audit(url, "pre", "sanitized-production-copy")
    assert pre["status"] == "quarantined"
    assert any(item["issue_type"] == "orphan_membership" for item in pre["issues"])
    post = audit(url, "post", "sanitized-production-copy")
    assert post["conservation"]["tenant_mapping"] == {"source": 1, "target": 1}
    assert post["conservation"]["membership"] == {"source": 2, "target": 2}
    assert post["conserved"] is True
    assert len(post["checksum"]) == 64
