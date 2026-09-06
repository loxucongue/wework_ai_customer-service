# Sales Strategy BI Audit

- Goal: audit every operations/sales BI metric against production MySQL, correct authoritative counting logic, test, and deploy.
- Non-goals: change reply, SOP execution, outreach scheduling, or customer-message sending behavior.
- Base SHA: `ac25114700e087781f8e45416cfc635a7b3d198c`
- Production baseline: `8f911bea`, release `ai-paths-unified-20260906-231341-8f911bea`; rollback to this release.
- Ownership: `ai_paths/app/services/storage/operations_dashboard_repository.py`, `projects/src/components/admin/operations-dashboard.tsx`, focused tests and this task record.
- Contracts: V3 only; contact identity is corp + wechat + external/customer; SOP delivery and strategy callback remain separate facts.
- Validation: deterministic repository tests, backend suite for affected modules, frontend type/build, read-only production comparison, post-deploy health/API check.
- Completed locally: authoritative contact funnel, isolated-test exclusion, evidenced SOP sends, no-send reasons, unique outreach plans, corrected failure/retry/duration semantics.
- Test evidence: `314 passed`; focused BI tests `2 passed`; frontend TypeScript and ESLint passed.
- Production read-only evidence before deploy: contacts `6156/183`, AI `46/45/1`, SOP `109/48/61/0`, first-day `35` triggers and `1` unique plan; cold process `8.6s`, pooled query work approximately `4.8s` during measured run.
- Status: ready for release.
