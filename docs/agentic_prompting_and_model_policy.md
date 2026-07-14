# Agentic Prompting And Model Policy

## Project Constitution

The AI customer-service workflow should use strong models for business semantics instead of replacing them with brittle Python keyword rules.

Planner and Reply are responsible for:

- understanding the customer's current intent and emotional state;
- deciding whether to continue history or answer the current question directly;
- choosing the sales rhythm after answering the current concern;
- deciding when to explain deposit value, send a payment card, ask for registration information, or move to store/date scheduling.

Code is responsible for:

- cleaning input and preserving UTF-8 text;
- fetching and summarizing facts;
- validating tool arguments;
- normalizing message schema;
- enforcing hard boundaries for real store facts, payment amounts, order state, appointment facts, media URLs, and safety notices;
- preventing empty HTTP replies with a neutral non-business fallback.

Business wording, customer psychology, and ordinary sales timing should be fixed in prompts, context payloads, model selection, or tests, not by adding Python keyword branches.

## Testing Modes

Single-node model effect tests isolate one model node, usually Planner or Reply. They use fixed context fixtures and directly inspect the model's raw JSON or text output. Use them for prompt tuning, model comparison, context-shape checks, and sales-rhythm regressions.

Full-chain online tests run against the deployed service. They verify SOP Gate, Planner, tools, Reply, async send, logs, latency, persistent state, and platform APIs together. Use them before judging production readiness.

For every reply-quality change, run the smallest relevant single-node set first, then deploy and run full-chain online smoke if the change affects production behavior.
