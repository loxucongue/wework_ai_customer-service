"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowLeft, Clock, Database, RefreshCw, Search } from "lucide-react";

type JsonValue = unknown;

type RunItem = {
  request_id: string;
  conversation_id?: string;
  customer_id?: string;
  input_snapshot?: Record<string, JsonValue>;
  output_snapshot?: Record<string, JsonValue>;
  intents?: string[];
  tags?: string[];
  duration_ms?: number;
  token_usage?: Record<string, JsonValue>;
  error?: string;
  created_at?: string;
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

  const selectedRun = useMemo(
    () => detail?.run || runs.find((item) => item.request_id === selectedId) || null,
    [detail, runs, selectedId]
  );

  const loadRuns = useCallback(async () => {
    setLoading(true);
    setError("");
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value) search.set(key, value);
    }

    try {
      const response = await fetch(`/api/logs/runs?${search.toString()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.error || "加载日志失败");
      }
      const items = Array.isArray(data?.items) ? data.items : [];
      setRuns(items);
      setSelectedId((current) => current || items[0]?.request_id || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载日志失败");
    } finally {
      setLoading(false);
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
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.error || "加载详情失败");
      }
      setDetail(data);
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

  return (
    <main className="flex h-screen bg-slate-50 text-slate-950">
      <aside className="flex w-[420px] min-w-[360px] flex-col border-r bg-white">
        <header className="border-b p-4">
          <div className="mb-4 flex items-center justify-between gap-3">
            <Link
              href="/"
              className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-slate-50"
            >
              <ArrowLeft className="h-4 w-4" />
              返回对话
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
          <p className="mt-1 text-sm text-slate-500">查看每轮请求、节点耗时、工具调用和回复快照。</p>
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
                <span className="truncate font-mono text-xs text-slate-500">{run.request_id}</span>
                <span className="shrink-0 text-xs text-slate-500">{formatTime(run.created_at)}</span>
              </div>
              <div className="mt-2 line-clamp-2 text-sm">{contentSnippet(run)}</div>
              {replySnippet(run) ? <div className="mt-1 line-clamp-1 text-xs text-slate-500">{replySnippet(run)}</div> : null}
              <div className="mt-2 flex flex-wrap gap-1">
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
          <RunDetailPanel
            run={selectedRun}
            traces={detail?.node_traces || []}
            loading={detailLoading}
            rawLog={detail?.raw_log}
          />
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
}: {
  run: RunItem;
  traces: NodeTrace[];
  loading: boolean;
  rawLog?: JsonValue;
}) {
  return (
    <div className="space-y-5">
      <section className="rounded-lg border bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h2 className="font-mono text-sm font-semibold">{run.request_id}</h2>
            <p className="mt-1 text-sm text-slate-500">
              customer: {run.customer_id || "-"} / conversation: {run.conversation_id || "-"}
            </p>
          </div>
          <div className="flex items-center gap-2 text-sm text-slate-600">
            <Clock className="h-4 w-4" />
            {run.duration_ms ?? "-"}ms
            <span className="text-slate-300">|</span>
            token {stringField(run.token_usage?.total_tokens) || "0"}
          </div>
        </div>
        {run.error ? (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">
            <div className="mb-1 font-medium">运行错误</div>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed">
              {prettyError(run.error)}
            </pre>
          </div>
        ) : null}
        <div className="mt-4 grid gap-4 lg:grid-cols-2">
          <Snapshot title="客户输入（含历史）" value={inputSnapshotForDisplay(run, rawLog)} />
          <Snapshot title="最终输出" value={run.output_snapshot} />
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
                {failedToolCount(trace) ? (
                  <span className="ml-3 text-red-600">failed tool x{failedToolCount(trace)}</span>
                ) : null}
              </summary>
              <div className="mt-2 grid gap-3 lg:grid-cols-3">
                <Snapshot title="输入快照" value={trace.input_snapshot} />
                <Snapshot title="工具调用" value={trace.tool_calls || []} />
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
            <Snapshot title="raw_log" value={rawLog || {}} />
          </div>
        </details>
      </section>
    </div>
  );
}

function Snapshot({ title, value }: { title: string; value: JsonValue }) {
  return (
    <div className="min-w-0 rounded-md border">
      <div className="border-b bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600">{title}</div>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap break-words p-3 text-xs leading-relaxed text-slate-700">
        {formatJson(value)}
      </pre>
    </div>
  );
}

function contentSnippet(run: RunItem) {
  return stringField(run.input_snapshot?.content) || stringField(run.input_snapshot?.current_message) || "无文本输入";
}

function replySnippet(run: RunItem) {
  const messages = run.output_snapshot?.reply_messages;
  if (!Array.isArray(messages)) return "";
  return messages.map((item) => stringField((item as Record<string, JsonValue>).content)).filter(Boolean).join(" / ");
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
  return calls.filter((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return false;
    return Boolean((item as Record<string, JsonValue>).error);
  }).length;
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
