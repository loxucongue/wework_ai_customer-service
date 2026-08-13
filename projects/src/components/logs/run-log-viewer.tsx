"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowLeft, Bot, Clock, Database, RefreshCw, Search, Send } from "lucide-react";

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
  node_name?: string;
  node?: string;
  input_snapshot?: Record<string, JsonValue>;
  output_snapshot?: Record<string, JsonValue>;
  tool_calls?: JsonValue[];
  duration_ms?: number;
  error?: string;
  created_at?: string;
};

type RunDetail = {
  run?: RunItem;
  node_traces?: NodeTrace[];
  raw_log?: JsonValue;
};

type Filters = {
  limit: string;
  customer_id: string;
  conversation_id: string;
  has_error: string;
};

type ModelCallView = {
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
  limit: "50",
  customer_id: "",
  conversation_id: "",
  has_error: "",
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
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value) search.set(key, value);
    }

    try {
      const response = await fetch(`/api/logs/runs?${search.toString()}`, { cache: "no-store" });
      const data = await readJsonResponse(response, "加载日志失败");
      if (!response.ok) {
        throw new Error(errorMessage(data, "加载日志失败"));
      }
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
      if (!response.ok) {
        throw new Error(errorMessage(data, "加载详情失败"));
      }
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
    if (selectedId) {
      void loadDetail(selectedId);
    }
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
    <main className="flex h-screen bg-slate-50 text-slate-950">
      <aside className="flex w-[420px] min-w-[360px] flex-col border-r bg-white">
        <header className="border-b p-4">
          <div className="mb-4 flex flex-wrap items-center gap-2">
            <Link href="/" className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-slate-50">
              <ArrowLeft className="h-4 w-4" />
              返回对话
            </Link>
            <Link href="/logs/sop" className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-slate-50">
              <Send className="h-4 w-4" />
              SOP
            </Link>
            <Link href="/logs/sop-platform" className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-slate-50">
              <Clock className="h-4 w-4" />
              第三方任务
            </Link>
            <Link href="/logs/outreach-first-day" className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-slate-50">
              <Bot className="h-4 w-4" />
              首日千人千面
            </Link>
            <button
              type="button"
              onClick={() => void loadRuns()}
              className="inline-flex items-center gap-2 rounded-md bg-slate-950 px-3 py-2 text-sm text-white disabled:opacity-60"
              disabled={loading}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              刷新
            </button>
          </div>
          <h1 className="flex items-center gap-2 text-lg font-semibold">
            <Database className="h-5 w-5" />
            运行日志
          </h1>
          <p className="mt-1 text-sm text-slate-500">实时查看正在执行的 AI 任务、开始时间、总耗时和节点详情。</p>
        </header>

        <section className="border-b p-4">
          <div className="grid grid-cols-2 gap-3">
            <label className="text-xs font-medium text-slate-600">
              数量
              <input
                value={filters.limit}
                onChange={(event) => setFilters((prev) => ({ ...prev, limit: event.target.value }))}
                className="mt-1 w-full rounded-md border px-2 py-2 text-sm"
              />
            </label>
            <label className="text-xs font-medium text-slate-600">
              错误
              <select
                value={filters.has_error}
                onChange={(event) => setFilters((prev) => ({ ...prev, has_error: event.target.value }))}
                className="mt-1 w-full rounded-md border px-2 py-2 text-sm"
              >
                <option value="">全部</option>
                <option value="true">只看错误</option>
                <option value="false">只看正常</option>
              </select>
            </label>
            <label className="col-span-2 text-xs font-medium text-slate-600">
              customer_id
              <input
                value={filters.customer_id}
                onChange={(event) => setFilters((prev) => ({ ...prev, customer_id: event.target.value }))}
                className="mt-1 w-full rounded-md border px-2 py-2 text-sm"
                placeholder="可为空"
              />
            </label>
            <label className="col-span-2 text-xs font-medium text-slate-600">
              conversation_id
              <input
                value={filters.conversation_id}
                onChange={(event) => setFilters((prev) => ({ ...prev, conversation_id: event.target.value }))}
                className="mt-1 w-full rounded-md border px-2 py-2 text-sm"
                placeholder="可为空"
              />
            </label>
          </div>
          <button
            type="button"
            onClick={() => void loadRuns()}
            className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-slate-50"
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
            <button
              key={run.request_id}
              type="button"
              onClick={() => setSelectedId(run.request_id)}
              className={`w-full border-b p-4 text-left hover:bg-slate-50 ${
                selectedId === run.request_id ? "bg-slate-100" : "bg-white"
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <InterfaceVersionBadge run={run} />
                <span className="truncate font-mono text-xs text-slate-500">{run.request_id}</span>
                <span className="shrink-0 text-xs text-slate-500">{formatTime(run.created_at)}</span>
              </div>
              <div className="mt-2 line-clamp-2 text-sm">{contentSnippet(run)}</div>
              {replySnippet(run) ? <div className="mt-1 line-clamp-1 text-xs text-slate-500">{replySnippet(run)}</div> : null}
              <div className="mt-2 flex flex-wrap gap-1">
                <RuntimeStatusBadge run={run} />
                <span className="rounded-full bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
                  {isRunning(run) ? `已耗时 ${formatDuration(runDurationMs(run, nowMs))}` : `总耗时 ${formatDuration(run.duration_ms || 0)}`}
                </span>
                {(run.tags || []).slice(0, 4).map((tag) => (
                  <span key={tag} className="rounded-full bg-slate-200 px-2 py-0.5 text-xs text-slate-700">
                    {tag}
                  </span>
                ))}
                {run.error ? <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-700">错误</span> : null}
              </div>
            </button>
          ))}
          {!loading && runs.length === 0 ? <div className="p-6 text-sm text-slate-500">暂无运行日志。</div> : null}
        </section>
      </aside>

      <section className="min-w-0 flex-1 overflow-y-auto p-6">
        {selectedRun ? (
          <RunDetailPanel run={selectedRun} traces={detail?.node_traces || []} loading={detailLoading} rawLog={detail?.raw_log} nowMs={nowMs} />
        ) : (
          <div className="rounded-lg border bg-white p-8 text-sm text-slate-500">请选择一条运行日志。</div>
        )}
      </section>
    </main>
  );
}

function RunDetailPanel({
  run,
  traces,
  loading,
  rawLog,
  nowMs,
}: {
  run: RunItem;
  traces: NodeTrace[];
  loading: boolean;
  rawLog?: JsonValue;
  nowMs: number;
}) {
  const modelCalls = useMemo(() => collectModelCalls(traces), [traces]);
  const modelTotalMs = modelCalls.reduce((sum, item) => sum + (item.durationMs || 0), 0);
  const modelTokens = modelCalls.reduce((sum, item) => sum + item.totalTokens, 0);

  return (
    <div className="space-y-5">
      <section className="rounded-lg border bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
              <InterfaceVersionBadge run={run} />
              <h2 className="font-mono text-sm font-semibold">{run.request_id}</h2>
            <p className="mt-1 text-sm text-slate-500">
              customer: {run.customer_id || "-"} / conversation: {run.conversation_id || "-"}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
            <Clock className="h-4 w-4" />
            开始 {formatTime(run.started_at || run.created_at)}
            <span className="text-slate-300">|</span>
            {isRunning(run) ? `已耗时 ${formatDuration(runDurationMs(run, nowMs))}` : `总耗时 ${formatDuration(run.duration_ms || 0)}`}
            <span className="text-slate-300">|</span>
            模型 {modelCalls.length} 次 / {modelTotalMs || 0}ms
            <span className="text-slate-300">|</span>
            token {modelTokens || stringField(run.token_usage?.total_tokens) || "0"}
          </div>
        </div>
        {run.error ? (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            <div className="mb-1 font-medium">运行错误</div>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed">{prettyError(run.error)}</pre>
          </div>
        ) : null}
        <div className="mt-4 grid gap-4 xl:grid-cols-3">
          <Snapshot title="客户输入（含历史）" value={inputSnapshotForDisplay(run, rawLog)} />
          <Snapshot title="最终输出（HTTP 响应体）" value={httpResponseForDisplay(run)} />
          <Snapshot title="客户最终收到（异步）" value={asyncFinalForDisplay(run, traces, rawLog)} />
        </div>
      </section>

      <section className="rounded-lg border bg-white p-5">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="flex items-center gap-2 font-semibold">
            <Bot className="h-4 w-4" />
            模型调用
          </h3>
          <span className="text-sm text-slate-500">{loading ? "加载中..." : `${modelCalls.length} 次模型调用`}</span>
        </div>
        <div className="space-y-3">
          {modelCalls.map((call, index) => (
            <details key={call.id} open={index === 0 || Boolean(call.error)}>
              <summary className="cursor-pointer rounded-md border bg-slate-50 px-3 py-2 text-sm font-medium">
                {index + 1}. {call.node} / {call.name}
                {call.model ? <span className="ml-3 text-slate-500">{call.model}</span> : null}
                {call.tier ? <span className="ml-2 rounded bg-slate-200 px-1.5 py-0.5 text-xs text-slate-700">{call.tier}</span> : null}
                <span className="ml-3 text-slate-500">{call.durationMs ?? "-"}ms</span>
                {call.totalTokens ? <span className="ml-3 text-slate-500">token {call.totalTokens}</span> : null}
                {call.hedgeStarted ? <span className="ml-3 text-amber-700">hedge</span> : null}
                {call.attempts > 1 ? <span className="ml-3 text-amber-700">尝试 {call.attempts}</span> : null}
                {call.timeoutStage ? <span className="ml-3 text-red-600">{call.timeoutStage}</span> : null}
                {call.error ? <span className="ml-3 text-red-600">error</span> : null}
              </summary>
              <div className="mt-2 grid gap-3 xl:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)_minmax(260px,0.65fr)]">
                <ModelInputMessages value={call.input || {}} />
                <Snapshot title="输出 JSON" value={call.output || {}} tall />
                <Snapshot title="耗时 / usage / error" value={{ usage: call.usage, error: call.error }} tall />
              </div>
            </details>
          ))}
          {!loading && modelCalls.length === 0 ? <div className="text-sm text-slate-500">没有记录到模型调用。</div> : null}
        </div>
      </section>

      <section className="rounded-lg border bg-white p-5">
        <div className="mb-4 flex items-center justify-between">
          <h3 className="font-semibold">节点轨迹</h3>
          <span className="text-sm text-slate-500">{loading ? "加载中..." : `${traces.length} 个节点`}</span>
        </div>
        <div className="space-y-3">
          {traces.map((trace, index) => (
            <details key={`${trace.node_name || trace.node || index}-${index}`} open={index >= traces.length - 2}>
              <summary className="cursor-pointer rounded-md border bg-slate-50 px-3 py-2 text-sm font-medium">
                {index + 1}. {trace.node_name || trace.node || "unknown"}
                <span className="ml-3 text-slate-500">{trace.duration_ms ?? "-"}ms</span>
                {trace.error ? <span className="ml-3 text-red-600">error</span> : null}
                {trace.tool_calls?.length ? <span className="ml-3 text-blue-600">tool x{trace.tool_calls.length}</span> : null}
                {failedToolCount(trace) ? <span className="ml-3 text-red-600">failed tool x{failedToolCount(trace)}</span> : null}
              </summary>
              <div className="mt-2 grid gap-3 lg:grid-cols-3">
                <Snapshot title="输入快照" value={trace.input_snapshot} />
                <Snapshot title="工具/模型调用原始记录" value={trace.tool_calls || []} />
                <Snapshot title="输出快照" value={trace.output_snapshot} />
              </div>
            </details>
          ))}
          {!loading && traces.length === 0 ? <div className="text-sm text-slate-500">没有节点轨迹。</div> : null}
        </div>
      </section>

      <section className="rounded-lg border bg-white p-5">
        <details>
          <summary className="cursor-pointer text-sm font-semibold">完整日志 JSON</summary>
          <div className="mt-3">
            <Snapshot title="raw_log" value={rawLog || {}} tall />
          </div>
        </details>
      </section>
    </div>
  );
}

function Snapshot({ title, value, tall = false }: { title: string; value: JsonValue; tall?: boolean }) {
  return (
    <div className="min-w-0 rounded-md border">
      <div className="border-b bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600">{title}</div>
      <pre
        className={`overflow-auto whitespace-pre-wrap break-words p-3 text-xs leading-relaxed text-slate-700 ${
          tall ? "max-h-[620px]" : "max-h-80"
        }`}
      >
        {formatJson(value)}
      </pre>
    </div>
  );
}

function ModelInputMessages({ value }: { value: JsonValue }) {
  const messages = Array.isArray(value) ? value : [];
  if (!messages.length) {
    return <Snapshot title="输入 messages / prompt" value={value} tall />;
  }

  return (
    <div className="min-w-0 rounded-md border">
      <div className="flex items-center justify-between border-b bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600">
        <span>输入 messages / prompt</span>
        <span>{messages.length} 条</span>
      </div>
      <div className="max-h-[620px] space-y-3 overflow-auto p-3">
        {messages.map((message, index) => (
          <ModelMessageCard key={index} message={message} index={index} />
        ))}
      </div>
    </div>
  );
}

function ModelMessageCard({ message, index }: { message: JsonValue; index: number }) {
  if (!isRecord(message)) {
    return (
      <div className="rounded-md border bg-white">
        <MessageHeader index={index} role="unknown" content={message} />
        <pre className="whitespace-pre-wrap break-words p-3 text-xs leading-relaxed text-slate-700">{formatJson(message)}</pre>
      </div>
    );
  }

  const role = stringField(message.role) || "unknown";
  const content = message.content;
  return (
    <div className="rounded-md border bg-white">
      <MessageHeader index={index} role={role} content={content} />
      <div className="p-3">
        <MessageContent value={content} role={role} />
      </div>
    </div>
  );
}

function MessageHeader({ index, role, content }: { index: number; role: string; content: JsonValue }) {
  const tone = roleClassName(role);
  return (
    <div className="flex flex-wrap items-center justify-between gap-2 border-b bg-white px-3 py-2">
      <div className="flex items-center gap-2">
        <span className="font-mono text-xs text-slate-400">#{index + 1}</span>
        <span className={`rounded px-2 py-0.5 text-xs font-semibold ${tone}`}>{role || "unknown"}</span>
        {contentKind(content) ? <span className="text-xs text-slate-500">{contentKind(content)}</span> : null}
      </div>
      <span className="text-xs text-slate-400">{contentLength(content)} chars</span>
    </div>
  );
}

function MessageContent({ value, role }: { value: JsonValue; role: string }) {
  if (typeof value === "string") {
    const parsed = parseJsonString(value);
    if (parsed.ok) {
      return (
        <div className="space-y-2">
          <div className="text-xs font-medium text-slate-500">JSON payload</div>
          <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-xs leading-relaxed text-slate-100">
            {JSON.stringify(parsed.value, null, 2)}
          </pre>
        </div>
      );
    }
    return (
      <pre
        className={`max-h-[520px] overflow-auto whitespace-pre-wrap break-words rounded p-3 text-xs leading-relaxed ${
          role === "system" ? "bg-amber-50 text-slate-900" : "bg-slate-50 text-slate-800"
        }`}
      >
        {value}
      </pre>
    );
  }

  if (Array.isArray(value)) {
    return (
      <div className="space-y-2">
        {value.map((item, index) => (
          <div key={index} className="rounded border bg-slate-50 p-2">
            <div className="mb-1 text-xs font-medium text-slate-500">content[{index}]</div>
            <MessageContent value={item} role={role} />
          </div>
        ))}
      </div>
    );
  }

  if (isRecord(value)) {
    return (
      <pre className="max-h-[520px] overflow-auto whitespace-pre-wrap break-words rounded bg-slate-950 p-3 text-xs leading-relaxed text-slate-100">
        {JSON.stringify(value, null, 2)}
      </pre>
    );
  }

  return <pre className="whitespace-pre-wrap break-words text-xs leading-relaxed text-slate-700">{formatJson(value)}</pre>;
}

function collectModelCalls(traces: NodeTrace[]) {
  const output: ModelCallView[] = [];
  traces.forEach((trace, traceIndex) => {
    const node = trace.node_name || trace.node || "unknown";
    const calls = Array.isArray(trace.tool_calls) ? trace.tool_calls : [];
    calls.forEach((call, callIndex) => {
      collectModelCallValue(call, {
        node,
        idPrefix: `${traceIndex}-${callIndex}`,
        output,
      });
    });
  });
  return output;
}

function collectModelCallValue(
  value: JsonValue,
  context: { node: string; idPrefix: string; output: ModelCallView[] }
) {
  if (!isRecord(value)) return;
  if (isModelCall(value)) {
    context.output.push(toModelCallView(value, context.node, context.idPrefix));
  }
  const nested = value.nested_calls;
  if (Array.isArray(nested)) {
    nested.forEach((item, index) =>
      collectModelCallValue(item, {
        node: context.node,
        idPrefix: `${context.idPrefix}-nested-${index}`,
        output: context.output,
      })
    );
  }
  if (isRecord(value.retry)) {
    context.output.push(toModelCallView(value.retry, context.node, `${context.idPrefix}-retry`, `${stringField(value.name) || "model"}_retry`));
  }
  if (isRecord(value.recovery)) {
    context.output.push(toModelCallView(value.recovery, context.node, `${context.idPrefix}-recovery`, `${stringField(value.name) || "model"}_recovery`));
  }
}

function isModelCall(value: Record<string, JsonValue>) {
  const name = stringField(value.name).toLowerCase();
  if (isRecord(value.usage)) return true;
  if (value.raw_json_output !== undefined) return true;
  return /model|planner|reply_synthesizer|profile_analyzer|vision/.test(name);
}

function toModelCallView(value: Record<string, JsonValue>, node: string, id: string, fallbackName = ""): ModelCallView {
  const usage = isRecord(value.usage) ? value.usage : {};
  const input = isRecord(value.input) && value.input.messages !== undefined ? value.input.messages : value.input || {};
  const output = value.raw_json_output !== undefined ? value.raw_json_output : value.output || {};
  return {
    id,
    node,
    name: stringField(value.name) || fallbackName || "model_call",
    tier: stringField(usage.tier) || stringField(isRecord(value.input) ? value.input.tier : ""),
    model: stringField(usage.winner_model) || stringField(usage.model),
    durationMs: numberField(usage.overall_duration_ms) ?? numberField(usage.duration_ms) ?? numberField(value.duration_ms),
    totalTokens: numberField(usage.total_tokens) ?? 0,
    input,
    output,
    usage,
    error: stringField(value.error),
    hedgeStarted: Boolean(usage.hedge_started),
    attempts: numberField(usage.attempts) ?? numberField(usage.request_attempt) ?? 0,
    timeoutStage: stringField(usage.timeout_stage),
  };
}

function roleClassName(role: string) {
  const normalized = role.toLowerCase();
  if (normalized === "system") return "bg-amber-100 text-amber-800";
  if (normalized === "user") return "bg-blue-100 text-blue-800";
  if (normalized === "assistant") return "bg-emerald-100 text-emerald-800";
  if (normalized === "tool") return "bg-purple-100 text-purple-800";
  return "bg-slate-200 text-slate-700";
}

function contentKind(value: JsonValue) {
  if (typeof value === "string") {
    return parseJsonString(value).ok ? "json string" : "text";
  }
  if (Array.isArray(value)) return "multi-part";
  if (isRecord(value)) return "object";
  if (value === null || value === undefined) return "";
  return typeof value;
}

function contentLength(value: JsonValue) {
  if (typeof value === "string") return value.length;
  try {
    return JSON.stringify(value).length;
  } catch {
    return 0;
  }
}

function parseJsonString(value: string): { ok: true; value: JsonValue } | { ok: false; value: null } {
  const text = value.trim();
  if (!text || !/^[\[{]/.test(text)) {
    return { ok: false, value: null };
  }
  try {
    return { ok: true, value: JSON.parse(text) as JsonValue };
  } catch {
    return { ok: false, value: null };
  }
}

function contentSnippet(run: RunItem) {
  return stringField(run.input_snapshot?.content) || stringField(run.input_snapshot?.current_message) || "无文本输入";
}

function InterfaceVersionBadge({ run }: { run: RunItem }) {
  const version = runInterfaceVersion(run);
  const className =
    version === "v2"
      ? "bg-emerald-100 text-emerald-700 ring-emerald-200"
      : "bg-slate-200 text-slate-700 ring-slate-300";
  return <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase ring-1 ${className}`}>{version}</span>;
}

function RuntimeStatusBadge({ run }: { run: RunItem }) {
  const status = String(run.runtime_status || "completed");
  if (status === "running") {
    return <span className="rounded-full bg-blue-100 px-2 py-0.5 text-xs text-blue-700">运行中 · {phaseLabel(run.runtime_phase)}</span>;
  }
  if (status === "interrupted") {
    return <span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs text-amber-700">已中断</span>;
  }
  if (status === "completed_with_errors") {
    return <span className="rounded-full bg-red-100 px-2 py-0.5 text-xs text-red-700">已完成 · 有错误</span>;
  }
  return <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-xs text-emerald-700">已完成</span>;
}

function runInterfaceVersion(run: RunItem) {
  const direct = String(run.interface_version || "").trim().toLowerCase();
  if (direct === "v2") return "v2";
  const context = run.input_snapshot?.request_context;
  if (isRecord(context)) {
    const values = [context.interface_version, context.api_version, context.source_protocol];
    if (values.some((item) => String(item || "").toLowerCase().includes("v2"))) return "v2";
  }
  return "v1";
}

function isRunning(run: RunItem) {
  return run.runtime_status === "running";
}

function runDurationMs(run: RunItem, nowMs: number) {
  const start = new Date(run.started_at || run.created_at || "").getTime();
  return Number.isFinite(start) ? Math.max(0, nowMs - start) : Number(run.duration_ms || 0);
}

function formatDuration(value: number) {
  if (value < 1000) return `${Math.max(0, Math.round(value))}ms`;
  const seconds = Math.floor(value / 1000);
  if (seconds < 60) return `${seconds}s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
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

function errorMessage(data: Record<string, JsonValue>, fallback: string) {
  return typeof data.error === "string" && data.error ? data.error : fallback;
}

async function readJsonResponse(response: Response, fallbackMessage: string) {
  const text = await response.text();
  if (!text) {
    return {} as Record<string, JsonValue>;
  }
  try {
    return JSON.parse(text) as Record<string, JsonValue>;
  } catch {
    const preview = text.replace(/\s+/g, " ").slice(0, 180);
    throw new Error(`${fallbackMessage}：接口返回了非 JSON 响应（${response.status}）${preview ? `：${preview}` : ""}`);
  }
}

function replySnippet(run: RunItem) {
  const messages = replyMessagesForDisplay(run.output_snapshot);
  if (!Array.isArray(messages)) return "";
  return messages.map((item) => stringField(isRecord(item) ? item.content : "")).filter(Boolean).join(" / ");
}

function formatTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

function formatJson(value: JsonValue) {
  if (value === undefined) return "";
  return JSON.stringify(value, null, 2);
}

function inputSnapshotForDisplay(run: RunItem, rawLog?: JsonValue) {
  const snapshot: Record<string, JsonValue> = { ...(run.input_snapshot || {}) };
  if (!isRecord(rawLog)) {
    return snapshot;
  }
  for (const key of [
    "content",
    "customer_id",
    "corp_id",
    "conversation_history",
    "file_image",
    "user_id",
    "wechat",
    "external_userid",
    "customer_add_wechat_id",
    "confirmed_store_id",
    "confirmed_store_name",
    "store_id",
    "store_name",
    "appointment_id",
    "appointment_time",
    "request_context",
  ]) {
    if (rawLog[key] !== undefined) {
      snapshot[key] = rawLog[key];
    }
  }
  if (Array.isArray(snapshot.conversation_history)) {
    snapshot.conversation_history_count = snapshot.conversation_history.length;
  }
  return snapshot;
}

function httpResponseForDisplay(run: RunItem) {
  return run.output_snapshot?.http_response_body || run.output_snapshot || {};
}

function asyncFinalForDisplay(run: RunItem, traces: NodeTrace[], rawLog?: JsonValue) {
  const outputSnapshot = run.output_snapshot || {};
  const rawRecord = isRecord(rawLog) ? rawLog : {};
  const traceOutput = asyncFinalTraceOutput(traces);
  const replyMessages =
    replyMessagesForDisplay(outputSnapshot, { asyncOnly: true }) ||
    replyMessagesForDisplay(rawRecord, { asyncOnly: true }) ||
    replyMessagesForDisplay(traceOutput, { asyncOnly: true }) ||
    [];
  return {
    reply_messages: replyMessages,
    reply_control_async_final: pathValue(outputSnapshot, ["reply_control", "async_final"]) || pathValue(rawRecord, ["reply_control", "async_final"]) || {},
    async_final_reply: pathValue(outputSnapshot, ["async_final_reply"]) || pathValue(rawRecord, ["async_final_reply"]) || {},
    async_trace_output: traceOutput || {},
  };
}

function asyncFinalTraceOutput(traces: NodeTrace[]) {
  const trace = traces.find((item) => /async.*final|final.*send|send_reply/i.test(String(item.node_name || item.node || "")));
  return trace?.output_snapshot || {};
}

function replyMessagesForDisplay(record?: Record<string, JsonValue>, options?: { asyncOnly?: boolean }) {
  if (!record) return null;
  const asyncPaths = [
    ["reply_control", "async_final", "reply_messages"],
    ["async_final", "reply_messages"],
    ["async_final_reply", "reply_messages"],
    ["async_final_reply_messages"],
  ];
  const syncPaths = [["reply_messages"], ["http_response_reply_messages"], ["http_response_body", "reply_messages"]];
  for (const path of options?.asyncOnly ? asyncPaths : [...asyncPaths, ...syncPaths]) {
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

function prettyError(value: string) {
  if (!value) return "";
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
}

function failedToolCount(trace: NodeTrace) {
  const calls = Array.isArray(trace.tool_calls) ? trace.tool_calls : [];
  return calls.filter((item) => isRecord(item) && Boolean(item.error)).length;
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
