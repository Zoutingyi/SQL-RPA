# Required merge check

Protect the production branch and require the `user-acceptance-gate` status check.
That stable aggregate check fails unless backend tests, contract snapshots, Playwright E2E,
PostgreSQL/Redis integration tests, and the migration round-trip all succeed.
