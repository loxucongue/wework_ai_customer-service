# LangGraph Migration Task List

## Goal

Replace the current single-route Coze customer-service workflow with a local FastAPI + LangGraph service while keeping Coze as a tool layer for knowledge-base search, pricing database operations, and external business APIs.

## Phase 1: Minimal Local Conversation Loop

Status: mostly complete

Scope:

- Create local backend skeleton under `ai_paths/`.
- Expose `POST /chat` compatible with the current frontend request shape.
- Build a minimal LangGraph with trace logging.
- Support first-pass multi-intent routing and reply generation.
- Wrap Coze tools:
  - unified KB search workflow `7644575365759746083`
  - pricing DB CRUD workflow `7641872030061117450`
- Persist per-request trace to `logs/runs/{request_id}.json`.
- Proxy the existing Next.js chat API to local AI Paths by default.

Acceptance:

- A local API can receive `content`, `customer_id`, `corp_id`, `conversation_history`, and optional `file_image`.
- The graph returns `reply_messages`, `scene`, `intent`, `subflow`, and `request_id`.
- Every node writes an input/output trace entry.
- Missing external tokens fail gracefully with a structured error, not a crash.
- Existing chat frontend can keep using `/api/chat` without changing component code.

## Phase 2: Core Business Modules

Status: in progress

Scope:

- Project consultation using `project_qa`.
- Price consultation using pricing DB first, then `project_price` fallback.
- Trust building using `trust_assets`.
- Image understanding using a multimodal model, feeding SF4/project modules.
- Global reply planner combining up to three intents.
- Keep the first implementation lightweight: deterministic guardrails and planner heuristics before introducing model calls.

Acceptance:

- One customer message can produce a combined reply for image consult + price + store/trust context.
- Reply planner deduplicates repeated questions and avoids forcing customers to name professional projects when they describe needs.
- Customer-facing replies use the `小美` persona and do not expose internal AI/tool workflow language.

## Phase 3: Appointment And Store Context

Scope:

- Add appointment cache read every turn.
- Refresh appointment data on appointment-related messages.
- Add store matching and address lookup as tool wrappers.
- Global planner can remind customers of existing appointments when relevant.

Acceptance:

- Existing appointment state can influence any subflow reply.
- Address/parking questions do not trap later trust or price questions in SF6.

## Phase 4: Profile And Events

Scope:

- Run profile/event extraction every turn.
- Write only meaningful updates or diffs.
- Keep skin details such as `点状斑点`, `片状色沉`, `面部色沉`, and customer-stated goals.

Acceptance:

- Customer picture + text details are available to later price/project/trust modules.
- Event records remain business-level, not one fragmented event per phrase.

Status update:

- Local first-pass `profile_event_extractor` is implemented.
- It currently returns per-turn `profile_update` and `event_updates` in `/chat` response meta and trace logs.
- It can capture examples such as `点状斑点`, `祛斑`, `淡斑`, `皮秒`, price concern, and trust concern.
- Local JSON persistence is implemented through `CustomerMemoryStore`.
- Memory is loaded at the start of each run and saved under `logs/memory/{customer_id}.json` after profile/event extraction.
- This is a local development store only; later it should be replaced or backed by the customer system/database.
- Reply synthesis now consumes loaded memory for lightweight continuity. Example: after the customer says `点状斑为主`, a later `皮秒多少钱` reply can mention that pricing/configuration should consider spot depth and range.
- Price consultation now has local Excel fallback through `projects/public/items_pricing_system.xlsx` when Coze pricing DB is unavailable.
- If an exact procedure price such as `皮秒` is not found, the reply now avoids substituting unrelated skincare/product prices as the procedure quote.
- Multi-candidate pricing now lists up to three relevant configurations for broad categories such as `水光` or `祛斑`, while exact-procedure pricing still prefers a single exact item.
- Memory continuity now applies a simple relevance check so unrelated previous needs, such as `祛斑`, do not pollute later `热玛吉` pricing replies.

## Phase 5: Frontend Integration And Debugging

Scope:

- Switch `projects/src/app/api/chat/route.ts` to local FastAPI.
- Preserve image upload flow.
- Add a trace/debug panel using `request_id`.
- Keep pricing configuration UI using existing Coze DB/sync workflows.

Acceptance:

- The frontend can simulate conversation against local AI Paths service.
- Each assistant reply can be traced to graph nodes and tool calls.

Status update:

- Chat API proxy already defaults to local AI Paths and preserves the existing frontend request shape.
- Assistant message metadata now carries `request_id`, `trace_url`, tool result keys, image understanding output, profile updates, event updates, and memory-read status.
- The chat bubble now includes a collapsible `调试信息` panel so each local run can be inspected from the conversation UI without opening trace files first.
- Windows local dev can start the Next test UI directly through `pnpm tsx watch src/server.ts` when the `pnpm dev` bash script is unavailable. In this environment the UI is running on `http://127.0.0.1:5000`.
- Multi-intent planning now merges deterministic keyword-triggered intents back into optional model planner output, preventing explicit price/trust/project words from being dropped.
- Store words inside trust questions, such as `厦门店正规吗`, no longer incorrectly consume a store-match action.
- End-to-end test through `/api/chat` now handles `trust_issue + price_inquiry + project_inquiry` in one turn and returns profile/event updates for `点状斑点`, `祛斑/淡斑`, `皮秒`, `厦门`, and trust/price concerns.
- Local Coze OAuth is configured through ignored `ai_paths/.env`; the unified KB workflow is reachable with JWT OAuth.
- Skill-specific KB queries are now shortened per skill, improving hits for `trust_assets` and `project_qa` in mixed-intent turns.
- `trust_assets` image snippets are now parsed from `<img src="...">` and inserted as real `image` reply messages after the trust-building text, while keeping the reply capped at three messages.
- When a trust image consumes one of the three reply slots, project guidance such as `点状斑/淡斑 -> 皮秒属于光电淡斑方向` is compressed into the price reply so mixed-intent answers do not lose the project-consult part.
- Price replies now parse `project_price` KB snippets with labels such as `项目名称`, `新客体验价`, `活动价`, and `报价备注` as a fallback when structured DB/Excel lookup misses fuzzy project names.
- If a KB snippet only confirms a related configuration such as `超皮秒祛斑` but lacks an explicit price field, the reply acknowledges the matched configuration without inventing a number.
- `project_price` KB retrieval now uses only the detected project name as the query, matching the pricing KB's fuzzy project-name lookup design.
- Independent Coze calls in one turn are now executed concurrently inside `execute_actions`, reducing mixed-intent latency while preserving the same `tool_results`, `module_outputs`, and trace structure.
- Price selection priority is now fixed as: complete `project_price` KB price slice, then Coze DB row, then local Excel fallback, then KB note-only acknowledgement without inventing a number.

## Immediate Next Tasks

1. Replace keyword-only route node with lightweight `planner_brain`.
2. Add `hard_guardrails` before planning.
3. Replace `collect_tools` with action-driven `execute_actions`.
4. Replace template reply node with `synthesize_reply`.
5. Keep model-free implementation first for stability and speed.
6. Add real model client abstraction and let planner/reply synthesizer upgrade to L1/L2/L3 models only when needed.

Status update:

- Items 1-6 are implemented in the first lightweight version.
- `synthesize_reply` now attempts model-based JSON reply synthesis only for complex turns and falls back to deterministic templates when model credentials are missing or a call fails.
- `planner_brain` now attempts optional L1/L2 JSON planning for complex turns and falls back to deterministic heuristics when model credentials are missing or a call fails.
- `price_consult`, `trust_build`, and `project_consult` now emit structured `facts`, `reply_points`, `missing_slots`, `risk_flags`, and `suggested_next_step` instead of only generic templates.
- Price fallback now preserves the detected project name, e.g. `皮秒价格要看具体配置`, instead of asking the customer which project they meant.
- `image_understanding` now supports optional vision-model JSON extraction into `image_info`, with placeholder fallback when model credentials or vision calls are unavailable.
- Next: run end-to-end tests with a real uploaded image URL after model credentials are configured, then add profile/event persistence so visible concerns such as `点状斑点` and `面部色沉` are remembered in later turns.

## Current Gap From Previous Coze Flow

The local LangGraph service is now usable for a minimal local chat loop, but it is not yet feature-equivalent with the previous Coze workflow.

Already covered locally:

- Local FastAPI `/chat` entry compatible with the existing frontend shape.
- Trace logs for graph nodes and tool calls.
- Coze OAuth wrapper and unified KB search wrapper.
- First-pass multi-intent planning with up to three intents.
- Price consultation through `project_price`, pricing DB, and local Excel fallback.
- Trust asset retrieval with image URL extraction.
- Project consultation through `project_qa`.
- Competitor response through `competitor_qa`, using allowed replies, forbidden expressions, required collection, and next action.
- After-sales consultation through `after_sales_qa`, using slice fields such as risk level, allowed reply, required collection, and next action.
- Basic image understanding node with optional vision model.
- Image information now feeds routing, project replies, profile updates, and event facts when `image_info.visible_concerns` is available.
- Local profile/event extraction and memory persistence.
- Local customer/appointment context placeholder, read every turn from development memory.
- Local store/address/parking placeholder through `StoreService`, with structured results in trace.
- Frontend debug panel for request trace and metadata.

Still missing or incomplete:

- Appointment/customer-system integration is still a placeholder. The graph now has a replaceable `CustomerContextService`, but existing appointments are not yet pulled from the real customer system every round.
- Store matching/address/parking now has a local typed placeholder, but still needs replacement with the real store APIs.
- Competitor response can consume `competitor_qa`, but still needs richer quote-image OCR and structured competitor comparison when customers send screenshots.
- After-sales can consume `after_sales_qa` and escalate obvious severe terms, but has not yet implemented complete order binding or dedicated nursing workflow handoff.
- Campaign/activity flow is not complete because the activity KB/API is not currently part of the tool layer.
- Final reply synthesis is still partly deterministic. It needs more systematic global planning so it can answer multiple current needs while naturally advancing toward project intent and appointment.
- Profile/event extraction is local and heuristic; later it should be upgraded or backed by the customer system database.
- Vision model calls require model credentials in environment variables; without them the graph returns a stable fallback `image_info` and keeps the flow alive.
- Model routing exists as a policy, but not every node has final model selection and prompt contracts implemented.
- Full regression suite is not built yet. Current checks are smoke tests.

Customer context replacement point:

- Current implementation: `app.services.customer_context.CustomerContextService`.
- Current source: local memory only, used as a development placeholder.
- Future source: real customer system/customer database and appointment APIs.
- Required output shape should keep `appointment.has_active`, `appointment.status`, `appointment.store_id`, `appointment.store_name`, `appointment.appointment_time`, and `appointment.summary` so graph nodes and frontend trace do not need large changes.

Store replacement point:

- Current implementation: `app.services.store_service.StoreService`.
- Current source: local hard-coded development store data for smoke testing.
- Future source: real store list/search/detail APIs.
- Required output shape should keep `stores[].id`, `stores[].name`, `stores[].city`, `stores[].address`, `stores[].map_url`, `stores[].parking_name`, `stores[].parking_address`, and `stores[].business_hours`.
- The graph consumes this result as `tool_results.store_lookup`, so replacing the service should not require reply-planner changes.

## Current Round Goal

Focus: improve global planning stability before adding more tools.

Why:

- The old Coze flow tended to lock into one subflow.
- The local LangGraph flow can combine intents, but naive keyword merging can over-expand a simple request, e.g. `热玛吉多少钱` becoming both price and project consultation.
- Before adding appointment/store/customer-system tools, the planner must keep simple turns simple and reserve multi-intent replies for genuinely multi-intent messages.

Acceptance for this round:

- `项目名 + 多少钱/价格` should route as price only unless the customer also asks effect, suitability, recommendation, or plan.
- Genuine project questions such as `点状斑适合做什么项目` should still trigger project consultation.
- Mixed turns such as `点状斑想淡斑，皮秒多少钱，你们正规吗` should still keep price + trust + project guidance.
- Replies should answer the customer’s current problem first, use known facts directly, avoid exposing AI/tool process, and avoid unnecessary deferral.

## Latest Update: Image + Price + Trust Mixed Turns

Status: completed for the local deterministic path.

Changes:

- Image understanding results can now be reused as inline context in price and trust replies.
- If a turn contains an uploaded face image plus price/trust questions, the final reply no longer drops visible concerns such as `点状斑点` or `片状色沉`.
- Turns with uploaded images now skip model-based final synthesis and use deterministic reply composition first, because model synthesis previously dropped the price answer in a mixed image + trust + price test.
- Price/campaign turns also skip model-based final synthesis so factual price boundaries are preserved.

Verified smoke cases:

- `这个做皮秒多少钱，你们正规吗` with a face image:
  - detected image, trust, and price intents
  - returned trust text, trust asset image, and a price boundary
  - preserved image facts: `点状斑点`, `片状色沉`
  - did not invent a skin diagnosis or a missing `皮秒` price
- `这个正规吗` with a face image:
  - returned trust reply and qualification image
  - also mentioned visible image concerns and the broad淡斑判断维度

Reply quality judgment:

- The current reply solves all visible customer questions in the mixed turn.
- It avoids exposing AI/tool process.
- It uses `小美` persona.
- It avoids inventing price when `project_price` has no `皮秒` record.
- Remaining polish: first mixed-turn trust sentence is slightly long; later final reply synthesizer can split it more naturally while keeping deterministic fact coverage.

Follow-up refinement:

- Mixed image + trust + price replies now keep trust text shorter, then attach the trust asset image, then answer price with the image-based project context.
- Intent ordering now uses business priority instead of raw detection order, so image uploads do not force the main route label to `image_inquiry` when the customer is mainly asking about trust or price.
- Regression check: `热玛吉多少钱` still routes to `price_inquiry / SF7_price_consult` and returns one concise price answer.

## Latest Update: Platform Agent API Adapters

Status: wired with safe local fallback.

Changes:

- Added `PlatformAgentClient` as the typed wrapper for the current WeCom platform APIs.
- Added env-based configuration for platform base URL, token, request source, and timeout.
- `CustomerContextService` now uses `customer/get_customer_info` and `order/index` when `PLATFORM_AGENT_TOKEN` plus `external_userid` are available.
- `StoreService` now uses `store/index` and `store/info` when customer context contains the platform customer ID and add-wechat ID.
- Existing local customer-memory and store placeholders remain as fallback when the platform token is absent or an API call fails.
- Frontend `/api/chat` now forwards `corp_id`, `user_id`, `wechat`, and `external_userid` to the local FastAPI service.

Verified:

- Python compile passed.
- Backend health check passed.
- Store inquiry through the frontend proxy still returns the 厦门思明店 parking/address reply with local fallback.
- Forwarded request context now reaches backend meta.

Remaining:

- `PLATFORM_AGENT_TOKEN` is not configured in local `.env`, so real platform calls were not executed in this smoke test.
- `available_time` is wrapped in the client but not yet used by the appointment reply path.

Follow-up refinement:

- Appointment actions now resolve store + date and call `available_time` through the service layer.
- If the platform token is missing or the API fails, the reply keeps a safe customer-facing fallback instead of inventing open slots.
- Smoke check: `厦门思明店周六能约吗` routes to `appointment_intent / SF9_appointment`, resolves the local store, parses the next Saturday date, and records an `available_time` tool result.
