# Sales BI V3 Effectiveness

- Goal: rebuild Sales Strategy BI around the observable V3 strategy path, showing whether each decision, checkpoint, sequence, and script stage is used and where business content is missing.
- Non-goals: change V3 reply decisions, customer messages, SOP processing, or delivery/order attribution.
- Base SHA: `f8d63e46`.
- Production baseline: backend `ai-paths-unified-20260907-003744-5ad40a66`, frontend `frontend-20260907-bi-6ee81405`.
- Ownership: V3 strategy analytics repository/API, Sales Strategy BI component, focused tests, interface/current docs.
- Contracts: read-only additive analytics; one customer turn is not one customer; candidates, final selections, delivery, and outcomes remain separate facts.
- Change contract: product analytics redesign; medium UI/reporting risk; deterministic tests, frontend build, production read-only comparison, browser QA; rollback to the production baseline above.
- Completed locally: production field/denominator audit; additive adoption-detail schema; conservative historical backfill tool; effective-decision summary and dimensions; management BI redesign; focused backend tests and frontend production build.
- Pending: full regression, production migration/backfill, release, read-only reconciliation, and browser QA.
