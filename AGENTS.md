# Project Operating Contract

This repository follows one core rule for the customer reply chain:

- The model owns business semantics, customer psychology, and sales rhythm.
- Code owns factual inputs, tool calls, schema normalization, idempotency, safety boundaries, and non-business fallback.

Do not add Python keyword branches that decide normal sales intent, objections, or conversation stage. If a business reply is wrong, first inspect the model prompt, context payload, model choice, and tool facts.

## Testing Modes

Single-node model effect tests are used before online deployment to tune one model node in isolation. They call the Planner or Reply model with controlled context fixtures, so prompt changes can be evaluated without polluting online customer history.

Full-chain online tests are used after deployment to verify the whole runtime: SOP Gate, Planner, tools, Reply, async send, logs, latency, persistence, and real platform integration behavior.

Both modes are required for reply-quality changes. Single-node tests answer “is this model prompt/context good enough?” Full-chain tests answer “does the deployed system work end to end?”
