"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  ArrowLeft,
  Ban,
  Brain,
  CheckCircle2,
  Clock3,
  Database,
  Library,
  LoaderCircle,
  RefreshCw,
  Search,
  Send,
} from "lucide-react";

type JsonRecord = Record<string, unknown>;

type TaskItem = {
  task_id: string;
  bucket: string;
  stage_label: string;
  platform_status?: string;
  platform_terminal_status?: number | null;
  platform_visible?: boolean;
  event_status?: string;
  task_status?: string;
  decision?: string;
  decision_reason?: string;
  error?: string;
  customer_id?: string;
  external_userid?: string;
  corp_id?: string;
  user_id?: string;
  wechat?: string;
  rule_name?: string;
  scene?: JsonRecord;
  dispatch_mode?: string;
  first_added_at?: string;
  first_added_at_source?: string;
  scheduled_at?: string | number;
  pulled_at?: string;
  updated_at?: string;
  sent_at?: string;
  lateness_seconds?: number | null;
  original_messages?: unknown[];
  final_messages?: unknown[];
  send_response?: JsonRecord;
};

type Summary = {
  platform_pending_total?: number;
  visible_total?: number;
  platform_pending?: number;
  pulled_unjudged?: number;
  judging?: number;
  judged_send?: number;
  judged_no_send?: number;
  sending?: number;
  sent?: number;
  failed?: number;
  recovery?: number;
};

type Worker = {
  running?: boolean;
  queue_depth?: number;
  queue_capacity?: number;
  in_flight_count?: number;
  last_poll_at?: string;
  last_poll_error?: string;
};

type ApiResult = {
  summary?: Summary;
  platform?: { refreshed?: boolean; error?: string };
  worker?: Worker;
  items?: TaskItem[];
  error?: string;
};

const BUCKETS = [
  ["", "全部阶段"],
  ["platform_pending", "平台待拉取"],
  ["pulled_unjudged", "已拉取待判断"],
  ["judging", "判断中"],
  ["judged_send", "已判断发送"],
  ["judged_no_send", "已判断不发"],
  ["sending", "发送中"],
  ["sent", "已发送"],
  ["failed", "处理失败"],
  ["recovery", "恢复中"],
] as const;

const INITIAL_FILTERS = {
  task_id: "",
  customer_id: "",
  bucket: "",
  decision: "",
  limit: "100",
  refresh_platform: "true",
};

export function SopPlatformLogViewer() {
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [data, setData] = useState<ApiResult>({});
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [resendingId, setResendingId] = useState("");
  const [notice, setNotice] = useState("");

  const items = data.items || [];
  const selected = useMemo(
    () => items.find((item) => item.task_id === selectedId) || items[0] || null,
    [items, selectedId]
  );
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const search = new URLSearchParams(filters);
    try {
      const response = await fetch(`/api/logs/sop-platform?${search.toString()}`, { cache: "no-store" });
      const payload = (await response.json()) as ApiResult;
      if (!response.ok) throw new Error(payload.error || "加载第三方 SOP 任务失败");
      const nextItems = Array.isArray(payload.items) ? payload.items : [];
      setData(payload);
      setSelectedId((current) => (nextItems.some((item) => item.task_id === current) ? current : nextItems[0]?.task_id || ""));
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载第三方 SOP 任务失败");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void load();
  }, [load]);

  const resend = useCallback(
    async (task: TaskItem) => {
      if (!task.task_id || resendingId) return;
      if (!window.confirm(`确认补发任务 #${task.task_id} 吗？系统会按同一幂等 ID 发送，已发送任务会被后端拒绝。`)) {
        return;
      }
      setResendingId(task.task_id);
      setNotice("");
      setError("");
      try {
        const response = await fetch(`/api/logs/sop-platform/${encodeURIComponent(task.task_id)}/resend`, {
          method: "POST",
          cache: "no-store",
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const detail = isRecord(payload) ? String(payload.detail || payload.error || "") : "";
          throw new Error(detail || `补发失败：${response.status}`);
        }
        setNotice(`任务 #${task.task_id} 已补发成功`);
        await load();
      } catch (err) {
        setError(err instanceof Error ? err.message : "补发失败");
      } finally {
        setResendingId("");
      }
    },
    [load, resendingId]
  );

  const summary = data.summary || {};
  const worker = data.worker || {};

  return (
    <main className="flex min-h-screen flex-col bg-slate-50 text-slate-950">
      <header className="border-b bg-white px-5 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Link href="/logs" className="inline-flex h-9 w-9 items-center justify-center rounded-md border hover:bg-slate-50" title="返回运行日志">
              <ArrowLeft className="h-4 w-4" />
            </Link>
            <div>
              <h1 className="text-lg font-semibold">第三方 SOP 任务</h1>
              <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
                <span className="inline-flex items-center gap-1">
                  <span className={`h-2 w-2 rounded-full ${worker.running ? "bg-emerald-500" : "bg-slate-300"}`} />
                  Worker {worker.running ? "运行中" : "已关闭"}
                </span>
                <span>队列 {worker.queue_depth || 0}/{worker.queue_capacity || 0}</span>
                <span>执行中 {worker.in_flight_count || 0}</span>
                <span>最近拉取 {formatTime(worker.last_poll_at)}</span>
              </div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href="/sop-materials"
              className="inline-flex items-center gap-2 rounded-md border bg-white px-4 py-2 text-sm hover:bg-slate-50"
            >
              <Library className="h-4 w-4" />
              异议素材库
            </Link>
            <button
              type="button"
              onClick={() => { void load(); }}
              disabled={loading}
              className="inline-flex items-center gap-2 rounded-md bg-slate-950 px-4 py-2 text-sm text-white disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              刷新
            </button>
          </div>
        </div>
      </header>

      <section className="border-b bg-white px-5 py-4">
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border bg-slate-200 sm:grid-cols-4 xl:grid-cols-9">
          <Metric label="平台状态10" value={summary.platform_pending_total} icon={<Database className="h-4 w-4" />} />
          <Metric label="平台待拉取" value={summary.platform_pending} icon={<Clock3 className="h-4 w-4" />} />
          <Metric label="已拉取待判断" value={summary.pulled_unjudged} icon={<LoaderCircle className="h-4 w-4" />} />
          <Metric label="判断中" value={summary.judging} icon={<Brain className="h-4 w-4" />} />
          <Metric label="判断发送" value={summary.judged_send} icon={<Send className="h-4 w-4" />} />
          <Metric label="判断不发" value={summary.judged_no_send} icon={<Ban className="h-4 w-4" />} />
          <Metric label="发送中" value={summary.sending} icon={<LoaderCircle className="h-4 w-4" />} />
          <Metric label="已发送" value={summary.sent} icon={<CheckCircle2 className="h-4 w-4" />} />
          <Metric label="处理失败" value={summary.failed} icon={<AlertTriangle className="h-4 w-4" />} />
        </div>
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <FilterInput label="任务 ID" value={filters.task_id} onChange={(value) => setFilters((prev) => ({ ...prev, task_id: value }))} />
          <FilterInput label="客户 ID" value={filters.customer_id} onChange={(value) => setFilters((prev) => ({ ...prev, customer_id: value }))} />
          <FilterSelect label="处理阶段" value={filters.bucket} options={BUCKETS} onChange={(value) => setFilters((prev) => ({ ...prev, bucket: value }))} />
          <FilterSelect
            label="模型结论"
            value={filters.decision}
            options={[["", "全部结论"], ["send", "发送"], ["no_send", "不发送"]]}
            onChange={(value) => setFilters((prev) => ({ ...prev, decision: value }))}
          />
          <FilterSelect
            label="数据范围"
            value={filters.refresh_platform}
            options={[["true", "平台实时 + 本地"], ["false", "仅本地审计"]]}
            onChange={(value) => setFilters((prev) => ({ ...prev, refresh_platform: value }))}
          />
          <FilterSelect
            label="显示数量"
            value={filters.limit}
            options={[["50", "50"], ["100", "100"], ["200", "200"], ["500", "500"]]}
            onChange={(value) => setFilters((prev) => ({ ...prev, limit: value }))}
          />
          <button type="button" onClick={() => void load()} className="inline-flex h-9 items-center gap-2 rounded-md border bg-white px-4 text-sm hover:bg-slate-50">
            <Search className="h-4 w-4" />
            查询
          </button>
        </div>
        {data.platform?.error ? (
          <div className="mt-3 flex items-center gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            平台实时队列读取失败，当前仅显示本地审计：{data.platform.error}
          </div>
        ) : null}
        {error ? (
          <div className="mt-3 flex items-center gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertTriangle className="h-4 w-4 shrink-0" />
            {error}
          </div>
        ) : null}
        {notice ? (
          <div className="mt-3 flex items-center gap-2 rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
            <CheckCircle2 className="h-4 w-4 shrink-0" />
            {notice}
          </div>
        ) : null}
      </section>

      <section className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[380px_minmax(0,1fr)]">
        <aside className="max-h-[calc(100vh-260px)] overflow-y-auto border-r bg-white">
          <div className="sticky top-0 z-10 border-b bg-white px-4 py-3 text-xs font-medium text-slate-500">
            当前结果 {summary.visible_total || items.length} 条
          </div>
          {items.map((item) => (
            <button
              key={item.task_id}
              type="button"
              onClick={() => setSelectedId(item.task_id)}
              className={`w-full border-b px-4 py-3 text-left hover:bg-slate-50 ${selected?.task_id === item.task_id ? "bg-slate-100" : "bg-white"}`}
            >
              <div className="flex items-center justify-between gap-3">
                <span className="truncate font-mono text-sm font-semibold">#{item.task_id}</span>
                <StageBadge bucket={item.bucket} label={item.stage_label} />
              </div>
              <div className="mt-2 truncate text-sm">{item.rule_name || "未命名任务"}</div>
              <div className="mt-1 flex items-center justify-between gap-2 text-xs text-slate-500">
                <span className="truncate">客户 {item.customer_id || "身份缺失"}</span>
                <span className="shrink-0">{formatTime(item.scheduled_at)}</span>
              </div>
              {item.decision_reason ? <div className="mt-2 line-clamp-2 text-xs text-slate-500">{item.decision_reason}</div> : null}
            </button>
          ))}
          {!loading && items.length === 0 ? <div className="p-8 text-center text-sm text-slate-500">暂无匹配任务</div> : null}
        </aside>

        <div className="max-h-[calc(100vh-260px)] overflow-y-auto p-5">
          {selected ? (
            <TaskDetail task={selected} onResend={resend} resending={resendingId === selected.task_id} />
          ) : (
            <div className="border bg-white p-8 text-sm text-slate-500">请选择任务</div>
          )}
        </div>
      </section>
    </main>
  );
}

function Metric({ label, value, icon }: { label: string; value?: number; icon: ReactNode }) {
  return (
    <div className="bg-white px-4 py-3">
      <div className="flex items-center gap-2 text-xs text-slate-500">{icon}{label}</div>
      <div className="mt-2 text-xl font-semibold tabular-nums">{value || 0}</div>
    </div>
  );
}

function FilterInput({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return (
    <label className="text-xs font-medium text-slate-600">
      {label}
      <input value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 block h-9 w-44 rounded-md border bg-white px-3 text-sm" />
    </label>
  );
}

function FilterSelect({ label, value, options, onChange }: { label: string; value: string; options: readonly (readonly [string, string])[]; onChange: (value: string) => void }) {
  return (
    <label className="text-xs font-medium text-slate-600">
      {label}
      <select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 block h-9 rounded-md border bg-white px-3 text-sm">
        {options.map(([optionValue, optionLabel]) => <option key={optionValue} value={optionValue}>{optionLabel}</option>)}
      </select>
    </label>
  );
}

function TaskDetail({ task, onResend, resending }: { task: TaskItem; onResend: (task: TaskItem) => void; resending: boolean }) {
  const canResend = isResendable(task);
  return (
    <div className="space-y-4">
      <section className="border bg-white p-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-mono text-base font-semibold">任务 #{task.task_id}</h2>
              <StageBadge bucket={task.bucket} label={task.stage_label} />
            </div>
            <p className="mt-2 text-sm text-slate-600">{task.rule_name || "未命名任务"}</p>
          </div>
          <div className="text-right text-xs text-slate-500">
            <div>平台状态 {task.platform_status || "-"}</div>
            <div className="mt-1">本地状态 {task.task_status || task.event_status || "尚未拉取"}</div>
            <button
              type="button"
              onClick={() => onResend(task)}
              disabled={!canResend || resending}
              title={canResend ? "按当前任务内容手动补发" : "已发送、发送中或平台待拉取任务不能补发"}
              className="mt-3 inline-flex items-center gap-2 rounded-md border bg-white px-3 py-2 text-sm text-slate-900 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {resending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
              补发
            </button>
          </div>
        </div>
        <div className="mt-5 grid gap-x-6 gap-y-3 border-t pt-4 text-sm sm:grid-cols-2 xl:grid-cols-4">
          <Fact label="客户 ID" value={task.customer_id} />
          <Fact label="企微账号" value={task.wechat} />
          <Fact label="员工 ID" value={task.user_id} />
          <Fact label="下发方式" value={task.dispatch_mode === "direct" ? "直接下发" : "AI 客服处理"} />
          <Fact label="加微时间" value={formatTime(task.first_added_at)} />
          <Fact label="时间来源" value={task.first_added_at_source || "-"} />
          <Fact label="企业 ID" value={task.corp_id} />
          <Fact label="external_userid" value={task.external_userid} />
          <Fact label="计划时间" value={formatTime(task.scheduled_at)} />
          <Fact label="已延迟" value={formatLag(task.lateness_seconds)} />
        </div>
      </section>

      <section className="border bg-white p-5">
        <h3 className="text-sm font-semibold">处理时间线</h3>
        <div className="mt-4 grid gap-2 md:grid-cols-4">
          <TimelineStep label="平台到期" time={formatTime(task.scheduled_at)} done />
          <TimelineStep label="AI 已拉取" time={formatTime(task.pulled_at)} done={task.bucket !== "platform_pending"} />
          <TimelineStep label="模型判断" time={task.decision ? `${task.decision === "send" ? "发送" : "不发送"} · ${formatTime(task.updated_at)}` : "尚未完成"} done={Boolean(task.decision)} />
          <TimelineStep label="客户发送" time={task.sent_at ? formatTime(task.sent_at) : task.decision === "no_send" ? "无需发送" : "尚未发送"} done={Boolean(task.sent_at) || task.decision === "no_send"} />
        </div>
      </section>

      {task.decision || task.error ? (
        <section className={`border p-5 ${task.error ? "border-red-200 bg-red-50" : task.decision === "no_send" ? "border-amber-200 bg-amber-50" : "border-emerald-200 bg-emerald-50"}`}>
          <div className="flex items-center gap-2 text-sm font-semibold">
            {task.error ? <AlertTriangle className="h-4 w-4" /> : task.decision === "no_send" ? <Ban className="h-4 w-4" /> : <Brain className="h-4 w-4" />}
            {task.error ? "处理异常" : task.decision === "no_send" ? "模型判断：不发送" : "模型判断：发送"}
          </div>
          <p className="mt-2 whitespace-pre-wrap text-sm">{task.error || task.decision_reason || "未记录判断原因"}</p>
        </section>
      ) : null}

      <section className="grid gap-4 xl:grid-cols-2">
        <MessagePanel title="平台原始内容" messages={task.original_messages || []} empty="平台未提供消息内容" />
        <MessagePanel
          title={task.sent_at ? "实际发送内容" : task.decision === "send" ? "模型判断发送内容（尚未发出）" : "最终内容"}
          messages={task.final_messages || []}
          empty={task.decision === "no_send" ? "本任务已判断不发送" : "尚未生成最终内容"}
        />
      </section>

      {task.scene && Object.keys(task.scene).length ? (
        <section className="border bg-white p-5">
          <h3 className="text-sm font-semibold">平台场景</h3>
          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            {Object.entries(task.scene).map(([key, value]) => <Fact key={key} label={key} value={displayValue(value)} />)}
          </div>
        </section>
      ) : null}
    </div>
  );
}

function isResendable(task: TaskItem) {
  if (!task.task_id || task.bucket === "platform_pending" || task.sent_at) return false;
  if (task.task_status === "sent" || task.task_status === "sending" || task.event_status === "platform_send_uncertain") {
    return false;
  }
  return ["judged_no_send", "judged_send", "failed", "recovery", "pulled_unjudged"].includes(task.bucket);
}

function TimelineStep({ label, time, done }: { label: string; time: string; done: boolean }) {
  return (
    <div className={`border px-3 py-3 ${done ? "bg-slate-50" : "bg-white"}`}>
      <div className="flex items-center gap-2 text-sm font-medium">
        {done ? <CheckCircle2 className="h-4 w-4 text-emerald-600" /> : <Clock3 className="h-4 w-4 text-slate-400" />}
        {label}
      </div>
      <div className="mt-2 text-xs text-slate-500">{time || "-"}</div>
    </div>
  );
}

function MessagePanel({ title, messages, empty }: { title: string; messages: unknown[]; empty: string }) {
  return (
    <section className="border bg-white">
      <h3 className="border-b bg-slate-50 px-4 py-3 text-sm font-semibold">{title}</h3>
      <div className="space-y-3 p-4">
        {messages.map((message, index) => <MessageItem key={index} message={message} index={index} />)}
        {!messages.length ? <div className="py-6 text-center text-sm text-slate-500">{empty}</div> : null}
      </div>
    </section>
  );
}

function MessageItem({ message, index }: { message: unknown; index: number }) {
  const item = isRecord(message) ? message : {};
  const type = String(item.type || "unknown");
  const content = item.content;
  if (type === "text") {
    const text = isRecord(content) ? String(content.text || "") : String(content || "");
    return <div className="border bg-slate-50 p-3"><div className="mb-2 text-xs text-slate-500">#{index + 1} 文字</div><div className="whitespace-pre-wrap text-sm leading-6">{text}</div></div>;
  }
  const url = isRecord(content) ? String(content.url || "") : String(content || "");
  return (
    <div className="border bg-slate-50 p-3">
      <div className="mb-2 text-xs text-slate-500">#{index + 1} {type}</div>
      {type === "image" && url ? <img src={url} alt="SOP 素材" className="mb-2 max-h-56 max-w-full border object-contain" /> : null}
      {type === "video" && url ? <video src={url} controls preload="metadata" className="mb-2 max-h-56 max-w-full border bg-black" /> : null}
      {url ? <a href={url} target="_blank" rel="noreferrer" className="break-all text-xs text-blue-600 hover:underline">{url}</a> : <div className="text-xs text-slate-500">{displayValue(content)}</div>}
    </div>
  );
}

function StageBadge({ bucket, label }: { bucket: string; label: string }) {
  const tones: Record<string, string> = {
    platform_pending: "border-slate-200 bg-slate-100 text-slate-700",
    pulled_unjudged: "border-blue-200 bg-blue-50 text-blue-700",
    judging: "border-violet-200 bg-violet-50 text-violet-700",
    judged_send: "border-cyan-200 bg-cyan-50 text-cyan-700",
    judged_no_send: "border-amber-200 bg-amber-50 text-amber-800",
    sending: "border-indigo-200 bg-indigo-50 text-indigo-700",
    sent: "border-emerald-200 bg-emerald-50 text-emerald-700",
    failed: "border-red-200 bg-red-50 text-red-700",
    recovery: "border-red-200 bg-red-50 text-red-700",
  };
  return <span className={`shrink-0 rounded-md border px-2 py-1 text-xs ${tones[bucket] || tones.platform_pending}`}>{label}</span>;
}

function Fact({ label, value }: { label: string; value?: string }) {
  return <div className="min-w-0"><div className="text-xs text-slate-500">{label}</div><div className="mt-1 break-all text-sm">{value || "-"}</div></div>;
}

function formatTime(value?: string | number) {
  if (value === undefined || value === null || value === "") return "-";
  const raw = typeof value === "number" || /^\d+(\.\d+)?$/.test(String(value)) ? Number(value) : value;
  const date = typeof raw === "number" ? new Date(raw > 10_000_000_000 ? raw : raw * 1000) : new Date(raw);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}

function formatLag(value?: number | null) {
  if (value === undefined || value === null) return "-";
  if (value < 60) return `${Math.round(value)} 秒`;
  if (value < 3600) return `${Math.round(value / 60)} 分钟`;
  if (value < 86400) return `${(value / 3600).toFixed(1)} 小时`;
  return `${(value / 86400).toFixed(1)} 天`;
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}
