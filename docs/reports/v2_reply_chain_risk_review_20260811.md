# V2 Reply Chain Risk Review - 2026-08-11

## Scope

- Branch: `codex/reply-chain-refactor`
- Runtime: V2 ordinary reply sidecar only
- V1 ordinary reply, SOP Event, Outreach and platform task workers remain on the primary service
- No real customer message was sent during validation

## Architecture Review

The active V2 path is:

`normalize -> background facts -> shared context -> parallel Content Gate / Tool Planner -> read-only tools -> deterministic evidence join -> Reply -> structural validation -> fact auditor -> commit coordinator`

- Content Gate nominates at most a small set of evidence assets. It does not write customer replies or choose a closing action.
- Tool Planner requests read-only facts. It does not receive sales copy and does not generate customer replies.
- Join only merges evidence and conflicts.
- Reply is the only owner of normal sales semantics, wording, progression, switching, pausing and closing.
- Deterministic validation checks IDs, permissions, message structures, payment arithmetic, asset delivery integrity and evidence references.
- The fact auditor checks whether visible claims are supported. It cannot choose sales posture or rewrite the reply.
- Commit executes only validated deferred writes and SOP completion records.

## Risks Fixed

1. **SOP configuration drift**
   - V2 now consumes the same six-pack base SOP configuration as V1.
   - V2-only code metadata is applied from `v2_sop_asset_overlay.json`.
   - The overlay rejects customer-visible text, media, price or other business-content fields.
   - The current operator-edited opening, case media and store prompt were preserved; the confirmed activity, objection and deposit assets were separated so first activity education no longer carries a payment card.

2. **Code forcing store cards after the model chose not to send them**
   - Reply must explicitly decide `deliver` or `defer` for every current structured delivery option.
   - Code validates the selected decision and real store IDs but does not choose the sales action.
   - A real inbound location card remains a protocol-level delivery contract.

3. **Model copying a fake structured fact reference from the JSON example**
   - The output example now uses an empty decision array.
   - Reply may only copy exact fact references supplied in the current input.
   - The three previously failing simulation trajectories passed after this change.

4. **Prompt tests coupled to exact wording**
   - Tests now verify sections, ownership boundaries, prohibited legacy fields and factual contracts.
   - Prompt wording may evolve without weakening the architecture contract.

5. **Untraceable dirty sidecar deployment**
   - `/health` now returns release ID, Git commit, dirty flag and configuration revision.
   - Release manifest generation hashes the active business and SOP configuration.

6. **Spoofable V2 trusted-proxy header**
   - Header-based compatibility bypass now also requires a loopback Nginx connection.
   - Remote callers must still provide an accepted bearer token.

7. **Latest mainline administrative-region alias fix**
   - Autonomous-prefecture and county-level-city aliases were migrated into V2 store fact normalization.
   - This is location data normalization, not a sales-intent rule.

## Validation

- Deterministic suite: `1297 passed, 2 skipped`
- Focused Reply suite: `541 passed`
- Configuration/release/V2 route suite: `29 passed`
- Focused real-model simulations after repair:
  - `one_session_precision__v01`: hard pass
  - `opening_to_need__v04`: hard pass
  - `price_transparency__v03`: hard pass
- Focused simulations had no neutral fallback and no infrastructure failure after the repair.

## Remaining Risks

1. **Effect-confidence wording**
   - One model response used stronger wording than desirable for a one-session result.
   - This is a model-semantic quality issue. It must be controlled by authoritative effect facts, sales principles and semantic regression tests, not a Python phrase blacklist.

2. **Latency and cost**
   - Gate and Tool Planner are parallel, but Reply plus fact audit still creates multiple model calls.
   - The current focused runs took about 39-44 seconds. Speed was not an acceptance gate for this stage.

3. **Main branch divergence**
   - V2 intentionally replaces the old Planner/Normalizer path and therefore cannot be source-identical to current `main`.
   - Latest facts and behavior that affect V2 must be migrated by contract and regression tests; V1-only Outreach and platform-task worker changes stay in the primary service.

4. **Shared data safety**
   - V2 uses shared production facts and persistence, but its background workers are disabled.
   - Smoke tests must use `sim_` identities. Real customer-visible sends still require explicit review.

## Release Decision

The V2 sidecar is suitable for isolated platform-compatible testing after a clean, traceable commit and manifest-based deployment. It is not approved to replace V1 or to receive unreviewed broad production traffic.
