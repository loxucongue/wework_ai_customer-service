# V3 emotion and intent completion

- Type: V3 reply quality and decision-contract change
- Branch: `codex/v3-emotion-intent-completion`
- Base SHA: `0e009c408a44a857f41425015679177cfe6c738c`
- Production baseline: requires live verification; production deployment is not part of this task.
- Goal: define robust emotion boundaries, especially profanity versus true anger; complete intent/routing responsibilities; remove duplicate closing configuration; simplify avoidable semantic-selection calls only where quality is preserved; validate all model evaluations with DeepSeek.
- Non-goal: send customer messages, write production strategy events, call production write APIs, enable delayed closing sends, or make emotion authorize transactions.
- Exclusive scope: `ai_sales_policy_v1.json`, V3 semantic/reply prompts and policy validation, closing-catalog compatibility touched by cleanup, focused deterministic tests, ignored evaluation artifacts, and the intent/emotion/sales-strategy contracts.
- Invariants: V3 Reply remains the only final sales-semantic decision; profanity alone is never an anger or stop-marketing fact; active blockers are answered before closing; explicit opt-out still stops all marketing; appointments and deposits require authoritative facts and existing transaction permissions.
- Model-evaluation exception: the user explicitly requires DeepSeek for all model tests. Evaluation is isolated, read-only, writes only ignored artifacts, and sends no customer message.
- Risks: false angry classification may suppress valid sales; selector removal may reduce knowledge recall; forcing deposit progression may violate customer authorization.
- Verification: deterministic boundary tests, same-scenario DeepSeek classification/reply evaluation, call-count and latency comparison, full repository regression.
- Rollback: disable `AI_SALES_POLICY_ENABLED`, or revert the focused commits; no production database migration is planned.
