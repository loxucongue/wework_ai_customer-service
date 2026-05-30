# AI Paths Project Agent Guide

## Current Context

- Working directory: `e:\ai_code\vscode_codex\coze_cli_project`.
- User-facing language: Chinese by default.
- Target migration: move the enterprise WeChat medical-aesthetic customer service workflow from Coze node orchestration to a local Python FastAPI + LangGraph service.
- Coze remains as a temporary tool layer for knowledge-base retrieval, pricing database CRUD, and external business APIs until those systems are migrated.
- Existing frontend test project lives in `projects/` and currently calls the Coze workflow directly.
- Primary product planning document: `https://jv46huq18bb.feishu.cn/wiki/T5zDwS087iZsVMkbpnIcdX4xnLc`.

## Product Goal

Build a locally debuggable AI customer service service that can:

- Handle one customer message with multiple business intents in the same turn.
- Answer the current question while guiding the customer toward project intent and store appointment.
- Read appointment/context cache every round and surface cross-cutting reminders when useful.
- Run customer portrait and event extraction every round.
- Keep every graph node auditable through structured trace logs.
- Reduce future dependence on manual Coze node editing.

## Customer Service Persona

- The assistant represents the company's enterprise WeChat customer service and should refer to itself as `小贝` when self-reference is needed.
- Do not proactively disclose that the assistant is an AI. If the customer directly asks whether it is AI, answer naturally and avoid deception, while keeping the service focused.
- Avoid robotic system language such as `系统查询到`, `知识库显示`, `工具返回`, `我是AI客服`.
- For escalation, do not use blunt phrases like `转人工` unless the product explicitly requires that wording. Prefer customer-facing wording such as:
  - `这个我让专业人士协助你看下`
  - `这个情况我帮你同步给门店/护理老师确认下`
  - `涉及具体处理，我让专业同事接着协助你`
- Customer replies must primarily solve the customer's current problem. Do not overuse disclaimer, deferral, or "needs confirmation" language when available facts are enough to answer.
- When a fact is available from tools, memory, image understanding, or customer text, use it directly and briefly before asking for more information.
- Ask at most one necessary follow-up question unless the scenario is after-sales risk collection.

## Architecture Principles

- LangGraph owns orchestration, state, routing, multi-intent planning, reply synthesis, and trace logging.
- Coze tool workflows are called through typed wrappers only. Do not scatter raw workflow calls through graph nodes.
- Keep workflow IDs and tool contracts in `docs/langgraph_coze_tool_contracts.md`.
- Keep agentic prompting, skill boundaries, and model-routing decisions in `docs/agentic_prompting_and_model_policy.md`.
- Keep every flow as short and deterministic as practical. Prefer stable tool/code paths over long chained model reasoning.
- First release should be a minimal working loop, not a full rewrite:
  - input normalization
  - context loading
  - optional image understanding
  - multi-intent routing
  - Coze knowledge/database tool calls
  - global reply planning
  - final reply messages
  - profile/event extraction
  - trace persistence
- Multi-intent replies should usually cover at most three business intents per turn.
- Prefer explicit, typed state over ad hoc dictionaries where practical.

## Agentic Prompting And Model Choice

- Do not treat `SF1-SF12` as mutually exclusive routes. Treat them as composable business skills that return facts, reply points, missing slots, risks, and suggested next steps.
- Every model node must define: role, goal, inputs, allowed tools/data, decision rules, forbidden actions, output schema, and fallback behavior.
- Every business skill must have one focused responsibility and minimal tool access. It should not directly monopolize final customer replies unless it is the final reply synthesizer.
- Prefer shallow, stable workflows. Avoid adding model nodes when a deterministic rule, cached context, direct interface call, or template can solve the task.
- Prefer the fastest sufficient path:
  - L0 deterministic work: Python code, no model.
  - L1 simple classification, query rewrite, slot extraction: fastest low-cost text model.
  - L2 multi-intent planning, structured summaries, normal trust/competitor/after-sales reasoning: balanced model.
  - L3 sensitive multi-tool final replies, strong distrust, complaint-adjacent, complex competitor or after-sales tone: stronger model.
  - LV image understanding: vision model only, output structured `image_info`.
- Upgrade model strength only when complexity, ambiguity, sensitivity, multi-intent context, or tool-result conflict requires it.
- Do not use strong models for field mapping, sorting, JSON cleanup, exact DB parsing, or fixed template output.

## Required Logging

Every LangGraph node must record:

- `request_id`
- node name
- start time and end time
- duration in milliseconds
- compact input snapshot
- compact output snapshot
- tool calls made by the node
- errors, if any

Local logs should be written under `logs/runs/{request_id}.json` and may also be appended to JSONL later.

Do not log raw secrets, bearer tokens, cookies, private keys, or full sensitive personal information.

## Secrets And Credentials

- Never hard-code API keys, Coze tokens, cookies, private keys, or refresh tokens.
- Use environment variables:
  - `ALIYUN_DASHSCOPE_API_KEY`
  - `VOLCENGINE_ARK_API_KEY`
  - `COZE_OAUTH_CLIENT_ID`
  - `COZE_OAUTH_PUBLIC_KEY_ID`
  - `COZE_OAUTH_PRIVATE_KEY_FILE`
  - `COZE_OAUTH_TOKEN_TTL`
- Local development may use `.env`, but `.env` must not be committed.
- If credentials appear in chat or local files, do not copy them into documentation or source code.

## Coze Tool Contracts

Known workflow IDs:

- Unified knowledge-base search: `7644575365759746083`
- Pricing database CRUD: `7641872030061117450`
- Pricing knowledge-base sync: `7644090458134609974`

Current knowledge-base enum values:

- `project_price`
- `project_qa`
- `competitor_qa`
- `trust_assets`
- `after_sales_qa`

The unified knowledge-base workflow returns only `outputList[].output` and `outputList[].documentId`. Do not assume score, title, metadata, or image URL fields exist unless a later contract adds them.

## Frontend Project Rules

- `projects/` is the existing Next.js test frontend and pricing configuration UI.
- It currently handles:
  - chat simulation
  - image upload to object storage
  - Coze workflow chat calls
  - project pricing CRUD UI
  - pricing document sync
- Keep existing upload behavior unless explicitly replacing it.
- When switching chat to local FastAPI, preserve the request shape as much as practical:
  - `content`
  - `customer_id`
  - `corp_id`
  - `conversation_history`
  - `file_image`

## Coding Rules

- Prefer small, verifiable changes.
- Use `apply_patch` for manual file edits.
- Do not rewrite unrelated files.
- Do not move or delete user assets without explicit request.
- Do not commit or expose files under `projects/assets/` that contain private keys.
- For Python code:
  - keep modules typed where practical
  - use Pydantic models for HTTP boundaries
  - keep Coze/model clients behind service classes
  - keep graph nodes small and traceable
- For frontend code:
  - use `pnpm` only
  - preserve TypeScript strictness
  - prefer existing UI/components

## Task Operating Mode

For substantial work:

1. Inspect the relevant code and docs.
2. State the next concrete steps.
3. Update a durable task/roadmap document when architecture decisions are made.
4. Implement the smallest useful slice.
5. Run a syntax or smoke check.
6. Judge whether the actual customer-facing reply is appropriate:
   - Did it answer the customer's current question?
   - Did it use known facts instead of kicking the issue back?
   - Was any disclaimer necessary and short?
   - Did it avoid exposing AI/tool/system process?
   - Did it maintain the `小贝` persona?
7. Report what changed, what was verified, reply-quality judgment, and what remains.

If product assumptions are missing and cannot be inferred from local context, ask the user before implementing irreversible or broad changes.
