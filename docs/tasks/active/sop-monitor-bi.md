# SOP Monitor BI

- Goal: report today's production SOP processing accurately and rebuild the third-party SOP page into a management BI for throughput, outcomes, backlog, latency, account distribution, and failure bottlenecks.
- Non-goals: change SOP task decisions, sending, consumption, strategy callbacks, concurrency, or retry behavior.
- Base SHA: `46a17696`.
- Production baseline: backend/frontend `20260907-102203-753c3324`; MySQL; worker queue and pending both zero at task start.
- Ownership: SOP analytics repository/API, third-party SOP monitoring page and proxy, focused tests, public/current docs.
- Contracts: task, message, customer, and run counts remain separate; platform task terminal state and local event state are not inferred from each other; BI endpoints are read-only.
- Change contract: additive read-only analytics and UI redesign; medium reporting/query risk; focused and full regression, query-plan/read-only production reconciliation, frontend build, desktop/mobile browser QA; rollback to the production baseline above.
- Completed locally:
  - Production today baseline reconciled at 107 execution runs: 54 displayed completed, 52 no-send, 1 recoverable exception; 53 sends have authoritative active-send message IDs (121 messages), across 66 customers.
  - Operations metrics now separate upstream events, local tasks, scoped customers, confirmed sends/messages, no-send, recoverable failures, unfinished work, accounts, hourly outcomes, and persisted latency.
  - SOP page now defaults to today, reads worker health from the worker service instead of the control process, shows management BI before the existing evidence drill-down, and avoids live third-party queue reads by default.
- Validation: `342 passed`; frontend type-check and focused lint passed; production frontend build passed; local browser loaded production data through read-only tunnels.
- Remaining: production data reconciliation on the candidate release, commit/push, deploy, desktop/mobile browser QA, then close task documentation.
