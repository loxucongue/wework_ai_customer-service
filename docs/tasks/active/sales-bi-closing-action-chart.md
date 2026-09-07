# Sales BI Closing Action Chart

- Goal: aggregate duplicate closing action rows in the Sales Strategy BI chart while preserving detailed strategy rows.
- Non-goals: change strategy decisions, persisted analytics data, or sending behavior.
- Base SHA: `6a3d379c`.
- Production baseline: `ai-paths-unified-20260907-bi-4a926b66`; rollback to the corresponding frontend release.
- Ownership: `projects/src/components/admin/sales-strategy-dashboard.tsx`, focused validation, and this task record.
- Validation: frontend type/Lint/build, production API comparison, and browser screenshot.
- Status: active.
