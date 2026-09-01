"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, ArrowLeft, Database, RefreshCw, Search, Send } from "lucide-react";

type JsonValue = unknown;

type SopEventItem = {
  id?: string;
  event_id: string;
  event_type?: string;
  source?: string;
  status?: string;
  error?: string;
  received_at?: string;
  updated_at?: string;
  task_count?: number;
  sent_count?: number;
  failed_count?: number;
  skipped_count?: number;
  raw_payload_summary?: Record<string, JsonValue>;
};

type SopSendTask = {
  id?: string;
  event_id?: string;
  customer_id?: string;
  external_userid?: string;
  corp_id?: string;
  user_id?: string;
  wechat?: string;
  sop_pack_id?: string;
  sop_pack_name?: string;
  sop_category?: string;
  trigger_source?: string;
  reply_messages?: JsonValue[];
  status?: string;
  send_payload?: Record<string, JsonValue>;
  send_response?: Record<string, JsonValue>;
  error?: string;
  created_at?: string;
  updated_at?: string;
  sent_at?: string;
};

type SopEventDetail = {
  event?: SopEventItem & { raw_payload?: Record<string, JsonValue> };
  tasks?: SopSendTask[];
};

type Filters = {
  limit: string;
  event_type: string;
  status: string;
  customer_id: string;
  external_userid: string;
  has_error: string;
};

const DEFAULT_FILTERS: Filters = {
  limit: "50",
  event_type: "",
  status: "",
  customer_id: "",
  external_userid: "",
  has_error: "",
};

export function SopLogViewer() {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [events, setEvents] = useState<SopEventItem[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<SopEventDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");

  const selectedEvent = useMemo(
    () => detail?.event || events.find((item) => eventKey(item) === selectedId || item.event_id === selectedId) || null,
    [detail, events, selectedId]
  );

  const loadEvents = useCallback(async () => {
    setLoading(true);
    setError("");
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(filters)) {
      if (value) search.set(key, value);
    }

    try {
      const response = await fetch(`/api/logs/sop?${search.toString()}`, { cache: "no-store" });
      const data = await readJsonResponse(response, "加载 SOP 日志失败");
      if (!response.ok) {
        throw new Error(errorMessage(data, "加载 SOP 日志失败"));
      }
      const items = Array.isArray(data?.items) ? data.items : [];
      setEvents(items);
      setSelectedId((current) => current || eventKey(items[0]) || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 SOP 日志失败");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const loadDetail = useCallback(async (eventId: string) => {
    if (!eventId) return;
    setDetailLoading(true);
    setError("");
    try {
      const response = await fetch(`/api/logs/sop?event_id=${encodeURIComponent(eventId)}`, { cache: "no-store" });
      const data = await readJsonResponse(response, "加载 SOP 详情失败");
      if (!response.ok) {
        throw new Error(errorMessage(data, "加载 SOP 详情失败"));
      }
      setDetail(data);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载 SOP 详情失败");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    void loadEvents();
  }, [loadEvents]);

  useEffect(() => {
    if (selectedId) {
      void loadDetail(selectedId);
    }
  }, [loadDetail, selectedId]);

  return (
    <main className="flex h-screen bg-slate-50 text-slate-950">
      <aside className="flex w-[430px] min-w-[360px] flex-col border-r bg-white">
        <header className="border-b p-4">
          <div className="mb-4 flex items-center justify-between gap-3">
            <Link href="/logs" className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-slate-50">
              <ArrowLeft className="h-4 w-4" />
              返回运行日志
            </Link>
            <button
              type="button"
              onClick={() => void loadEvents()}
              className="inline-flex items-center gap-2 rounded-md bg-slate-950 px-3 py-2 text-sm text-white disabled:opacity-60"
              disabled={loading}
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              刷新
            </button>
          </div>
          <h1 className="flex items-center gap-2 text-lg font-semibold">
            <Send className="h-5 w-5" />
            SOP 事件日志
          </h1>
          <p className="mt-1 text-sm text-slate-500">只读展示历史 SOP 事件与发送记录；旧事件接收入口已下线，不再创建新事件。</p>
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
            <TextFilter label="event_type" value={filters.event_type} onChange={(value) => setFilters((prev) => ({ ...prev, event_type: value }))} />
            <TextFilter label="status" value={filters.status} onChange={(value) => setFilters((prev) => ({ ...prev, status: value }))} />
            <TextFilter label="customer_id" value={filters.customer_id} onChange={(value) => setFilters((prev) => ({ ...prev, customer_id: value }))} />
            <TextFilter
              label="external_userid"
              value={filters.external_userid}
              onChange={(value) => setFilters((prev) => ({ ...prev, external_userid: value }))}
            />
          </div>
          <button
            type="button"
            onClick={() => void loadEvents()}
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
          {events.map((event) => (
            <button
              key={eventKey(event)}
              type="button"
              onClick={() => setSelectedId(eventKey(event))}
              className={`w-full border-b p-4 text-left hover:bg-slate-50 ${
                selectedId === eventKey(event) ? "bg-slate-100" : "bg-white"
              }`}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="truncate font-mono text-xs text-slate-600">{eventKey(event)}</span>
                <span className="shrink-0 text-xs text-slate-500">{formatTime(event.received_at)}</span>
              </div>
              <div className="mt-1 truncate font-mono text-xs text-slate-400">{event.event_id}</div>
              <div className="mt-2 line-clamp-1 text-sm">{event.event_type || "unknown_event"}</div>
              <div className="mt-1 line-clamp-1 text-xs text-slate-500">
                {summaryText(event.raw_payload_summary)} / tasks {event.task_count || 0}
              </div>
              <div className="mt-2 flex flex-wrap gap-1">
                <StatusBadge status={event.status || ""} />
                {event.sent_count ? <Badge>{event.sent_count} sent</Badge> : null}
                {event.skipped_count ? <Badge>{event.skipped_count} skipped</Badge> : null}
                {eventHasError(event) ? <Badge tone="red">错误</Badge> : null}
              </div>
            </button>
          ))}
          {!loading && events.length === 0 ? <div className="p-6 text-sm text-slate-500">暂无 SOP 事件日志。</div> : null}
        </section>
      </aside>

      <section className="min-w-0 flex-1 overflow-y-auto p-6">
        {selectedEvent ? (
          <SopDetailPanel event={selectedEvent} tasks={detail?.tasks || []} rawPayload={detail?.event?.raw_payload} loading={detailLoading} />
        ) : (
          <div className="rounded-lg border bg-white p-8 text-sm text-slate-500">请选择一条 SOP 事件。</div>
        )}
      </section>
    </main>
  );
}

function TextFilter({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="col-span-2 text-xs font-medium text-slate-600">
      {label}
      <input
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="mt-1 w-full rounded-md border px-2 py-2 text-sm"
        placeholder="可为空"
      />
    </label>
  );
}

function SopDetailPanel({
  event,
  tasks,
  rawPayload,
  loading,
}: {
  event: SopEventItem;
  tasks: SopSendTask[];
  rawPayload?: Record<string, JsonValue>;
  loading: boolean;
}) {
  const taskMetrics = tasks.length
    ? {
        taskCount: tasks.length,
        sentCount: tasks.filter((task) => task.status === "sent").length,
        skippedCount: tasks.filter((task) => String(task.status || "").startsWith("skipped")).length,
        failedCount: tasks.filter((task) => String(task.status || "").startsWith("failed")).length,
      }
    : {
        taskCount: event.task_count || 0,
        sentCount: event.sent_count || 0,
        skippedCount: event.skipped_count || 0,
        failedCount: event.failed_count || 0,
      };
  return (
    <div className="space-y-5">
      <section className="rounded-lg border bg-white p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="break-all font-mono text-sm font-semibold">{eventKey(event)}</h2>
            <p className="mt-1 break-all font-mono text-xs text-slate-500">{event.event_id}</p>
            <p className="mt-2 text-sm text-slate-600">
              {event.event_type} / {event.status} / {formatTime(event.received_at)}
            </p>
          </div>
          <Database className="h-5 w-5 shrink-0 text-slate-400" />
        </div>
        {event.error && eventHasError(event) ? (
          <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{event.error}</div>
        ) : null}
        <div className="mt-4 grid grid-cols-4 gap-3 text-sm">
          <Metric label="任务" value={taskMetrics.taskCount} />
          <Metric label="已发" value={taskMetrics.sentCount} />
          <Metric label="业务跳过" value={taskMetrics.skippedCount} />
          <Metric label="处理失败" value={taskMetrics.failedCount} />
        </div>
      </section>

      <section className="rounded-lg border bg-white p-5">
        <h3 className="mb-3 text-sm font-semibold">发送任务</h3>
        {tasks.map((task) => {
          const isTaskError = taskHasError(task);
          const modelAttempts = taskModelAttempts(task);
          return (
            <div key={task.id || `${task.event_id}-${task.sop_pack_id}`} className="mb-4 rounded-md border p-4 last:mb-0">
              <div className="flex flex-wrap items-center gap-2">
                <StatusBadge status={task.status || ""} />
                <span className="font-mono text-xs text-slate-500">{task.sop_pack_id || "actions_fallback"}</span>
                {task.sop_category ? <span className="text-xs text-slate-500">category: {task.sop_category}</span> : null}
                <span className="text-xs text-slate-500">{task.trigger_source}</span>
              </div>
              <div className="mt-3 grid gap-2 text-sm text-slate-600 md:grid-cols-2">
                <div>客户：{task.customer_id || "-"}</div>
                <div>external_userid：{task.external_userid || "-"}</div>
                <div>员工：{task.user_id || "-"}</div>
                <div>企微：{task.wechat || "-"}</div>
                <div>创建：{formatTime(task.created_at)}</div>
                <div>发送：{formatTime(task.sent_at)}</div>
              </div>
              {task.error ? (
                <div
                  className={`mt-3 whitespace-pre-wrap rounded-md border p-3 text-sm ${
                    isTaskError ? "border-red-200 bg-red-50 text-red-700" : "border-amber-200 bg-amber-50 text-amber-800"
                  }`}
                >
                  <span className="font-medium">{isTaskError ? "错误" : "跳过原因"}：</span>
                  {task.error}
                </div>
              ) : null}
              {modelAttempts.length ? (
                <div className="mt-3 rounded-md border bg-slate-50 p-3">
                  <div className="text-xs font-semibold text-slate-600">模型尝试 {modelAttempts.length} 次</div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {modelAttempts.map((attempt, index) => {
                      const status = String(attempt.status || "unknown");
                      const duration = Number(attempt.duration_ms || 0);
                      return (
                        <span
                          key={`${index}-${status}`}
                          className={`rounded-md border px-2 py-1 text-xs ${
                            status === "succeeded" ? "border-emerald-200 bg-emerald-50 text-emerald-700" : "border-red-200 bg-red-50 text-red-700"
                          }`}
                          title={String(attempt.error || "")}
                        >
                          #{String(attempt.attempt || index + 1)} {status === "succeeded" ? "成功" : "失败"} · {duration}ms
                        </span>
                      );
                    })}
                  </div>
                </div>
              ) : null}
              <MessagePreview messages={task.reply_messages || []} status={task.status || ""} />
              <JsonBlock title="reply_messages" value={task.reply_messages || []} />
              <JsonBlock title="send_payload（含 conversation_fetch / event_decision_input）" value={task.send_payload || {}} />
              <JsonBlock title="send_response（含 event_decision / 主动发送响应体）" value={task.send_response || {}} />
            </div>
          );
        })}
        {!loading && tasks.length === 0 ? <div className="text-sm text-slate-500">暂无发送任务。</div> : null}
      </section>

      <section className="rounded-lg border bg-white p-5">
        <h3 className="mb-3 text-sm font-semibold">事件原始请求</h3>
        <JsonBlock title="raw_payload" value={rawPayload || {}} initiallyOpen />
      </section>
    </div>
  );
}

function MessagePreview({ messages, status }: { messages: JsonValue[]; status: string }) {
  const sent = status === "sent";
  const pending = status === "pending";
  const title = sent ? "实际发送内容" : pending ? "待发送内容" : "候选内容（未发送）";
  if (!Array.isArray(messages) || messages.length === 0) {
    return <div className="mt-3 rounded-md border bg-slate-50 p-3 text-sm text-slate-500">{title}：空</div>;
  }
  return (
    <div className="mt-3 rounded-md border bg-white">
      <div className="border-b bg-slate-50 px-3 py-2 text-xs font-semibold text-slate-600">{title}</div>
      <div className="space-y-2 p-3">
        {messages.map((message, index) => (
          <MessagePreviewItem key={index} message={message} index={index} />
        ))}
      </div>
    </div>
  );
}

function MessagePreviewItem({ message, index }: { message: JsonValue; index: number }) {
  const data = isRecord(message) ? message : {};
  const type = typeof data.type === "string" ? data.type : "unknown";
  const order = data.order ?? index + 1;
  const content = isRecord(data.content) ? data.content : data.content;

  if (type === "text") {
    const text = isRecord(content) ? content.text : content;
    return (
      <div className="rounded-md border bg-slate-50 p-3">
        <div className="mb-1 text-xs text-slate-500">#{String(order)} text</div>
        <div className="whitespace-pre-wrap text-sm text-slate-900">{String(text || "")}</div>
      </div>
    );
  }

  if (type === "image" || type === "video") {
    const url = isRecord(content) ? content.url : content;
    return (
      <div className="rounded-md border bg-slate-50 p-3">
        <div className="mb-1 text-xs text-slate-500">
          #{String(order)} {type}
        </div>
        {type === "image" && typeof url === "string" && url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={url} alt="SOP message" className="mb-2 max-h-48 rounded border bg-white object-contain" />
        ) : null}
        {type === "video" && typeof url === "string" && url ? (
          <video src={url} className="mb-2 max-h-56 rounded border bg-black" controls preload="metadata" />
        ) : null}
        <a href={String(url || "#")} target="_blank" rel="noreferrer" className="break-all text-xs text-blue-600 hover:underline">
          {String(url || "-")}
        </a>
      </div>
    );
  }

  if (type === "payment_collection") {
    const amount = isRecord(content) ? content.amount : "";
    const remark = isRecord(content) ? content.remark : "";
    return (
      <div className="rounded-md border border-emerald-200 bg-emerald-50 p-3">
        <div className="mb-1 text-xs text-emerald-700">#{String(order)} payment_collection</div>
        <div className="text-sm text-emerald-950">金额：{String(amount || 10)} 元</div>
        {remark ? <div className="mt-1 text-xs text-emerald-800">备注：{String(remark)}</div> : null}
      </div>
    );
  }

  if (type === "store_address") {
    const storeId = isRecord(content) ? content.store_id : "";
    return (
      <div className="rounded-md border border-indigo-200 bg-indigo-50 p-3">
        <div className="mb-1 text-xs text-indigo-700">#{String(order)} store_address</div>
        <div className="text-sm text-indigo-950">store_id：{String(storeId || "-")}</div>
      </div>
    );
  }

  return (
    <div className="rounded-md border bg-slate-50 p-3">
      <div className="mb-1 text-xs text-slate-500">
        #{String(order)} {type}
      </div>
      <pre className="overflow-auto whitespace-pre-wrap text-xs text-slate-800">{JSON.stringify(message, null, 2)}</pre>
    </div>
  );
}

function isRecord(value: JsonValue): value is Record<string, JsonValue> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
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

function eventKey(event?: SopEventItem) {
  return event?.id || event?.event_id || "";
}

function eventHasError(event?: SopEventItem) {
  if (!event) return false;
  return statusHasError(event.status) || Boolean(event.failed_count) || (Boolean(event.error) && statusHasError(event.status));
}

function taskHasError(task?: SopSendTask) {
  if (!task) return false;
  return statusHasError(task.status);
}

function taskModelAttempts(task: SopSendTask) {
  const response = isRecord(task.send_response) ? task.send_response : {};
  const decision = isRecord(response.event_decision) ? response.event_decision : {};
  return Array.isArray(decision.model_attempts)
    ? decision.model_attempts.filter(isRecord)
    : [];
}

function statusHasError(status?: string) {
  const value = String(status || "");
  return (
    value.startsWith("failed") ||
    value === "skipped_missing_identity" ||
    value === "skipped_unsupported_event_type" ||
    value === "skipped_model_error" ||
    value.includes("processed_with_errors")
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="rounded-md border bg-slate-50 p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
    </div>
  );
}

function JsonBlock({ title, value, initiallyOpen = false }: { title: string; value: JsonValue; initiallyOpen?: boolean }) {
  return (
    <details className="mt-3 rounded-md border bg-slate-50" open={initiallyOpen}>
      <summary className="cursor-pointer px-3 py-2 text-xs font-semibold text-slate-600">{title}</summary>
      <pre className="max-h-[360px] overflow-auto border-t p-3 text-xs leading-relaxed text-slate-800">{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

function StatusBadge({ status }: { status: string }) {
  if (!status) return null;
  const tone = statusHasError(status) ? "red" : status.includes("skipped") ? "amber" : "slate";
  return <Badge tone={tone}>{statusLabel(status)}</Badge>;
}

function statusLabel(status: string) {
  const labels: Record<string, string> = {
    sent: "已发送",
    pending: "待发送",
    processed: "处理完成",
    processed_with_errors: "处理有错误",
    failed_model_error: "模型重试失败",
    skipped_model_rejected: "模型判断不发送",
    skipped_deposit_paid: "已付后跳过收款流程",
    skipped_unsafe_payment_collection: "收款内容被安全拦截",
  };
  return labels[status] || status;
}

function Badge({ children, tone = "slate" }: { children: ReactNode; tone?: "slate" | "red" | "amber" }) {
  const className =
    tone === "red"
      ? "bg-red-100 text-red-700"
      : tone === "amber"
        ? "bg-amber-100 text-amber-700"
        : "bg-slate-200 text-slate-700";
  return <span className={`rounded-full px-2 py-0.5 text-xs ${className}`}>{children}</span>;
}

function summaryText(summary?: Record<string, JsonValue>) {
  if (!summary) return "无摘要";
  const parts = [
    summary.delay_minutes ? `${summary.delay_minutes}min` : "",
    summary.customer_state ? String(summary.customer_state) : "",
    summary.first_external_userid ? String(summary.first_external_userid) : "",
  ].filter(Boolean);
  return parts.join(" / ") || "无摘要";
}

function formatTime(value?: string) {
  if (!value) return "-";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}
