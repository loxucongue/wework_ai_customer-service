"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronRight,
  CircleDashed,
  Database,
  ExternalLink,
  Image as ImageIcon,
  MapPin,
  RefreshCw,
  Search,
  Send,
  TriangleAlert,
  WalletCards,
  Wrench,
  XCircle,
} from "lucide-react";

type JsonValue = unknown;

type RunItem = {
  request_id: string;
  interface_version?: string;
  conversation_id?: string;
  customer_id?: string;
  input_snapshot?: Record<string, JsonValue>;
  output_snapshot?: Record<string, JsonValue>;
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

type NodeTrace = {
  id?: string;
  node_name?: string;
  node?: string;
  input_snapshot?: Record<string, JsonValue>;
  output_snapshot?: Record<string, JsonValue>;
  tool_calls?: JsonValue[];
  duration_ms?: number;
  error?: string;
  created_at?: string;
};

type ImportantField = {
  key: string;
  label: string;
  value: JsonValue;
};

type ObservableModelCall = {
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

type ObservableToolCall = {
  name: string;
  status: string;
  duration_ms: number;
  input_summary: JsonValue;
  output_summary: JsonValue;
  error: string;
};

type ObservableNode = {
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

type DeliveryItem = {
  message_index: number;
  message_type: string;
  status: string;
  platform_message_id: string;
  error_code: string;
  error_message: string;
  sent_at: string;
};

type DeliveryDispatch = {
  dispatch_id: string;
  source_channel: string;
  source_kind: string;
  status: string;
  expected_count: number;
  succeeded_count: number;
  failed_count: number;
  platform_request_id: string;
  system_msgid: string;
  error_code: string;
  error_message: string;
  submitted_at: string;
  confirmed_at: string;
  last_callback_at: string;
  items: DeliveryItem[];
};

type ObservabilityView = {
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
    slowest_node: { node_name: string; display_name: string; duration_ms: number };
    model_call_count: number;
    model_retry_count: number;
    model_fallback_count: number;
    total_tokens: number;
    fallback_detected: boolean;
    error_count: number;
    warning_count: number;
    errors: JsonValue[];
    warnings: JsonValue[];
    final_messages: JsonValue[];
    http_response_messages: JsonValue[];
    async_final_messages: JsonValue[];
  };
  nodes: ObservableNode[];
  delivery: {
    status: string;
    expected_count: number;
    succeeded_count: number;
    failed_count: number;
    dispatches: DeliveryDispatch[];
  };
  debug: { snapshot_is_compacted: boolean; snapshot_label: string };
};

type RunDetail = {
  run?: RunItem;
  node_traces?: NodeTrace[];
  raw_log?: JsonValue;
  message_dispatches?: JsonValue[];
  observability_view?: ObservabilityView;
};

type Filters = {
  request_id: string;
  limit: string;
  customer_id: string;
  conversation_id: string;
  has_error: string;
};

type RawModelCall = {
  id: string;
  node: string;
  name: string;
  tier: string;
  model: string;
  durationMs: number | null;
  totalTokens: number;
  input: JsonValue;
  output: JsonValue;
  usage: JsonValue;
  error: string;
  hedgeStarted: boolean;
  attempts: number;
  timeoutStage: string;
};

const DEFAULT_FILTERS: Filters = {
  request_id: "",
  limit: "50",
  customer_id: "",
  conversation_id: "",
  has_error: "",
};

const STATUS_META: Record<string, { label: string; className: string }> = {
  delivered: { label: "已确认送达", className: "bg-emerald-100 text-emerald-800" },
  success: { label: "处理成功", className: "bg-emerald-100 text-emerald-800" },
  warning: { label: "成功但有警告", className: "bg-amber-100 text-amber-800" },
  fallback: { label: "异常兜底", className: "bg-amber-100 text-amber-900" },
  delivery_pending: { label: "等待发送回调", className: "bg-blue-100 text-blue-800" },
  pending: { label: "等待回调", className: "bg-blue-100 text-blue-800" },
  partial_failed: { label: "部分发送失败", className: "bg-red-100 text-red-800" },
  delivery_failed: { label: "发送失败", className: "bg-red-100 text-red-800" },
  failed: { label: "请求失败", className: "bg-red-100 text-red-800" },
  skipped: { label: "已跳过", className: "bg-slate-200 text-slate-700" },
  not_recorded: { label: "未记录发送回调", className: "bg-slate-200 text-slate-700" },
  send_succeeded: { label: "发送成功", className: "bg-emerald-100 text-emerald-800" },
  send_failed: { label: "发送失败", className: "bg-red-100 text-red-800" },
  platform_accepted: { label: "平台已接受", className: "bg-blue-100 text-blue-800" },
};

export function RunLogViewer() {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [runs, setRuns] = useState<RunItem[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [nowMs, setNowMs] = useState(() => Date.now());

  const selectedRun = useMemo(
    () => detail?.run || runs.find((item) => item.request_id === selectedId) || null,
    [detail, runs, selectedId]
  );

  const loadRuns = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    setError("");
    const directRequestId = filters.request_id.trim();
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value && key !== "request_id") search.set(key, value);
    }
    try {
      if (directRequestId) {
        const response = await fetch(`/api/logs/runs?request_id=${encodeURIComponent(directRequestId)}`, { cache: "no-store" });
        const data = await readJsonResponse(response, "按请求 ID 查询失败");
        if (!response.ok) throw new Error(errorMessage(data, "按请求 ID 查询失败"));
        const directRun = isRecord(data.run) ? (data.run as RunItem) : null;
        if (!directRun?.request_id) throw new Error(`没有找到请求 ${directRequestId}`);
        setRuns([directRun]);
        setSelectedId(directRun.request_id);
        setDetail(data as RunDetail);
        return;
      }
      const response = await fetch(`/api/logs/runs?${search.toString()}`, { cache: "no-store" });
      const data = await readJsonResponse(response, "加载日志失败");
      if (!response.ok) throw new Error(errorMessage(data, "加载日志失败"));
      const items = Array.isArray(data?.items) ? (data.items as RunItem[]) : [];
      setRuns(items);
      setSelectedId((current) => current || items[0]?.request_id || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载日志失败");
    } finally {
      if (!silent) setLoading(false);
    }
  }, [filters]);

  const loadDetail = useCallback(async (requestId: string) => {
    if (!requestId) return;
    setDetailLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/logs/runs?request_id=${encodeURIComponent(requestId)}`, {
        cache: "no-store",
      });
      const data = await readJsonResponse(response, "加载详情失败");
      if (!response.ok) throw new Error(errorMessage(data, "加载详情失败"));
      setDetail(data as RunDetail);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载详情失败");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadRuns();
  }, [loadRuns]);

  useEffect(() => {
    if (selectedId) void loadDetail(selectedId);
  }, [loadDetail, selectedId]);

  useEffect(() => {
    const interval = window.setInterval(() => {
      setNowMs(Date.now());
      void loadRuns(true);
      if (selectedId && runs.some((run) => run.request_id === selectedId && isRunning(run))) {
        void loadDetail(selectedId);
      }
    }, 2000);
    return () => window.clearInterval(interval);
  }, [loadDetail, loadRuns, runs, selectedId]);

  return (
    <main className="flex h-screen flex-col bg-slate-50 text-slate-950 lg:flex-row">
      <aside className="flex max-h-[46vh] w-full min-w-0 flex-col border-b bg-white lg:max-h-none lg:w-[370px] lg:min-w-[330px] lg:border-b-0 lg:border-r">
        <header className="border-b px-4 py-3">
          <div className="flex items-center justify-between gap-3">
            <h1 className="flex items-center gap-2 text-lg font-semibold">
              <Database className="h-5 w-5" />
              AI 回复运行日志
            </h1>
            <button
              type="button"
              onClick={() => void loadRuns()}
              className="inline-flex h-9 w-9 items-center justify-center rounded-md bg-slate-950 text-white disabled:opacity-60"
              title="刷新"
              disabled={loading}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
          <p className="mt-1 text-xs text-slate-500">节点事实、处理结果、耗时与最终发送状态</p>
        </header>

        <section className="border-b px-4 py-3">
          <div className="grid grid-cols-2 gap-x-3 gap-y-2">
            <div className="col-span-2">
              <FilterInput label="请求 ID" value={filters.request_id} onChange={(value) => setFilters((prev) => ({ ...prev, request_id: value }))} />
            </div>
            <FilterInput label="客户 ID" value={filters.customer_id} onChange={(value) => setFilters((prev) => ({ ...prev, customer_id: value }))} />
            <FilterInput label="会话 ID" value={filters.conversation_id} onChange={(value) => setFilters((prev) => ({ ...prev, conversation_id: value }))} />
            <FilterInput label="数量" value={filters.limit} onChange={(value) => setFilters((prev) => ({ ...prev, limit: value }))} />
            <label className="text-xs font-medium text-slate-600">
              运行错误
              <select
                value={filters.has_error}
                onChange={(event) => setFilters((prev) => ({ ...prev, has_error: event.target.value }))}
                className="mt-1 h-8 w-full rounded-md border px-2 text-sm"
              >
                <option value="">全部</option>
                <option value="true">只看错误</option>
                <option value="false">只看正常</option>
              </select>
            </label>
          </div>
          <button
            type="button"
            onClick={() => void loadRuns()}
            className="mt-2 inline-flex h-8 w-full items-center justify-center gap-2 rounded-md border px-3 text-sm hover:bg-slate-50"
          >
            <Search className="h-4 w-4" />
            查询
          </button>
          {error ? (
            <div className="mt-3 flex gap-2 rounded-md border border-red-200 bg-red-50 p-2 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 shrink-0" />
              {error}
            </div>
          ) : null}
        </section>

        <section className="min-h-0 flex-1 overflow-y-auto">
          {runs.map((run) => (
            <RunListItem key={run.request_id} run={run} selected={selectedId === run.request_id} onSelect={setSelectedId} nowMs={nowMs} />
          ))}
          {!loading && runs.length === 0 ? <div className="p-6 text-sm text-slate-500">暂无运行日志。</div> : null}
        </section>
      </aside>

      <section className="min-h-0 min-w-0 flex-1 overflow-y-auto p-3 sm:p-5 xl:p-6">
        {selectedRun ? (
          <RunDetailPanel run={selectedRun} detail={detail} loading={detailLoading} nowMs={nowMs} />
        ) : (
          <div className="border bg-white p-8 text-sm text-slate-500">请选择一条运行日志。</div>
        )}
      </section>
    </main>
  );
}

function FilterInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="text-xs font-medium text-slate-600">
      {label}
      <input value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 h-8 w-full rounded-md border px-2 text-sm" />
    </label>
  );
}

function RunListItem({ run, selected, onSelect, nowMs }: { run: RunItem; selected: boolean; onSelect: (id: string) => void; nowMs: number }) {
  const context = isRecord(run.input_snapshot?.request_context) ? run.input_snapshot?.request_context : {};
  const version = stringField(run.interface_version) || stringField(context?.interface_version) || stringField(context?.api_version) || "v1";
  const versionClassName =
    version.toLowerCase() === "v3"
      ? "bg-blue-100 text-blue-700"
      : version.toLowerCase() === "v2"
        ? "bg-emerald-100 text-emerald-700"
        : "bg-slate-200 text-slate-700";
  return (
    <button
      type="button"
      onClick={() => onSelect(run.request_id)}
      className={`w-full border-b p-4 text-left hover:bg-slate-50 ${selected ? "bg-slate-100" : "bg-white"}`}
    >
      <div className="flex items-center justify-between gap-3">
        <span className="truncate font-mono text-xs text-slate-500">{run.request_id}</span>
        <span className="shrink-0 text-xs text-slate-500">{formatTime(run.created_at)}</span>
      </div>
      <div className="mt-2 line-clamp-2 text-sm font-medium">{contentSnippet(run)}</div>
      {replySnippet(run) ? <div className="mt-1 line-clamp-2 text-xs leading-relaxed text-slate-500">{replySnippet(run)}</div> : null}
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <span className={`rounded px-1.5 py-0.5 text-xs ${versionClassName}`}>{version}</span>
        <RuntimeStatusBadge run={run} />
        <span className="text-xs text-slate-500">{isRunning(run) ? `已耗时 ${formatDuration(runDurationMs(run, nowMs))}` : `总耗时 ${formatDuration(run.duration_ms)}`}</span>
        {run.error ? <StatusBadge status="failed" compact /> : null}
      </div>
    </button>
  );
}

function RunDetailPanel({ run, detail, loading, nowMs }: { run: RunItem; detail: RunDetail | null; loading: boolean; nowMs: number }) {
  const observability = detail?.observability_view || legacyObservability(run, detail?.node_traces || []);
  const nodes = observability.nodes;
  const [selectedNodeId, setSelectedNodeId] = useState("");

  useEffect(() => {
    if (!nodes.length) {
      setSelectedNodeId("");
      return;
    }
    setSelectedNodeId((current) => (nodes.some((node) => node.id === current) ? current : nodes[0].id));
  }, [nodes]);

  const selectedNode = nodes.find((node) => node.id === selectedNodeId) || nodes[0];
  const selectedTrace = findTraceForNode(detail?.node_traces || [], selectedNode);
  const rawModelCalls = useMemo(() => collectRawModelCalls(detail?.node_traces || []), [detail?.node_traces]);

  return (
    <div className="space-y-5">
      <Overview run={run} view={observability} loading={loading} nowMs={nowMs} />

      <section className="border bg-white">
        <div className="border-b px-5 py-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h3 className="font-semibold">执行链路</h3>
              <p className="mt-1 text-sm text-slate-500">选择节点查看关键输入、输出、模型、工具和异常恢复。</p>
            </div>
            <span className="text-sm text-slate-500">{loading ? "加载中..." : `${nodes.length} 个节点`}</span>
          </div>
        </div>
        <div className="grid min-h-[520px] lg:grid-cols-[340px_minmax(0,1fr)]">
          <ExecutionTimeline nodes={nodes} selectedId={selectedNode?.id || ""} onSelect={setSelectedNodeId} />
          {selectedNode ? (
            <NodeInspector node={selectedNode} trace={selectedTrace} />
          ) : (
            <div className="p-6 text-sm text-slate-500">没有记录到节点轨迹。</div>
          )}
        </div>
      </section>

      <DeliveryPanel delivery={observability.delivery} />
      <ModelCallPanel calls={rawModelCalls} />

      <section className="border bg-white p-5">
        <details>
          <summary className="cursor-pointer text-sm font-semibold">开发者详情：调试快照（可能截断）</summary>
          <p className="mt-2 text-xs text-slate-500">该内容经过长度和字段数量压缩，不代表平台请求或模型上下文的无损原文。</p>
          <div className="mt-3 grid gap-3 xl:grid-cols-2">
            <Snapshot title="运行调试快照" value={detail?.raw_log || {}} tall />
            <Snapshot title="数据库节点轨迹" value={detail?.node_traces || []} tall />
          </div>
        </details>
      </section>
    </div>
  );
}

function Overview({ run, view, loading, nowMs }: { run: RunItem; view: ObservabilityView; loading: boolean; nowMs: number }) {
  const summary = view.summary;
  return (
    <section className="border bg-white">
      <div className="flex flex-wrap items-start justify-between gap-4 border-b px-5 py-4">
        <div>
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="font-mono text-sm font-semibold">{run.request_id}</h2>
            {isRunning(run) ? <RuntimeStatusBadge run={run} /> : <StatusBadge status={summary.status} />}
            <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-700">{summary.interface_version || "v1"}</span>
          </div>
          <p className="mt-2 text-sm text-slate-500">
            客户 {run.customer_id || "-"} · 会话 {run.conversation_id || "-"} · {formatTime(run.created_at)}
          </p>
        </div>
        {loading ? <RefreshCw className="h-4 w-4 animate-spin text-slate-400" /> : null}
      </div>

      <div className="grid border-b sm:grid-cols-3 xl:grid-cols-6">
        <Metric label={isRunning(run) ? "当前耗时" : "整轮耗时"} value={formatDuration(isRunning(run) ? runDurationMs(run, nowMs) : summary.wall_duration_ms)} detail="按墙钟时间" />
        <Metric label="最慢节点" value={formatDuration(summary.slowest_node?.duration_ms)} detail={summary.slowest_node?.node_name || "-"} />
        <Metric label="模型调用" value={`${summary.model_call_count} 次`} detail={`重试 ${summary.model_retry_count} / fallback ${summary.model_fallback_count}`} />
        <Metric label="Token" value={String(summary.total_tokens || 0)} detail="本轮模型合计" />
        <Metric label="警告" value={String(summary.warning_count || 0)} detail={summary.fallback_detected ? "命中异常兜底" : "节点与模型"} />
        <Metric label="发送结果" value={statusLabel(view.delivery.status)} detail={`${view.delivery.succeeded_count}/${view.delivery.expected_count || 0} 成功`} />
      </div>

      <div className="grid gap-0 xl:grid-cols-[minmax(280px,0.8fr)_minmax(420px,1.2fr)]">
        <div className="border-b p-5 xl:border-b-0 xl:border-r">
          <div className="mb-3 text-xs font-semibold uppercase text-slate-500">客户当前消息</div>
          <div className="rounded-md bg-slate-100 px-4 py-3 text-sm leading-relaxed">{summary.customer_message || "无文本内容"}</div>
          <div className="mt-3 flex flex-wrap gap-2 text-xs text-slate-500">
            <span>类型：{summary.message_type || "text"}</span>
            {summary.reply_chain_mode ? <span>链路：{summary.reply_chain_mode}</span> : null}
          </div>
          {summary.errors.length ? <IssueList title="运行错误" values={summary.errors} tone="error" /> : null}
          {summary.warnings.length ? <IssueList title="运行警告" values={summary.warnings} tone="warning" /> : null}
        </div>
        <div className="p-5">
          <div className="mb-3 flex items-center justify-between">
            <div className="text-xs font-semibold uppercase text-slate-500">客户最终收到</div>
            <StatusBadge status={view.delivery.status} compact />
          </div>
          <ReplyMessages messages={summary.final_messages} />
        </div>
      </div>
    </section>
  );
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return (
    <div className="border-b px-4 py-3 last:border-b-0 sm:border-r xl:border-b-0">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 truncate text-base font-semibold">{value}</div>
      <div className="mt-0.5 truncate text-xs text-slate-400" title={detail}>{detail}</div>
    </div>
  );
}

function ExecutionTimeline({ nodes, selectedId, onSelect }: { nodes: ObservableNode[]; selectedId: string; onSelect: (id: string) => void }) {
  return (
    <div className="border-b bg-slate-50 p-3 lg:border-b-0 lg:border-r">
      <div className="space-y-1">
        {nodes.map((node, index) => {
          const previousParallel = index > 0 ? nodes[index - 1].parallel_group : "";
          const showParallel = Boolean(node.parallel_group && node.parallel_group !== previousParallel);
          return (
            <div key={node.id}>
              {showParallel ? <div className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase text-blue-600">并行分支</div> : null}
              <button
                type="button"
                onClick={() => onSelect(node.id)}
                className={`grid w-full grid-cols-[24px_minmax(0,1fr)_auto] items-start gap-2 rounded-md px-3 py-3 text-left ${
                  selectedId === node.id ? "bg-white shadow-sm ring-1 ring-slate-200" : "hover:bg-white"
                }`}
              >
                <NodeStatusIcon status={node.status} />
                <span className="min-w-0">
                  <span className="block truncate text-sm font-medium">{node.display_name}</span>
                  <span className="mt-1 block line-clamp-1 text-xs text-slate-500">{node.summary?.[0] || node.node_name}</span>
                </span>
                <span className="flex items-center gap-1 text-xs text-slate-500">
                  {formatDuration(node.duration_ms)}
                  <ChevronRight className="h-3.5 w-3.5" />
                </span>
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function NodeInspector({ node, trace }: { node: ObservableNode; trace?: NodeTrace }) {
  return (
    <div className="min-w-0 p-5">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b pb-4">
        <div>
          <div className="flex items-center gap-2">
            <NodeStatusIcon status={node.status} />
            <h4 className="font-semibold">{node.display_name}</h4>
          </div>
          <div className="mt-2 font-mono text-xs text-slate-400">{node.node_name}</div>
        </div>
        <div className="text-right text-sm text-slate-500">
          <div>{formatDuration(node.duration_ms)}</div>
          <div className="mt-1 text-xs">{formatTime(node.started_at)}</div>
        </div>
      </div>

      <div className="py-4">
        <SectionTitle title="节点结论" />
        <ul className="space-y-1.5 text-sm leading-relaxed">
          {(node.summary || []).map((line, index) => <li key={index}>· {line}</li>)}
        </ul>
      </div>

      <div className="grid gap-5 border-t py-4 xl:grid-cols-2">
        <FieldList title="关键输入" values={node.important_inputs} empty="该节点未记录可读输入摘要" />
        <FieldList title="关键输出" values={node.important_outputs} empty="该节点未记录可读输出摘要" />
      </div>

      {node.model_calls.length ? (
        <div className="border-t py-4">
          <SectionTitle title={`模型调用（${node.model_calls.length}）`} icon={<Bot className="h-4 w-4" />} />
          <div className="space-y-2">
            {node.model_calls.map((call) => <ModelSummary key={call.id} call={call} />)}
          </div>
        </div>
      ) : null}

      {node.tool_calls.length ? (
        <div className="border-t py-4">
          <SectionTitle title={`工具调用（${node.tool_calls.length}）`} icon={<Wrench className="h-4 w-4" />} />
          <div className="space-y-2">
            {node.tool_calls.map((call, index) => <ToolSummary key={`${call.name}-${index}`} call={call} />)}
          </div>
        </div>
      ) : null}

      {node.errors.length ? <IssueList title="节点错误" values={node.errors} tone="error" /> : null}
      {node.warnings.length ? <IssueList title="节点警告与恢复" values={node.warnings} tone="warning" /> : null}

      <details className="mt-4 border-t pt-4">
        <summary className="cursor-pointer text-sm font-medium text-slate-600">查看该节点调试快照</summary>
        <div className="mt-3 grid gap-3 xl:grid-cols-3">
          <Snapshot title="输入快照" value={trace?.input_snapshot || {}} />
          <Snapshot title="调用快照" value={trace?.tool_calls || []} />
          <Snapshot title="输出快照" value={trace?.output_snapshot || {}} />
        </div>
      </details>
    </div>
  );
}

function SectionTitle({ title, icon }: { title: string; icon?: React.ReactNode }) {
  return <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase text-slate-500">{icon}{title}</div>;
}

function FieldList({ title, values, empty }: { title: string; values: ImportantField[]; empty: string }) {
  return (
    <div>
      <SectionTitle title={title} />
      {values.length ? (
        <dl className="space-y-2">
          {values.map((item) => (
            <div key={item.key} className="grid grid-cols-[110px_minmax(0,1fr)] gap-3 text-sm">
              <dt className="text-slate-500">{item.label}</dt>
              <dd className="min-w-0 whitespace-pre-wrap break-words text-slate-800">{displayValue(item.value)}</dd>
            </div>
          ))}
        </dl>
      ) : <div className="text-sm text-slate-400">{empty}</div>}
    </div>
  );
}

function ModelSummary({ call }: { call: ObservableModelCall }) {
  return (
    <details className="rounded-md border bg-slate-50">
      <summary className="cursor-pointer px-3 py-2 text-sm">
        <span className="font-medium">{call.name}</span>
        <span className="ml-2 text-slate-500">{call.model || "未记录模型"}</span>
        <span className="ml-3 text-slate-500">{formatDuration(call.duration_ms)}</span>
        {call.attempts > 1 ? <span className="ml-2 text-amber-700">重试 {call.attempts}</span> : null}
        {call.fallback_used || call.hedge_started ? <span className="ml-2 text-amber-700">fallback</span> : null}
        {call.error ? <span className="ml-2 text-red-700">失败</span> : null}
      </summary>
      <div className="border-t px-3 py-3 text-xs text-slate-600">
        <div className="mb-2 flex flex-wrap gap-4">
          <span>Token：{call.total_tokens || 0}</span>
          <span>层级：{call.tier || "-"}</span>
          <span>Prompt：{call.prompt_messages.reduce((sum, item) => sum + item.chars, 0)} 字符</span>
        </div>
        <div className="space-y-2">
          {call.prompt_messages.map((message, index) => (
            <div key={index} className="grid grid-cols-[76px_64px_minmax(0,1fr)] gap-2 rounded bg-white px-2 py-2">
              <span className="font-medium">{message.role}</span>
              <span>{message.chars} 字符</span>
              <span className="truncate" title={message.preview}>{message.preview}</span>
            </div>
          ))}
        </div>
        {call.timeout_stage ? <div className="mt-2 text-red-700">超时阶段：{call.timeout_stage}</div> : null}
        {call.error ? <div className="mt-2 text-red-700">{call.error}</div> : null}
      </div>
    </details>
  );
}

function ToolSummary({ call }: { call: ObservableToolCall }) {
  return (
    <details className="rounded-md border bg-slate-50">
      <summary className="cursor-pointer px-3 py-2 text-sm">
        <span className="font-medium">{call.name}</span>
        <span className="ml-3 text-slate-500">{formatDuration(call.duration_ms)}</span>
        <span className={`ml-2 ${call.status === "failed" ? "text-red-700" : "text-emerald-700"}`}>
          {call.status === "failed" ? "失败" : "成功"}
        </span>
      </summary>
      <div className="grid gap-3 border-t p-3 xl:grid-cols-2">
        <ReadableObject title="参数（已脱敏）" value={call.input_summary} />
        <ReadableObject title="结果摘要" value={call.output_summary} />
      </div>
      {call.error ? <div className="border-t px-3 py-2 text-xs text-red-700">{call.error}</div> : null}
    </details>
  );
}

function DeliveryPanel({ delivery }: { delivery: ObservabilityView["delivery"] }) {
  return (
    <section className="border bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 font-semibold"><Send className="h-4 w-4" />消息发送与平台回调</h3>
          <p className="mt-1 text-sm text-slate-500">区分“模型已生成”“平台已接受”和“客户消息已确认送达”。</p>
        </div>
        <StatusBadge status={delivery.status} />
      </div>
      {delivery.dispatches.length ? (
        <div className="mt-4 divide-y border">
          {delivery.dispatches.map((dispatch) => (
            <div key={dispatch.dispatch_id} className="p-4">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0">
                  <div className="truncate font-mono text-xs text-slate-500">{dispatch.dispatch_id}</div>
                  <div className="mt-1 text-sm">{dispatch.source_channel} · {dispatch.source_kind}</div>
                </div>
                <StatusBadge status={dispatch.status} compact />
              </div>
              <div className="mt-3 grid gap-2 sm:grid-cols-3 text-sm">
                <div>应发 <strong>{dispatch.expected_count}</strong></div>
                <div className="text-emerald-700">成功 <strong>{dispatch.succeeded_count}</strong></div>
                <div className="text-red-700">失败 <strong>{dispatch.failed_count}</strong></div>
              </div>
              <div className="mt-3 space-y-2">
                {dispatch.items.map((item) => (
                  <div key={`${dispatch.dispatch_id}-${item.message_index}`} className="grid grid-cols-[32px_100px_minmax(0,1fr)_auto] gap-2 rounded bg-slate-50 px-3 py-2 text-xs">
                    <span>#{item.message_index + 1}</span>
                    <span>{item.message_type}</span>
                    <span className={item.error_message ? "text-red-700" : "text-slate-500"}>{item.error_message || item.platform_message_id || "等待平台消息 ID"}</span>
                    <StatusBadge status={item.status} compact />
                  </div>
                ))}
              </div>
              {dispatch.error_message ? <div className="mt-3 text-sm text-red-700">{dispatch.error_code} {dispatch.error_message}</div> : null}
              <div className="mt-3 text-xs text-slate-400">提交 {formatTime(dispatch.submitted_at)} · 回调 {formatTime(dispatch.last_callback_at)}</div>
            </div>
          ))}
        </div>
      ) : (
        <div className="mt-4 border border-dashed p-4 text-sm text-slate-500">这条请求没有关联到消息派发记录。旧日志或同步返回场景可能没有回调数据。</div>
      )}
    </section>
  );
}

function ModelCallPanel({ calls }: { calls: RawModelCall[] }) {
  return (
    <section className="border bg-white p-5">
      <details>
        <summary className="cursor-pointer font-semibold">全部模型调用与 Prompt 调试（{calls.length}）</summary>
        <p className="mt-2 text-sm text-slate-500">默认不展开完整 Prompt。这里可能包含客户历史，仅供开发排障。</p>
        <div className="mt-4 space-y-3">
          {calls.map((call, index) => (
            <details key={call.id} className="border">
              <summary className="cursor-pointer bg-slate-50 px-3 py-2 text-sm">
                {index + 1}. {call.node} / {call.name}
                <span className="ml-3 text-slate-500">{call.model || "-"}</span>
                <span className="ml-3 text-slate-500">{formatDuration(call.durationMs)}</span>
                {call.attempts > 1 ? <span className="ml-3 text-amber-700">尝试 {call.attempts}</span> : null}
                {call.error ? <span className="ml-3 text-red-700">error</span> : null}
              </summary>
              <div className="grid gap-3 p-3 xl:grid-cols-[minmax(0,1.2fr)_minmax(0,1fr)_minmax(240px,0.7fr)]">
                <PromptMessages value={call.input} />
                <Snapshot title="模型原始输出" value={call.output} tall />
                <Snapshot title="Usage / error" value={{ usage: call.usage, error: call.error }} tall />
              </div>
            </details>
          ))}
          {!calls.length ? <div className="text-sm text-slate-500">没有记录到模型调用。</div> : null}
        </div>
      </details>
    </section>
  );
}

function ReplyMessages({ messages }: { messages: JsonValue[] }) {
  if (!messages.length) return <div className="border border-dashed p-4 text-sm text-red-700">没有客户可见回复。</div>;
  return (
    <div className="space-y-2">
      {messages.map((message, index) => <ReplyMessage key={index} value={message} index={index} />)}
    </div>
  );
}

function ReplyMessage({ value, index }: { value: JsonValue; index: number }) {
  const record = isRecord(value) ? value : {};
  const type = stringField(record.type) || "text";
  const content = record.content;
  if (type === "image") {
    const url = contentString(content, "url");
    return (
      <div className="flex items-start gap-3 rounded-md border p-3">
        <ImageIcon className="mt-1 h-4 w-4 text-blue-600" />
        <div className="min-w-0">
          <div className="text-xs text-slate-500">#{index + 1} 图片</div>
          {url ? <img src={url} alt="回复图片" className="mt-2 max-h-52 max-w-full object-contain" /> : <div className="mt-1 text-sm">未记录图片地址</div>}
        </div>
      </div>
    );
  }
  if (type === "store_address") {
    return <StructuredMessage icon={<MapPin className="h-4 w-4" />} label="门店卡" value={`门店 ID：${contentString(content, "store_id") || contentString(content) || "-"}`} />;
  }
  if (type === "payment_collection") {
    const amount = isRecord(content) ? stringField(content.amount) : "";
    return <StructuredMessage icon={<WalletCards className="h-4 w-4" />} label="预约金卡" value={`金额：${amount || "10"} 元`} />;
  }
  if (type === "video") {
    const url = contentString(content, "url");
    return <StructuredMessage icon={<ExternalLink className="h-4 w-4" />} label="视频" value={url || "未记录视频地址"} />;
  }
  return (
    <div className="rounded-md bg-blue-50 px-4 py-3 text-sm leading-relaxed text-slate-900">
      <div className="mb-1 text-[11px] text-blue-600">#{index + 1} 文本</div>
      <div className="whitespace-pre-wrap">{contentString(content) || displayValue(content) || "空文本"}</div>
    </div>
  );
}

function StructuredMessage({ icon, label, value }: { icon: React.ReactNode; label: string; value: string }) {
  return (
    <div className="flex items-center gap-3 rounded-md border bg-emerald-50 px-4 py-3 text-sm">
      <span className="text-emerald-700">{icon}</span>
      <div><div className="text-xs font-medium text-emerald-800">{label}</div><div className="mt-0.5">{value}</div></div>
    </div>
  );
}

function StatusBadge({ status, compact = false }: { status: string; compact?: boolean }) {
  const meta = STATUS_META[status] || { label: status || "未知", className: "bg-slate-200 text-slate-700" };
  return <span className={`inline-flex items-center rounded px-2 py-1 font-medium ${compact ? "text-[11px]" : "text-xs"} ${meta.className}`}>{meta.label}</span>;
}

function NodeStatusIcon({ status }: { status: string }) {
  if (status === "failed") return <XCircle className="h-4 w-4 text-red-600" />;
  if (status === "warning") return <TriangleAlert className="h-4 w-4 text-amber-600" />;
  if (status === "pending") return <CircleDashed className="h-4 w-4 text-blue-600" />;
  if (status === "skipped") return <CircleDashed className="h-4 w-4 text-slate-400" />;
  return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
}

function IssueList({ title, values, tone }: { title: string; values: JsonValue[]; tone: "error" | "warning" }) {
  const className = tone === "error" ? "border-red-200 bg-red-50 text-red-800" : "border-amber-200 bg-amber-50 text-amber-900";
  return (
    <div className={`mt-4 rounded-md border p-3 text-sm ${className}`}>
      <div className="mb-1 font-medium">{title}</div>
      {values.map((value, index) => <div key={index} className="break-words text-xs leading-relaxed">{displayValue(value)}</div>)}
    </div>
  );
}

function ReadableObject({ title, value }: { title: string; value: JsonValue }) {
  if (isRecord(value)) {
    return (
      <div>
        <div className="mb-2 text-xs font-medium text-slate-500">{title}</div>
        <dl className="space-y-1.5 text-xs">
          {Object.entries(value).map(([key, item]) => <div key={key} className="grid grid-cols-[110px_minmax(0,1fr)] gap-2"><dt className="text-slate-500">{key}</dt><dd className="break-words">{displayValue(item)}</dd></div>)}
        </dl>
      </div>
    );
  }
  return <div><div className="mb-2 text-xs font-medium text-slate-500">{title}</div><div className="text-xs">{displayValue(value)}</div></div>;
}

function Snapshot({ title, value, tall = false }: { title: string; value: JsonValue; tall?: boolean }) {
  return (
    <div className="min-w-0 rounded-md border">
      <div className="border-b bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600">{title}</div>
      <pre className={`overflow-auto whitespace-pre-wrap break-words p-3 text-xs leading-relaxed text-slate-700 ${tall ? "max-h-[620px]" : "max-h-80"}`}>{formatJson(value)}</pre>
    </div>
  );
}

function PromptMessages({ value }: { value: JsonValue }) {
  const messages = Array.isArray(value) ? value : [];
  if (!messages.length) return <Snapshot title="输入 messages / prompt" value={value} tall />;
  return (
    <div className="min-w-0 rounded-md border">
      <div className="flex items-center justify-between border-b bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600"><span>输入 messages / prompt</span><span>{messages.length} 条</span></div>
      <div className="max-h-[620px] space-y-2 overflow-auto p-3">
        {messages.map((message, index) => {
          const record = isRecord(message) ? message : {};
          const role = stringField(record.role) || "unknown";
          const content = record.content ?? message;
          return (
            <details key={index} className="border">
              <summary className="cursor-pointer bg-white px-3 py-2 text-xs"><span className="font-semibold">#{index + 1} {role}</span><span className="ml-3 text-slate-500">{contentLength(content)} 字符</span></summary>
              <pre className="max-h-[500px] overflow-auto whitespace-pre-wrap break-words border-t bg-slate-50 p-3 text-xs leading-relaxed">{typeof content === "string" ? content : formatJson(content)}</pre>
            </details>
          );
        })}
      </div>
    </div>
  );
}

function legacyObservability(run: RunItem, traces: NodeTrace[]): ObservabilityView {
  const messages = replyMessagesForDisplay(run.output_snapshot) || [];
  const nodes = traces.map((trace, index): ObservableNode => ({
    id: trace.id || `legacy-${index + 1}`,
    sequence: index + 1,
    node_name: trace.node_name || trace.node || "unknown",
    node_kind: "other",
    display_name: trace.node_name || trace.node || "unknown",
    status: trace.error ? "failed" : "success",
    duration_ms: trace.duration_ms || 0,
    started_at: trace.created_at || "",
    finished_at: "",
    parallel_group: "",
    summary: [trace.error ? `节点失败：${trace.error}` : "节点已完成"],
    important_inputs: [],
    important_outputs: [],
    model_calls: [],
    tool_calls: [],
    warnings: [],
    errors: trace.error ? [trace.error] : [],
  }));
  const context = isRecord(run.input_snapshot?.request_context) ? run.input_snapshot?.request_context : {};
  return {
    contract_version: "legacy_frontend_fallback",
    summary: {
      status: run.error ? "failed" : "success",
      request_id: run.request_id,
      created_at: run.created_at || "",
      interface_version: stringField(context?.interface_version) || "v1",
      reply_chain_mode: stringField(context?.reply_chain_mode),
      message_type: stringField(context?.msgtype) || "text",
      customer_message: contentSnippet(run),
      wall_duration_ms: run.duration_ms || 0,
      recorded_duration_ms: run.duration_ms || 0,
      slowest_node: { node_name: "", display_name: "", duration_ms: 0 },
      model_call_count: collectRawModelCalls(traces).length,
      model_retry_count: 0,
      model_fallback_count: 0,
      total_tokens: numberField(run.token_usage?.total_tokens) || 0,
      fallback_detected: false,
      error_count: run.error ? 1 : 0,
      warning_count: 0,
      errors: run.error ? [run.error] : [],
      warnings: [],
      final_messages: messages,
      http_response_messages: messages,
      async_final_messages: [],
    },
    nodes,
    delivery: { status: "not_recorded", expected_count: 0, succeeded_count: 0, failed_count: 0, dispatches: [] },
    debug: { snapshot_is_compacted: true, snapshot_label: "调试快照（可能截断）" },
  };
}

function findTraceForNode(traces: NodeTrace[], node?: ObservableNode) {
  if (!node) return undefined;
  return traces.find((trace) => trace.id === node.id) || traces[node.sequence - 1] || traces.find((trace) => (trace.node_name || trace.node) === node.node_name);
}

function collectRawModelCalls(traces: NodeTrace[]) {
  const output: RawModelCall[] = [];
  traces.forEach((trace, traceIndex) => {
    const node = trace.node_name || trace.node || "unknown";
    const calls = Array.isArray(trace.tool_calls) ? trace.tool_calls : [];
    calls.forEach((call, callIndex) => collectRawModelCallValue(call, { node, idPrefix: `${traceIndex}-${callIndex}`, output }));
  });
  return output;
}

function collectRawModelCallValue(value: JsonValue, context: { node: string; idPrefix: string; output: RawModelCall[] }) {
  if (!isRecord(value)) return;
  if (isModelCall(value)) context.output.push(toRawModelCall(value, context.node, context.idPrefix));
  const nested = value.nested_calls;
  if (Array.isArray(nested)) nested.forEach((item, index) => collectRawModelCallValue(item, { node: context.node, idPrefix: `${context.idPrefix}-nested-${index}`, output: context.output }));
  if (isRecord(value.retry)) context.output.push(toRawModelCall(value.retry, context.node, `${context.idPrefix}-retry`, `${stringField(value.name) || "model"}_retry`));
  if (isRecord(value.recovery)) context.output.push(toRawModelCall(value.recovery, context.node, `${context.idPrefix}-recovery`, `${stringField(value.name) || "model"}_recovery`));
}

function isModelCall(value: Record<string, JsonValue>) {
  const name = stringField(value.name).toLowerCase();
  return isRecord(value.usage) || value.raw_json_output !== undefined || /model|planner|reply_synthesizer|profile_analyzer|vision|gate/.test(name);
}

function toRawModelCall(value: Record<string, JsonValue>, node: string, id: string, fallbackName = ""): RawModelCall {
  const usage = isRecord(value.usage) ? value.usage : {};
  const inputRecord = isRecord(value.input) ? value.input : {};
  const input = inputRecord.messages !== undefined ? inputRecord.messages : value.input || {};
  return {
    id,
    node,
    name: stringField(value.name) || fallbackName || "model_call",
    tier: stringField(usage.tier) || stringField(inputRecord.tier),
    model: stringField(usage.winner_model) || stringField(usage.model),
    durationMs: numberField(usage.overall_duration_ms) ?? numberField(usage.duration_ms) ?? numberField(value.duration_ms),
    totalTokens: numberField(usage.total_tokens) ?? 0,
    input,
    output: value.raw_json_output !== undefined ? value.raw_json_output : value.output || {},
    usage,
    error: stringField(value.error),
    hedgeStarted: Boolean(usage.hedge_started),
    attempts: numberField(usage.attempts) ?? numberField(usage.request_attempt) ?? 0,
    timeoutStage: stringField(usage.timeout_stage),
  };
}

function contentSnippet(run: RunItem) {
  return stringField(run.input_snapshot?.content) || stringField(run.input_snapshot?.current_message) || "无文本输入";
}

function replySnippet(run: RunItem) {
  const messages = replyMessagesForDisplay(run.output_snapshot);
  if (!Array.isArray(messages)) return "";
  return messages.map((item) => contentString(isRecord(item) ? item.content : "")).filter(Boolean).join(" / ");
}

function replyMessagesForDisplay(record?: Record<string, JsonValue>) {
  if (!record) return null;
  for (const path of [
    ["reply_control", "async_final", "reply_messages"],
    ["async_final_reply", "reply_messages"],
    ["http_response_reply_messages"],
    ["http_response_body", "reply_messages"],
    ["reply_messages"],
  ]) {
    const value = pathValue(record, path);
    if (Array.isArray(value)) return value;
  }
  return null;
}

function pathValue(value: JsonValue, path: string[]) {
  let current: JsonValue = value;
  for (const key of path) {
    if (!isRecord(current)) return undefined;
    current = current[key];
  }
  return current;
}

function statusLabel(status: string) {
  return STATUS_META[status]?.label || status || "未知";
}

function RuntimeStatusBadge({ run }: { run: RunItem }) {
  const status = String(run.runtime_status || "completed");
  if (status === "running") {
    return <span className="rounded bg-blue-100 px-1.5 py-0.5 text-xs text-blue-700">运行中 · {phaseLabel(run.runtime_phase)}</span>;
  }
  if (status === "interrupted") {
    return <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700">已中断</span>;
  }
  if (status === "completed_with_errors") {
    return <span className="rounded bg-red-100 px-1.5 py-0.5 text-xs text-red-700">已完成 · 有错误</span>;
  }
  return <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-xs text-emerald-700">已完成</span>;
}

function isRunning(run: RunItem) {
  return run.runtime_status === "running";
}

function runDurationMs(run: RunItem, nowMs: number) {
  const start = new Date(run.started_at || run.created_at || "").getTime();
  return Number.isFinite(start) ? Math.max(0, nowMs - start) : Number(run.duration_ms || 0);
}

function phaseLabel(phase?: string) {
  const labels: Record<string, string> = {
    request_received: "请求已接收",
    sop_gate: "SOP Gate",
    planner: "Planner",
    reply: "Reply",
    full: "AI 全链路",
    commit: "提交结果",
  };
  return labels[String(phase || "")] || String(phase || "处理中");
}

function formatDuration(value?: number | null) {
  if (value === null || value === undefined) return "-";
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(value >= 10000 ? 1 : 2)}s`;
}

function formatTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function displayValue(value: JsonValue): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function formatJson(value: JsonValue) {
  if (value === undefined) return "";
  try { return JSON.stringify(value, null, 2); } catch { return String(value); }
}

function contentString(value: JsonValue, preferred = "text"): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (isRecord(value)) {
    return stringField(value[preferred]) || stringField(value.text) || stringField(value.url) || stringField(value.store_id) || stringField(value.id);
  }
  return "";
}

function contentLength(value: JsonValue) {
  if (typeof value === "string") return value.length;
  try { return JSON.stringify(value).length; } catch { return 0; }
}

async function readJsonResponse(response: Response, fallbackMessage: string) {
  const text = await response.text();
  if (!text) return {} as Record<string, JsonValue>;
  try { return JSON.parse(text) as Record<string, JsonValue>; }
  catch {
    const preview = text.replace(/\s+/g, " ").slice(0, 180);
    throw new Error(`${fallbackMessage}：接口返回了非 JSON 响应（${response.status}）${preview ? `：${preview}` : ""}`);
  }
}

function errorMessage(data: Record<string, JsonValue>, fallback: string) {
  return typeof data.error === "string" && data.error ? data.error : fallback;
}

function isRecord(value: JsonValue): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function stringField(value: JsonValue) {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "";
}

function numberField(value: JsonValue) {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim()) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}
