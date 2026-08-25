"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, CheckCircle2, Clock3, Database, RefreshCw, Search, XCircle } from "lucide-react";

type JsonRecord = Record<string, unknown>;

type QuietItem = {
  event_id: string;
  customer_id?: string;
  external_userid?: string;
  corp_id?: string;
  user_id?: string;
  wechat?: string;
  status: string;
  raw_status?: string;
  error?: string;
  reason?: string;
  source_task_count?: number;
  source_task_ids?: string[];
  covered_pack_ids?: string[];
  message_count?: number;
  suppressed_at?: string;
  processed_at?: string;
  sent_at?: string;
};

type QuietSummary = {
  night_task_count?: number;
  customer_count?: number;
  fusion_count?: number;
  sent_count?: number;
  not_sent_count?: number;
  pending_count?: number;
  processing_count?: number;
  model_rejected_count?: number;
  conversation_fetch_failed_count?: number;
  downstream_send_failed_count?: number;
  other_failed_count?: number;
};

type QuietListResponse = {
  local_date?: string;
  timezone?: string;
  summary?: QuietSummary;
  items?: QuietItem[];
  error?: string;
};

type QuietDetail = {
  event?: JsonRecord;
  task?: JsonRecord;
  source_tasks?: JsonRecord[];
  timeline?: { stage?: string; time?: string; label?: string }[];
};

const STATUS_OPTIONS = [
  ["", "全部状态"],
  ["sent", "成功补发"],
  ["pending", "待处理"],
  ["model_rejected", "模型跳过"],
  ["conversation_fetch_failed", "会话拉取失败"],
  ["downstream_send_failed", "下游发送失败"],
  ["failed", "其他失败"],
] as const;

export function QuietBacklogLogViewer() {
  const [filters, setFilters] = useState({ local_date: today(), status: "", customer_id: "", wechat: "", limit: "200" });
  const [data, setData] = useState<QuietListResponse>({});
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<QuietDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");

  const items = useMemo(() => data.items || [], [data.items]);
  const selected = useMemo(() => items.find((item) => item.event_id === selectedId) || items[0] || null, [items, selectedId]);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const search = new URLSearchParams(filters);
      const response = await fetch(`/api/logs/sop-platform/quiet-backlog?${search.toString()}`, { cache: "no-store" });
      const payload = (await response.json()) as QuietListResponse;
      if (!response.ok) throw new Error(payload.error || "加载夜间补发日志失败");
      const nextItems = Array.isArray(payload.items) ? payload.items : [];
      setData(payload);
      setSelectedId((current) => (nextItems.some((item) => item.event_id === current) ? current : nextItems[0]?.event_id || ""));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载夜间补发日志失败");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const loadDetail = useCallback(async (eventId: string) => {
    if (!eventId) return;
    setDetailLoading(true);
    try {
      const response = await fetch(`/api/logs/sop-platform/quiet-backlog/${encodeURIComponent(eventId)}`, { cache: "no-store" });
      const payload = (await response.json()) as QuietDetail & { error?: string };
      if (!response.ok) throw new Error(payload.error || "加载补发详情失败");
      setDetail(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载补发详情失败");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (selected?.event_id && selected.status !== "pending") void loadDetail(selected.event_id); else setDetail(null); }, [loadDetail, selected]);

  const summary = data.summary || {};
  return (
    <main className="flex min-h-screen flex-col bg-slate-50 text-slate-950">
      <header className="border-b bg-white px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Link href="/logs" className="inline-flex h-9 w-9 items-center justify-center rounded-md border hover:bg-slate-50" title="返回运行日志">
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <div>
              <h1 className="text-lg font-semibold">第三方 SOP · 夜间补发</h1>
              <p className="mt-1 text-xs text-slate-500">凌晨拦截、08:30 客户级融合、模型判断和实际发送的完整记录</p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/logs/sop-platform" className="inline-flex items-center gap-2 rounded-md border bg-white px-4 py-2 text-sm hover:bg-slate-50">
              <Database className="h-4 w-4" />第三方任务
            </Link>
            <button type="button" onClick={() => void load()} disabled={loading} className="inline-flex items-center gap-2 rounded-md bg-slate-950 px-4 py-2 text-sm text-white disabled:opacity-60">
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />刷新
            </button>
          </div>
        </div>
      </header>

      <section className="border-b bg-white px-5 py-4">
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border bg-slate-200 sm:grid-cols-4 xl:grid-cols-8">
          <Metric label="夜间任务" value={summary.night_task_count} />
          <Metric label="涉及客户" value={summary.customer_count} />
          <Metric label="融合任务" value={summary.fusion_count} />
          <Metric label="成功补发" value={summary.sent_count} tone="green" />
          <Metric label="未补发" value={summary.not_sent_count} tone="red" />
          <Metric label="模型跳过" value={summary.model_rejected_count} />
          <Metric label="会话失败" value={summary.conversation_fetch_failed_count} />
          <Metric label="发送失败" value={summary.downstream_send_failed_count} />
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <Field label="日期"><input type="date" value={filters.local_date} onChange={(event) => setFilters((prev) => ({ ...prev, local_date: event.target.value }))} className="mt-1 block h-9 rounded-md border px-3 text-sm" /></Field>
          <Field label="状态"><select value={filters.status} onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))} className="mt-1 block h-9 rounded-md border px-3 text-sm">{STATUS_OPTIONS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></Field>
          <Field label="客户 ID"><input value={filters.customer_id} onChange={(event) => setFilters((prev) => ({ ...prev, customer_id: event.target.value }))} className="mt-1 block h-9 w-40 rounded-md border px-3 text-sm" /></Field>
          <Field label="接待企微"><input value={filters.wechat} onChange={(event) => setFilters((prev) => ({ ...prev, wechat: event.target.value }))} className="mt-1 block h-9 w-32 rounded-md border px-3 text-sm" /></Field>
          <button type="button" onClick={() => void load()} className="inline-flex h-9 items-center gap-2 rounded-md border bg-white px-4 text-sm hover:bg-slate-50"><Search className="h-4 w-4" />查询</button>
        </div>
        {error ? <div className="mt-3 flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700"><AlertTriangle className="h-4 w-4" />{error}</div> : null}
      </section>

      <section className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[390px_minmax(0,1fr)]">
        <aside className="max-h-[calc(100vh-260px)] overflow-y-auto border-r bg-white">
          {items.map((item) => <QuietListItem key={item.event_id} item={item} selected={selected?.event_id === item.event_id} onSelect={() => setSelectedId(item.event_id)} />)}
          {!loading && !items.length ? <div className="p-8 text-center text-sm text-slate-500">该日期暂无夜间补发记录</div> : null}
        </aside>
        <div className="max-h-[calc(100vh-260px)] overflow-y-auto p-5">
          {selected ? <QuietDetailPanel item={selected} detail={detail} loading={detailLoading} /> : <div className="border bg-white p-8 text-sm text-slate-500">请选择一条记录</div>}
        </div>
      </section>
    </main>
  );
}

function QuietListItem({ item, selected, onSelect }: { item: QuietItem; selected: boolean; onSelect: () => void }) {
  return <button type="button" onClick={onSelect} className={`w-full border-b px-4 py-3 text-left hover:bg-slate-50 ${selected ? "bg-slate-100" : "bg-white"}`}>
    <div className="flex items-center justify-between gap-3"><span className="font-mono text-sm font-semibold">{item.customer_id || "身份缺失"}</span><StatusBadge status={item.status} /></div>
    <div className="mt-2 text-xs text-slate-500">{item.wechat || "-"} · 夜间任务 {item.source_task_count || 0} 条</div>
    <div className="mt-1 line-clamp-2 text-xs text-slate-500">{item.reason || item.error || "等待处理"}</div>
    <div className="mt-2 flex justify-between text-xs text-slate-400"><span>{item.message_count || 0} 条消息</span><span>{formatTime(item.processed_at || item.suppressed_at)}</span></div>
  </button>;
}

function QuietDetailPanel({ item, detail, loading }: { item: QuietItem; detail: QuietDetail | null; loading: boolean }) {
  const task = detail?.task || {};
  const sourceTasks = detail?.source_tasks || [];
  const sendResponse = isRecord(task.send_response) ? task.send_response : {};
  const eventDecision = isRecord(sendResponse.event_decision) ? sendResponse.event_decision : {};
  const selectorInput = isRecord(eventDecision.selector_input) ? eventDecision.selector_input : {};
  const messages = Array.isArray(task.reply_messages) ? task.reply_messages : [];
  return <div className="space-y-4">
    <section className="border bg-white p-5">
      <div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex items-center gap-2"><h2 className="font-mono font-semibold">{item.customer_id || "-"}</h2><StatusBadge status={item.status} /></div><p className="mt-2 text-sm text-slate-500">{item.wechat || "-"} · {item.event_id}</p></div><div className="text-right text-xs text-slate-500">处理 {formatTime(item.processed_at)}<br />发送 {formatTime(item.sent_at)}</div></div>
      <div className="mt-4 grid gap-3 border-t pt-4 sm:grid-cols-2 xl:grid-cols-4"><Fact label="原任务数量" value={String(item.source_task_count || 0)} /><Fact label="最终消息数量" value={String(item.message_count || 0)} /><Fact label="覆盖话术包" value={(item.covered_pack_ids || []).join(", ") || "-"} /><Fact label="原始状态" value={item.raw_status || "-"} /></div>
      {item.reason || item.error ? <div className={`mt-4 border p-3 text-sm ${item.status === "sent" ? "border-emerald-200 bg-emerald-50" : "border-amber-200 bg-amber-50"}`}>{item.reason || item.error}</div> : null}
    </section>
    <section className="border bg-white p-5"><h3 className="text-sm font-semibold">执行时间线</h3><div className="mt-4 grid gap-2 md:grid-cols-4">{(detail?.timeline || []).map((step) => <div key={`${step.stage}-${step.time}`} className="border bg-slate-50 p-3"><div className="flex items-center gap-2 text-sm font-medium"><Clock3 className="h-4 w-4 text-slate-500" />{step.label}</div><div className="mt-2 text-xs text-slate-500">{formatTime(step.time)}</div></div>)}</div></section>
    <section className="grid gap-4 xl:grid-cols-2"><MessagePanel title="夜间被拦截的原始 SOP" messages={sourceMessages(sourceTasks)} /><MessagePanel title={item.status === "sent" ? "实际发送内容" : "候选内容（未发送）"} messages={messages} /></section>
    <JsonPanel title="原始第三方任务" value={sourceTasks} />
    <JsonPanel title="模型融合输入" value={selectorInput} />
    <JsonPanel title="模型判断与发送响应" value={sendResponse} />
    {loading ? <div className="text-sm text-slate-500">正在加载详情…</div> : null}
  </div>;
}

function sourceMessages(tasks: JsonRecord[]) {
  const messages: unknown[] = [];
  for (const task of tasks) {
    const raw = isRecord(task.raw_event_payload) ? task.raw_event_payload : {};
    const platformTask = isRecord(raw.platform_task) ? raw.platform_task : {};
    const content = platformTask.message_content ?? platformTask.messageContent;
    if (Array.isArray(content)) messages.push(...content);
  }
  return messages;
}

function MessagePanel({ title, messages }: { title: string; messages: unknown[] }) {
  return <section className="border bg-white"><h3 className="border-b bg-slate-50 px-4 py-3 text-sm font-semibold">{title}</h3><div className="space-y-3 p-4">{messages.map((message, index) => <MessageItem key={index} message={message} index={index} />)}{!messages.length ? <div className="py-6 text-center text-sm text-slate-500">暂无消息</div> : null}</div></section>;
}

function MessageItem({ message, index }: { message: unknown; index: number }) {
  const item = isRecord(message) ? message : {};
  const type = String(item.type || item.msgType || "unknown");
  const content = item.content ?? item.contentText;
  if (type === "text") {
    const text = isRecord(content) ? String(content.text || "") : String(content || "");
    return <div className="border bg-slate-50 p-3"><div className="mb-2 text-xs text-slate-500">#{index + 1} 文字</div><div className="whitespace-pre-wrap text-sm leading-6">{text}</div></div>;
  }
  const url = isRecord(content) ? String(content.url || "") : String(content || item.mediaUrl || "");
  return <div className="border bg-slate-50 p-3"><div className="mb-2 text-xs text-slate-500">#{index + 1} {type}</div>{type === "image" && url ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt="SOP 素材" className="mb-2 max-h-56 max-w-full border object-contain" />
  ) : null}<div className="break-all text-xs text-slate-500">{url || JSON.stringify(content)}</div></div>;
}

function Metric({ label, value, tone = "default" }: { label: string; value?: number; tone?: "default" | "green" | "red" }) {
  return <div className={`px-4 py-3 ${tone === "green" ? "bg-emerald-50" : tone === "red" ? "bg-red-50" : "bg-white"}`}><div className="text-xs text-slate-500">{label}</div><div className="mt-2 text-xl font-semibold tabular-nums">{value || 0}</div></div>;
}

function StatusBadge({ status }: { status: string }) {
  const labels: Record<string, string> = { sent: "已补发", pending: "待处理", processing: "处理中", model_rejected: "模型跳过", conversation_fetch_failed: "会话失败", downstream_send_failed: "发送失败", failed: "失败" };
  const good = status === "sent";
  const Icon = good ? CheckCircle2 : status === "pending" || status === "processing" ? Clock3 : XCircle;
  return <span className={`inline-flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 text-xs ${good ? "border-emerald-200 bg-emerald-50 text-emerald-700" : status === "pending" ? "border-slate-200 bg-slate-50 text-slate-700" : "border-amber-200 bg-amber-50 text-amber-800"}`}><Icon className="h-3 w-3" />{labels[status] || status}</span>;
}

function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="text-xs font-medium text-slate-600">{label}{children}</label>; }
function Fact({ label, value }: { label: string; value: string }) { return <div><div className="text-xs text-slate-500">{label}</div><div className="mt-1 break-all text-sm">{value}</div></div>; }
function JsonPanel({ title, value }: { title: string; value: unknown }) { return <details className="border bg-white"><summary className="cursor-pointer bg-slate-50 px-4 py-3 text-sm font-semibold">{title}</summary><pre className="max-h-[460px] overflow-auto border-t p-4 text-xs leading-5">{JSON.stringify(value, null, 2)}</pre></details>; }
function isRecord(value: unknown): value is JsonRecord { return typeof value === "object" && value !== null && !Array.isArray(value); }
function today() { const now = new Date(); return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`; }
function formatTime(value?: string) { if (!value) return "-"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date); }
