import assert from "node:assert/strict";
import test from "node:test";

import {
  replyMessages,
  runDisplayStatus,
  workflowNodesForDisplay,
  type ObservabilityView,
  type RunItem,
} from "./run-log-model";


function run(overrides: Partial<RunItem> = {}): RunItem {
  return {
    request_id: "run-1",
    runtime_status: "completed",
    business_summary: { response_kind: "business_reply" },
    ...overrides,
  };
}


test("terminal no-reply kinds are not rendered as ordinary success", () => {
  assert.equal(
    runDisplayStatus(run({ business_summary: { response_kind: "human_takeover" } })),
    "human_takeover",
  );
  assert.equal(
    runDisplayStatus(run({ business_summary: { response_kind: "protocol_filtered" } })),
    "protocol_filtered",
  );
  assert.equal(
    runDisplayStatus(run({ business_summary: { response_kind: "superseded" } })),
    "superseded",
  );
  assert.equal(
    runDisplayStatus(run({ business_summary: { response_kind: "failure_fallback" } })),
    "failure_fallback",
  );
});


test("stale interrupted runs and legacy reply sources retain their real status", () => {
  assert.equal(runDisplayStatus(run({ runtime_status: "interrupted" })), "interrupted");
  assert.equal(
    runDisplayStatus(run({
      business_summary: undefined,
      output_snapshot: { reply_source: "human_takeover_guard" },
    })),
    "human_takeover",
  );
  assert.equal(
    runDisplayStatus(run({
      business_summary: { response_kind: "business_reply" },
      output_snapshot: { reply_source: "platform_filtered" },
    })),
    "protocol_filtered",
  );
  assert.equal(
    runDisplayStatus(run({
      business_summary: undefined,
      output_snapshot: { reply_source: "takeover_status_unavailable_fallback" },
    })),
    "failure_fallback",
  );
});


test("historical public HTTP snapshots expose nested reply messages", () => {
  const messages = [{ type: "text", order: 1, content: { text: "您稍等一下" } }];
  assert.deepEqual(
    replyMessages({ http_response_body: { data: { reply_messages: messages } } }),
    messages,
  );
});


test("missing delivery evidence is displayed as not recorded", () => {
  const view = {
    delivery: { status: "", expected_count: 0, succeeded_count: 0, failed_count: 0, dispatches: [] },
    workflow_nodes: [{
      key: "delivery",
      label: "发送与平台回执",
      purpose: "区分生成与送达",
      status: "success",
      summary: "",
      node_ids: [],
      node_names: [],
      duration_ms: 0,
    }],
  } as unknown as ObservabilityView;

  assert.deepEqual(workflowNodesForDisplay(view)[0], {
    ...view.workflow_nodes?.[0],
    status: "not_recorded",
    summary: "未记录异步发送回执",
  });
});
