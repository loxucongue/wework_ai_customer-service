export type JsonValue = unknown;

export type BusinessSummary = {
  wechat?: string;
  intent_code?: string;
  emotion_code?: string;
  checkpoint_code?: string;
  checkpoint_name?: string;
  sequence_matched?: boolean;
  sequence_adopted?: boolean;
  script_adopted?: boolean;
  decision_status?: string;
  fallback_used?: boolean;
  delivery_status?: string;
  usage_event_recorded?: boolean;
};

export type CustomerIdentity = {
  request_id?: string;
  conversation_id?: string;
  customer_id?: string;
  platform_customer_id?: string;
  customer_add_wechat_id?: string;
  external_userid?: string;
  corp_id?: string;
  user_id?: string;
  wechat?: string;
};

export type RunItem = {
  request_id: string;
  interface_version?: string;
  conversation_id?: string;
  customer_id?: string;
  input_snapshot?: Record<string, JsonValue>;
  output_snapshot?: Record<string, JsonValue>;
  business_summary?: BusinessSummary;
  intents?: JsonValue[];
  tags?: string[];
  duration_ms?: number;
  token_usage?: Record<string, JsonValue>;
  error?: string;
  created_at?: string;
  started_at?: string;
  finished_at?: string;
  runtime_status?: string;
  runtime_phase?: string;
};

export type ImportantField = { key: string; label: string; value: JsonValue };

export type ObservableModelCall = {
  id: string;
  node_name: string;
  name: string;
  tier: string;
  model: string;
  configured_model: string;
  duration_ms: number;
  total_tokens: number;
  attempts: number;
  hedge_started: boolean;
  fallback_used: boolean;
  timeout_stage: string;
  error: string;
  prompt_messages: Array<{ role: string; chars: number; preview: string }>;
};

export type ObservableToolCall = {
  name: string;
  status: string;
  duration_ms: number;
  input_summary: JsonValue;
  output_summary: JsonValue;
  error: string;
};

export type ObservableNode = {
  id: string;
  sequence: number;
  node_name: string;
  node_kind: string;
  display_name: string;
  status: string;
  duration_ms: number;
  started_at: string;
  finished_at: string;
  parallel_group: string;
  summary: string[];
  important_inputs: ImportantField[];
  important_outputs: ImportantField[];
  model_calls: ObservableModelCall[];
  tool_calls: ObservableToolCall[];
  warnings: string[];
  errors: string[];
};

export type Evidence = { ref?: string; quote?: string };

export type DecisionSummary = {
  available?: boolean;
  decision_status?: string;
  decision_reasons?: string[];
  primary_task?: { code?: string; name?: string; goal?: string; basis?: string[] };
  intent?: {
    code?: string;
    name?: string;
    confidence?: string;
    secondary_codes?: string[];
    secondary_names?: string[];
    evidence?: Evidence[];
    basis?: string[];
  };
  emotion?: {
    code?: string;
    name?: string;
    confidence?: string;
    pressure?: string;
    flow_action?: string;
    evidence?: Evidence[];
    basis?: string[];
  };
  closing?: {
    action?: string;
    action_name?: string;
    customer_state?: string;
    pressure?: string;
    trigger?: string;
    rule_id?: string;
    rule_name?: string;
    sequence_key?: string;
    sequence_name?: string;
    node_key?: string;
    node_name?: string;
    rule_match_status?: string;
    constraint_status?: string;
    constraint_reasons?: string[];
    evidence?: Evidence[];
    basis?: string[];
  };
};

export type CheckpointSummary = {
  router?: {
    retrieval_goal?: { summary?: string; evidence?: Evidence[] };
    classification_status?: string;
    primary?: { type_id?: number; code?: string; name?: string; tag_id?: number; tag_name?: string };
    secondary?: { type_id?: number; code?: string; name?: string; tag_id?: number; tag_name?: string };
    evidence?: Evidence[];
    reason?: string;
  };
  final?: {
    available?: boolean;
    category_key?: string;
    scenario?: string;
    state?: string;
    confidence?: string;
    tactic_tags?: string[];
    evidence?: Evidence[];
    basis?: string[];
  };
};

export type MatchedSequence = {
  rank?: number;
  sequence_id?: string;
  sequence_name?: string;
  checkpoint_code?: string;
  checkpoint_name?: string;
  alternative?: boolean;
  adopted?: boolean;
  selection_reason?: string;
  steps?: Array<{
    step_id?: string;
    sort_order?: number;
    action_code?: string;
    action_name?: string;
    adopted?: boolean;
  }>;
};

export type ScriptCandidate = {
  script_id?: string;
  script_code?: string;
  script_name?: string;
  checkpoint_type_name?: string;
  checkpoint_tag_name?: string;
  action_code?: string;
  action_name?: string;
  text_preview?: string;
  adopted?: boolean;
  delivered?: boolean;
};

export type KnowledgeMatch = {
  available?: boolean;
  execution?: {
    router_invoked?: boolean;
    router_status?: string;
    sequence_index_count?: number;
    knowledge_status?: string;
    script_lookup_invoked?: boolean;
    script_lookup_count?: number;
    selector_invoked?: boolean;
  };
  sequence_reason?: string;
  selector?: { status?: string; reason?: string };
  matched_sequences?: MatchedSequence[];
  script_candidate_count?: number;
  script_candidates?: ScriptCandidate[];
  adopted?: {
    sequence_id?: string;
    sequence_name?: string;
    step_id?: string;
    checkpoint_code?: string;
    action_code?: string;
    script_ids?: string[];
    reason?: string;
  };
  delivered_content_ids?: string[];
  adoption_explanation?: { sequence?: string; script?: string };
};

export type WorkflowNode = {
  key: string;
  label: string;
  purpose: string;
  status: string;
  summary: string;
  node_ids: string[];
  node_names: string[];
  duration_ms: number;
};

export type DeliveryDispatch = {
  dispatch_id: string;
  source_channel: string;
  source_kind: string;
  status: string;
  expected_count: number;
  succeeded_count: number;
  failed_count: number;
  platform_request_id: string;
  error_code: string;
  error_message: string;
  submitted_at: string;
  confirmed_at: string;
  last_callback_at: string;
  items: Array<{
    message_index: number;
    message_type: string;
    status: string;
    platform_message_id: string;
    error_code: string;
    error_message: string;
    sent_at: string;
  }>;
};

export type ObservabilityView = {
  contract_version: string;
  summary: {
    status: string;
    request_id: string;
    created_at: string;
    interface_version: string;
    reply_chain_mode: string;
    message_type: string;
    customer_message: string;
    wall_duration_ms: number;
    recorded_duration_ms: number;
    graph_duration_ms?: number;
    slowest_node: { node_name: string; display_name: string; duration_ms: number };
    model_call_count: number;
    model_retry_count: number;
    model_fallback_count: number;
    total_tokens: number;
    model_names?: string[];
    fallback_detected: boolean;
    error_count: number;
    warning_count: number;
    errors: JsonValue[];
    warnings: JsonValue[];
    final_messages: JsonValue[];
  };
  nodes: ObservableNode[];
  delivery: {
    status: string;
    expected_count: number;
    succeeded_count: number;
    failed_count: number;
    dispatches: DeliveryDispatch[];
  };
  customer_identity?: CustomerIdentity;
  decision_summary?: DecisionSummary;
  checkpoint_summary?: CheckpointSummary;
  knowledge_match?: KnowledgeMatch;
  workflow_nodes?: WorkflowNode[];
  store_workflow?: Record<string, JsonValue>;
  data_availability?: {
    business_summary?: string;
    customer_identity?: string;
    strategy_usage_event?: string;
    node_traces?: string;
    raw_detail?: string;
    trace_retention_days?: number;
    snapshot_compacted?: boolean;
    notice?: string;
  };
};

export type RunDetail = { run?: RunItem; observability_view?: ObservabilityView };

export type NodeDetail = {
  node?: ObservableNode;
  trace?: {
    input_snapshot?: JsonValue;
    output_snapshot?: JsonValue;
    tool_calls?: JsonValue[];
    [key: string]: JsonValue;
  };
  data_availability?: { status?: string; snapshot_compacted?: boolean; notice?: string };
};

export type Filters = {
  request_id: string;
  limit: string;
  customer_id: string;
  conversation_id: string;
  started_from: string;
  started_to: string;
  wechat: string;
  run_status: string;
  intent_code: string;
  emotion_code: string;
  checkpoint_code: string;
  decision_status: string;
  sequence_matched: string;
  sequence_adopted: string;
  script_adopted: string;
  node_failed: string;
};

export const INTENT_LABELS: Record<string, string> = {
  fact_inquiry: "咨询事实",
  blocker_expression: "表达卡点",
  transaction_progress: "推进成交",
  information_submission: "提交信息",
  defer: "暂缓",
  explicit_exit: "明确退出",
  normal_exchange: "普通交流",
};

export const EMOTION_LABELS: Record<string, string> = {
  enthusiastic: "热情",
  curious: "好奇",
  neutral: "中性",
  hesitant: "犹豫",
  cold: "冷淡",
  defensive: "防备",
  impatient: "不耐烦",
  angry: "愤怒",
};

export const STATUS_META: Record<string, { label: string; className: string }> = {
  delivered: { label: "已确认送达", className: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  send_succeeded: { label: "发送成功", className: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  success: { label: "处理成功", className: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  completed: { label: "已完成", className: "bg-emerald-50 text-emerald-700 ring-emerald-200" },
  warning: { label: "有警告", className: "bg-amber-50 text-amber-800 ring-amber-200" },
  degraded: { label: "已降级", className: "bg-amber-50 text-amber-800 ring-amber-200" },
  fallback: { label: "异常兜底", className: "bg-amber-50 text-amber-800 ring-amber-200" },
  delivery_pending: { label: "等待回执", className: "bg-blue-50 text-blue-700 ring-blue-200" },
  pending: { label: "处理中", className: "bg-blue-50 text-blue-700 ring-blue-200" },
  partial_failed: { label: "部分发送失败", className: "bg-red-50 text-red-700 ring-red-200" },
  delivery_failed: { label: "发送失败", className: "bg-red-50 text-red-700 ring-red-200" },
  send_failed: { label: "发送失败", className: "bg-red-50 text-red-700 ring-red-200" },
  failed: { label: "失败", className: "bg-red-50 text-red-700 ring-red-200" },
  skipped: { label: "无需执行", className: "bg-zinc-100 text-zinc-600 ring-zinc-200" },
  not_reached: { label: "未到达", className: "bg-red-50 text-red-700 ring-red-200" },
  expired: { label: "轨迹已过期", className: "bg-zinc-100 text-zinc-500 ring-zinc-200" },
  not_recorded: { label: "未记录", className: "bg-zinc-100 text-zinc-500 ring-zinc-200" },
  running: { label: "处理中", className: "bg-blue-50 text-blue-700 ring-blue-200" },
};

export function isRecord(value: JsonValue): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function stringField(value: JsonValue) {
  if (value === null || value === undefined) return "";
  if (["string", "number", "boolean"].includes(typeof value)) return String(value);
  return "";
}

export function formatDuration(value?: number | null) {
  if (value === null || value === undefined) return "-";
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)}s`;
}

export function formatTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function formatJson(value: JsonValue) {
  try { return JSON.stringify(value ?? {}, null, 2); } catch { return String(value ?? ""); }
}

export function displayValue(value: JsonValue): string {
  if (value === null || value === undefined || value === "") return "未记录";
  if (["string", "number", "boolean"].includes(typeof value)) return String(value);
  if (Array.isArray(value)) return value.map(displayValue).join("、") || "未记录";
  return formatJson(value);
}

export function replyMessages(record?: Record<string, JsonValue>) {
  if (!record) return [];
  for (const path of [
    ["reply_control", "async_final", "reply_messages"],
    ["async_final_reply", "reply_messages"],
    ["http_response_reply_messages"],
    ["http_response_body", "reply_messages"],
    ["reply_messages"],
  ]) {
    let current: JsonValue = record;
    for (const key of path) current = isRecord(current) ? current[key] : undefined;
    if (Array.isArray(current)) return current;
  }
  return [];
}

export function contentString(value: JsonValue, preferred = "text"): string {
  if (value === null || value === undefined) return "";
  if (["string", "number", "boolean"].includes(typeof value)) return String(value);
  if (isRecord(value)) {
    return stringField(value[preferred]) || stringField(value.text) || stringField(value.url) || stringField(value.store_id) || stringField(value.id);
  }
  return "";
}

export function runContent(run: RunItem) {
  return stringField(run.input_snapshot?.content) || stringField(run.input_snapshot?.current_message) || "无文本输入";
}

export function runReply(run: RunItem) {
  return replyMessages(run.output_snapshot).map((item) => contentString(isRecord(item) ? item.content : item)).filter(Boolean).join(" / ");
}

export function isRunning(run: RunItem) {
  return run.runtime_status === "running";
}

export function runtimePhaseLabel(phase?: string) {
  return ({ request_received: "请求已接收", full: "主链处理中", commit: "提交中", completed: "已完成" } as Record<string, string>)[phase || ""] || phase || "处理中";
}
