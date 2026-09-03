# Store Matching Core Rebuild

- Goal: rebuild the V3 store matching tool around semantic destination resolution, constrained geocoding, visible-store matching, and deterministic delivery decisions.
- Non-goals: reply generation, SOP, message sending, appointments, and unrelated routing behavior.
- Base SHA: `8dec3d794894964c62cb0715a21c3626fb09c9ce`
- Production baseline at start: V3 release `ai-paths-v3-20260903-7121a9cb`; refreshed before integration to `ai-paths-v3-20260903-09f4f196`. The worker was already inactive during the concurrent SOP hotfix release, so this task did not change service state.
- Owned modules: store destination resolver, store lookup/resolution helpers, store-only test script, and store matching tests.
- Contracts: V3 only; visible-store scope is authoritative; model extracts semantics; code validates facts and delivery; isolated tests never send or persist customer data.
- Required acceptance: Huli returns its unique store; Xinjiang returns its unique visible store; Beijing resolves as a municipality; Jianyang Dahua International preserves the Jianyang anchor and never accepts Shenyang or Zhongshan candidates.
- Validation: repository tests `39/39`; Ruff and diff checks passed. Real write-free V3 store-only checks passed for `湖里`, `新疆`, `北京`, `简阳大华国际`, `万达中心B座`, `浙江人民医院附近有吗`, and `东坑`.
- Integration: merged to `main` at `2396070777455e359754fc7a31660d012aefc4ee`.
- Release: clean package `ai-paths-v3-20260903-23960707` staged on the production host and verified with a write-free real `新疆` query. It has not been activated because a concurrent SOP release left `ai-paths-workers.service` in `deactivating` with an active `systemctl stop` process; switching V3 during that operation would violate the release checklist.
- Status: implementation, integration, and pre-deployment validation complete; production activation remains blocked on the concurrent worker release finishing.
