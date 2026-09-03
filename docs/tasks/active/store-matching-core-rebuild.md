# Store Matching Core Rebuild

- Goal: rebuild the V3 store matching tool around semantic destination resolution, constrained geocoding, visible-store matching, and deterministic delivery decisions.
- Non-goals: reply generation, SOP, message sending, appointments, and unrelated routing behavior.
- Base SHA: `8dec3d794894964c62cb0715a21c3626fb09c9ce`
- Production baseline at start: V3 release `ai-paths-v3-20260903-7121a9cb`; refreshed before integration to `ai-paths-v3-20260903-09f4f196`. The worker was already inactive during the concurrent SOP hotfix release, so this task did not change service state.
- Owned modules: store destination resolver, store lookup/resolution helpers, store-only test script, and store matching tests.
- Contracts: V3 only; visible-store scope is authoritative; model extracts semantics; code validates facts and delivery; isolated tests never send or persist customer data.
- Required acceptance: Huli returns its unique store; Xinjiang returns its unique visible store; Beijing resolves as a municipality; Jianyang Dahua International preserves the Jianyang anchor and never accepts Shenyang or Zhongshan candidates.
- Validation: repository tests `56/56`; Ruff and diff checks passed. The final real-model/map matrix covered 122 supplied destinations against a real 217-store customer-visible scope with empty conversation history: 114 delivered stores, 7 correctly required location confirmation, 1 correctly returned no visible candidate, 0 tool errors, 0 unanchored deliveries, 0 reply generation/sends/memory writes. Fixed cases include `湖里`, `新疆`, `北京`, `简阳大华国际`, Beijing Daxing Airport, Shanghai Hongqiao Hub, functional zones, county-level cities, townships, old district names, typo normalization, and relative landmark descriptions.
- Integration: merged to `main` at `2396070777455e359754fc7a31660d012aefc4ee`.
- Release: obsolete pre-fix package `ai-paths-v3-20260903-23960707` remains staged but must not be activated. Production still points to `ai-paths-v3-20260903-09f4f196`; V3, control-plane, and worker services were all active before the final release build.
- Status: final fixes and validation complete; commit, push, clean release build, V3-only activation, and post-deployment read-only checks remain.
