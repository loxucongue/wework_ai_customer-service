# Sales Strategy BI Audit

- Goal: audit every operations/sales BI metric against production MySQL, correct authoritative counting logic, test, and deploy.
- Non-goals: change reply, SOP execution, outreach scheduling, or customer-message sending behavior.
- Base SHA: `ac25114700e087781f8e45416cfc635a7b3d198c`
- Production baseline: `8f911bea`, release `ai-paths-unified-20260906-231341-8f911bea`; rollback to this release.
- Ownership: operations dashboard and V3 strategy analytics repositories/routes, their admin components, focused tests, and this task record.
- Contracts: V3 only; contact identity is corp + wechat + external/customer; SOP delivery and strategy callback remain separate facts.
- Validation: deterministic repository tests, backend suite for affected modules, frontend type/build, read-only production comparison, post-deploy health/API check.
- Completed locally: authoritative contact funnel, isolated-test exclusion, evidenced SOP sends, no-send reasons, unique outreach plans, corrected failure/retry/duration semantics.
- Strategy BI audit: 607 persisted events / 269 eligible customer turns / 20 strategy decisions; no isolated or fixture rows. Reply adoption is 7 of 17 candidate-bearing turns, not 7 of all 269 turns. The 44 recorded follow-up replies have no delivery attribution anchor, and order outcome queries have no eligible rows, so those rates must display as unavailable rather than zero or attributable success.
- Strategy BI changes: candidate-based adoption denominator, attribution-gated outcomes, unavailable nullable rates, blank dimension removal, and thread-pool execution for synchronous database reads used by the dashboard fan-out.
- Test evidence: `314 passed`; focused strategy/operations BI tests `28 passed`; frontend TypeScript, ESLint, and production build passed.
- Production read-only evidence before deploy: contacts `6156/183`, AI `46/45/1`, SOP `109/48/61/0`, first-day `35` triggers and `1` unique plan; cold process `8.6s`, pooled query work approximately `4.8s` during measured run.
- Strategy production read-only verification: `269` eligible turns, `17` candidate-bearing turns, `7` adopted (`41.18%`), `20` decision-eligible, no delivery/order attribution denominator; blank strategy dimension groups are excluded.
- Status: ready for release.
