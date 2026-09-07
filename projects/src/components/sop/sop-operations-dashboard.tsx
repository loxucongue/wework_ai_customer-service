"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Clock3,
  FileSearch,
  Inbox,
  RefreshCw,
  Search,
  Timer,
  Users,
  XCircle,
} from "lucide-react";

type LatencySummary = {
  count?: number;
  p50?: number;
  p90?: number;
  p50_ms?: number | null;
  p90_ms?: number | null;
};

type Worker = {
  running?: boolean | null;
  queue_depth?: number;
  queue_capacity?: number;
  in_flight_count?: number;
  pending_total?: number;
  oldest_due_lag_seconds?: number;
  last_poll_at?: string;
  last_poll_error?: string;
  timings_ms?: Record<string, LatencySummary>;
};

type SopMetrics = {
  events?: number;
  tasks?: number;
  customers?: number;
  messages_sent?: number;
  sent?: number;
  no_send?: number;
  failed?: number;
  unfinished?: number;
  terminal_rate?: number;
  reason_breakdown?: Array<{ key: string; count: number }>;
  wechat_breakdown?: Array<{ wechat: string; tasks: number; customers: number; sent: number; no_send: number; failed: number }>;
  trend?: Array<{ bucket: string; total: number; sent: number; no_send: number; failed: number; unfinished: number }>;
  latency?: Record<string, LatencySummary>;
};

type AnalyticsResult = {
  range?: { started_from?: string; started_to?: string; timezone?: string };
  platform_sop?: SopMetrics;
  freshness?: { latest_platform_sop_at?: string };
  error?: string;
};

type WorkerResult = {
  worker?: Worker;
  worker_source?: string;
  worker_error?: string;
};

const INITIAL_FILTERS = beijingTodayRange();

export function SopOperationsDashboard() {
  const [filters, setFilters] = useState({ ...INITIAL_FILTERS, wechat: "" });
  const [appliedFilters, setAppliedFilters] = useState(filters);
  const [analytics, setAnalytics] = useState<AnalyticsResult>({});
  const [workerResult, setWorkerResult] = useState<WorkerResult>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const search = new URLSearchParams({
      started_from: asBeijingIso(appliedFilters.from),
      started_to: asBeijingIso(appliedFilters.to),
    });
    if (appliedFilters.wechat) search.set("wechat", appliedFilters.wechat);
    try {
      const [analyticsResponse, workerResponse] = await Promise.all([
        fetch(`/api/logs/sop-platform-dashboard?${search}`, { cache: "no-store" }),
        fetch("/api/logs/sop-platform-worker", { cache: "no-store" }),
      ]);
      const analyticsPayload = (await analyticsResponse.json()) as AnalyticsResult;
      const workerPayload = (await workerResponse.json()) as WorkerResult;
      if (!analyticsResponse.ok) throw new Error(analyticsPayload.error || "SOP 统计加载失败");
      setAnalytics(analyticsPayload);
      setWorkerResult(workerPayload);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "SOP 统计加载失败");
    } finally {
      setLoading(false);
    }
  }, [appliedFilters]);

  useEffect(() => {
    void load();
  }, [load]);

  const metrics = analytics.platform_sop || {};
  const worker = workerResult.worker || {};

  return (
    <main className="min-h-screen bg-slate-50 text-slate-950">
      <header className="border-b bg-white px-5 py-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold">SOP 运行 BI</h1>
            <p className="mt-1 text-sm text-slate-500">监控第三方 SOP 从平台任务、本地处理到发送与消费终态的完整链路</p>
          </div>
          <div className="flex items-center gap-2">
            <Link href="/logs/sop-platform" className="inline-flex h-9 items-center gap-2 rounded-md border bg-white px-3 text-sm hover:bg-slate-50">
              <FileSearch className="h-4 w-4" />
              查看任务证据
            </Link>
            <button type="button" onClick={() => void load()} disabled={loading} className="inline-flex h-9 items-center gap-2 rounded-md bg-slate-950 px-4 text-sm text-white disabled:opacity-60">
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
              刷新
            </button>
          </div>
        </div>
        <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-slate-500">
          <span className="inline-flex items-center gap-1.5">
            <span className={`h-2 w-2 rounded-full ${worker.running === true ? "bg-emerald-500" : worker.running === false ? "bg-red-500" : "bg-amber-400"}`} />
            {worker.running === true ? "任务服务运行中" : worker.running === false ? "任务服务已停止" : "任务服务状态未知"}
          </span>
          <span>队列 {worker.queue_depth || 0}/{worker.queue_capacity || 0}</span>
          <span>执行中 {worker.in_flight_count || 0}</span>
          <span>平台待处理 {worker.pending_total || 0}</span>
          <span>最近拉取 {formatTime(worker.last_poll_at)}</span>
          <span>数据更新 {formatTime(analytics.freshness?.latest_platform_sop_at)}</span>
        </div>
      </header>

      <section className="border-b bg-white px-5 py-4">
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-[minmax(190px,.8fr)_minmax(190px,.8fr)_minmax(180px,.7fr)_auto]">
          <input type="datetime-local" value={filters.from} onChange={(event) => setFilters((current) => ({ ...current, from: event.target.value }))} className="h-9 min-w-0 rounded-md border px-3 text-sm" aria-label="开始时间" />
          <input type="datetime-local" value={filters.to} onChange={(event) => setFilters((current) => ({ ...current, to: event.target.value }))} className="h-9 min-w-0 rounded-md border px-3 text-sm" aria-label="结束时间" />
          <input value={filters.wechat} onChange={(event) => setFilters((current) => ({ ...current, wechat: event.target.value }))} placeholder="企微账号" className="h-9 min-w-0 rounded-md border px-3 text-sm" />
          <button type="button" onClick={() => setAppliedFilters(filters)} className="inline-flex h-9 items-center justify-center gap-2 rounded-md border bg-white px-4 text-sm hover:bg-slate-50">
            <Search className="h-4 w-4" />
            查询
          </button>
        </div>
        {workerResult.worker_error ? <Notice tone="warning">任务服务状态读取失败：{workerResult.worker_error}</Notice> : null}
        {worker.last_poll_error ? <Notice tone="warning">最近一次拉取异常：{worker.last_poll_error}</Notice> : null}
        {error ? <Notice tone="error">{error}</Notice> : null}
      </section>

      {analytics.platform_sop ? (
        <DashboardContent metrics={metrics} worker={worker} />
      ) : (
        <section className="bg-white px-5 py-16 text-center text-sm text-slate-500">
          {loading ? "正在加载 SOP 运行统计..." : "当前范围暂无可用统计数据"}
        </section>
      )}
    </main>
  );
}

function DashboardContent({ metrics, worker }: { metrics: SopMetrics; worker: Worker }) {
  const terminal = (metrics.sent || 0) + (metrics.no_send || 0) + (metrics.failed || 0);
  const hours = (metrics.trend || []).map((item) => ({
    hour: `${String(item.bucket).slice(11, 13)}时`,
    total: item.total,
    sent: item.sent,
    noSend: item.no_send,
    unfinished: Math.max(0, item.unfinished - item.failed),
    failed: item.failed,
  }));
  const maxHourTotal = Math.max(1, ...hours.map((item) => item.total));
  const accounts = metrics.wechat_breakdown || [];
  const reasons = metrics.reason_breakdown || [];
  const liveTimings = worker.timings_ms || {};

  return (
    <>
      <section className="border-b bg-white px-5 py-5">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">处理结果总览</h2>
            <p className="mt-1 text-xs text-slate-500">平台任务、客户和实际消息分别计数；发送只认主动发送接口返回的消息 ID</p>
          </div>
          <span className="text-xs text-slate-500">统计时区：Asia/Shanghai</span>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Kpi label="平台任务" value={metrics.events} detail={`${metrics.customers || 0} 个客户`} icon={<Inbox />} />
          <Kpi label="本地任务" value={metrics.tasks} detail="MySQL 持久化记录" icon={<BarChart3 />} />
          <Kpi label="发送完成" value={metrics.sent} detail={`${metrics.messages_sent || 0} 条实际消息`} icon={<CheckCircle2 />} tone="success" />
          <Kpi label="无需发送" value={metrics.no_send} detail={formatRate(metrics.no_send || 0, terminal)} icon={<XCircle />} tone="warning" />
          <Kpi label="未完成" value={metrics.unfinished} detail={`当前平台待处理 ${worker.pending_total || 0}`} icon={<Clock3 />} tone={(metrics.unfinished || 0) > 0 ? "warning" : "neutral"} />
          <Kpi label="异常" value={metrics.failed} detail={`终态率 ${formatPercent(metrics.terminal_rate)}`} icon={<AlertTriangle />} tone={(metrics.failed || 0) > 0 ? "danger" : "neutral"} />
        </div>
      </section>

      <section className="grid border-b bg-white xl:grid-cols-[minmax(0,1.2fr)_minmax(320px,.8fr)]">
        <div className="border-b px-5 py-5 xl:border-b-0 xl:border-r">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-sm font-semibold">每小时处理分布</h2>
            <div className="flex flex-wrap gap-3 text-xs text-slate-500"><Legend color="bg-emerald-500" label="发送" /><Legend color="bg-amber-400" label="无需发送" /><Legend color="bg-blue-500" label="未完成" /><Legend color="bg-red-500" label="异常" /></div>
          </div>
          <div className="mt-4 space-y-2.5">
            {hours.map((item) => (
              <div key={item.hour} className="grid grid-cols-[34px_minmax(0,1fr)_34px] items-center gap-3 text-xs">
                <span className="font-mono text-slate-500">{item.hour}</span>
                <div className="flex h-5 overflow-hidden bg-slate-100" style={{ width: `${Math.max(8, item.total / maxHourTotal * 100)}%` }}>
                  <span className="bg-emerald-500" style={{ width: `${item.total ? item.sent / item.total * 100 : 0}%` }} title={`发送 ${item.sent}`} />
                  <span className="bg-amber-400" style={{ width: `${item.total ? item.noSend / item.total * 100 : 0}%` }} title={`无需发送 ${item.noSend}`} />
                  <span className="bg-blue-500" style={{ width: `${item.total ? item.unfinished / item.total * 100 : 0}%` }} title={`未完成 ${item.unfinished}`} />
                  <span className="bg-red-500" style={{ width: `${item.total ? item.failed / item.total * 100 : 0}%` }} title={`异常 ${item.failed}`} />
                </div>
                <span className="text-right tabular-nums">{item.total}</span>
              </div>
            ))}
            {!hours.length ? <EmptyText>当前范围没有任务</EmptyText> : null}
          </div>
        </div>
        <div className="px-5 py-5">
          <h2 className="text-sm font-semibold">无需发送与失败原因</h2>
          <p className="mt-1 text-xs text-slate-500">用于区分正常业务过滤和真正故障</p>
          <div className="mt-4 divide-y border-y">
            {reasons.slice(0, 8).map((item) => (
              <div key={item.key} className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 py-2.5 text-sm">
                <span className="truncate" title={item.key}>{reasonLabel(item.key)}</span>
                <strong className="tabular-nums">{item.count}</strong>
              </div>
            ))}
            {!reasons.length ? <EmptyText>没有无需发送或失败记录</EmptyText> : null}
          </div>
        </div>
      </section>

      <section className="grid min-w-0 border-b bg-white xl:grid-cols-[minmax(0,1.05fr)_minmax(0,.95fr)]">
        <div className="min-w-0 border-b px-5 py-5 xl:border-b-0 xl:border-r">
          <div className="flex items-center gap-2"><Users className="h-4 w-4 text-slate-500" /><h2 className="text-sm font-semibold">企微账号处理分布</h2></div>
          <div className="mt-4 max-h-[420px] overflow-auto">
            <table className="w-full min-w-[560px] text-left text-xs">
              <thead className="sticky top-0 border-y bg-slate-50 text-slate-500"><tr><th className="px-3 py-2 font-medium">企微号</th><th className="px-3 py-2 text-right font-medium">任务</th><th className="px-3 py-2 text-right font-medium">客户</th><th className="px-3 py-2 text-right font-medium">发送</th><th className="px-3 py-2 text-right font-medium">无需发送</th><th className="px-3 py-2 text-right font-medium">异常</th></tr></thead>
              <tbody className="divide-y">
                {accounts.map((item) => <tr key={item.wechat}><td className="px-3 py-2.5 font-medium">{item.wechat}</td><td className="px-3 py-2.5 text-right tabular-nums">{item.tasks}</td><td className="px-3 py-2.5 text-right tabular-nums">{item.customers}</td><td className="px-3 py-2.5 text-right tabular-nums text-emerald-700">{item.sent}</td><td className="px-3 py-2.5 text-right tabular-nums text-amber-700">{item.no_send}</td><td className="px-3 py-2.5 text-right tabular-nums text-red-700">{item.failed}</td></tr>)}
              </tbody>
            </table>
            {!accounts.length ? <EmptyText>当前范围没有企微账号记录</EmptyText> : null}
          </div>
        </div>
        <div className="min-w-0 px-5 py-5">
          <div className="flex items-center gap-2"><Timer className="h-4 w-4 text-slate-500" /><h2 className="text-sm font-semibold">链路耗时</h2></div>
          <p className="mt-1 text-xs text-slate-500">历史列来自数据库；实时列为 worker 本次启动后的最近 500 次采样</p>
          <div className="mt-4 divide-y border-y">
            <LatencyRow label="调度后入库" value={metrics.latency?.queue} />
            <LatencyRow label="本地全流程" value={metrics.latency?.process} />
            <LatencyRow label="发送链路" value={metrics.latency?.dispatch} />
            <LatencyRow label="平台消费请求" value={metrics.latency?.consume_request} />
            <LatencyRow label="会话上下文（实时）" value={liveTimings.context} live />
            <LatencyRow label="托管发送（实时）" value={liveTimings.send} live />
            <LatencyRow label="平台拉取（实时）" value={liveTimings.pull} live />
          </div>
        </div>
      </section>
    </>
  );
}

function Kpi({ label, value, detail, icon, tone = "neutral" }: { label: string; value?: number; detail: string; icon: ReactNode; tone?: "neutral" | "success" | "warning" | "danger" }) {
  const tones = { neutral: "bg-slate-100 text-slate-600", success: "bg-emerald-50 text-emerald-700", warning: "bg-amber-50 text-amber-700", danger: "bg-red-50 text-red-700" };
  return <div className="min-w-0 border p-3"><div className="flex items-center justify-between gap-2 text-xs text-slate-500"><span>{label}</span><span className={`flex h-7 w-7 items-center justify-center rounded-md [&>svg]:h-4 [&>svg]:w-4 ${tones[tone]}`}>{icon}</span></div><div className="mt-2 text-2xl font-semibold tabular-nums">{value || 0}</div><div className="mt-1 truncate text-xs text-slate-500" title={detail}>{detail}</div></div>;
}

function LatencyRow({ label, value, live = false }: { label: string; value?: LatencySummary; live?: boolean }) {
  const count = value?.count || 0;
  const p50 = live ? value?.p50 : value?.p50_ms;
  const p90 = live ? value?.p90 : value?.p90_ms;
  return <div className="grid grid-cols-[minmax(0,1fr)_70px_70px_48px] gap-2 py-2.5 text-xs"><span>{label}</span><span className="text-right tabular-nums">P50 {formatDuration(p50)}</span><span className="text-right tabular-nums">P90 {formatDuration(p90)}</span><span className="text-right text-slate-400">{count}次</span></div>;
}

function Legend({ color, label }: { color: string; label: string }) {
  return <span className="inline-flex items-center gap-1.5"><span className={`h-2 w-2 ${color}`} />{label}</span>;
}

function EmptyText({ children }: { children: ReactNode }) {
  return <div className="py-5 text-center text-xs text-slate-500">{children}</div>;
}

function Notice({ tone, children }: { tone: "warning" | "error"; children: ReactNode }) {
  return <div className={`mt-3 rounded-md border px-3 py-2 text-sm ${tone === "error" ? "border-red-200 bg-red-50 text-red-700" : "border-amber-200 bg-amber-50 text-amber-800"}`}>{children}</div>;
}

function beijingTodayRange() {
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Shanghai", year: "numeric", month: "2-digit", day: "2-digit" }).formatToParts(new Date());
  const values = Object.fromEntries(parts.map((part) => [part.type, part.value]));
  const date = `${values.year}-${values.month}-${values.day}`;
  return { from: `${date}T00:00:00`, to: `${date}T23:59:59` };
}

function asBeijingIso(value: string) {
  if (!value) return "";
  return `${value.length === 16 ? `${value}:00` : value}+08:00`;
}

function reasonLabel(reason: string) {
  const labels: Record<string, string> = {
    human_takeover: "人工接管",
    customer_relation_deleted: "客户关系已失效",
    sop_task_expired: "任务超过有效期",
    sop_no_send_duplicate: "已有接待消息，避免重复发送",
    all_due_groups_filtered: "本轮任务均被策略过滤",
    account_disabled: "企微账号未启用",
    reason_unrecorded: "历史原因未记录",
  };
  if (labels[reason]) return labels[reason];
  if (reason.includes("超过") && reason.includes("分钟")) return "任务超过有效期";
  if (reason.toLowerCase().includes("model http 503")) return "模型服务暂时不可用";
  return reason || "原因未记录";
}

function formatRate(value: number, total: number) {
  return total ? `${(value / total * 100).toFixed(1)}%` : "0.0%";
}

function formatPercent(value?: number) {
  return typeof value === "number" ? `${(value * 100).toFixed(1)}%` : "-";
}

function formatDuration(value?: number | null) {
  if (typeof value !== "number") return "-";
  if (value < 1000) return `${Math.round(value)}ms`;
  return `${(value / 1000).toFixed(value >= 10_000 ? 1 : 2)}s`;
}

function formatTime(value?: string | number) {
  if (value === undefined || value === null || value === "") return "未记录";
  const raw = typeof value === "number" || /^\d+(\.\d+)?$/.test(String(value)) ? Number(value) : value;
  const date = typeof raw === "number" ? new Date(raw > 10_000_000_000 ? raw : raw * 1000) : new Date(raw);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", { timeZone: "Asia/Shanghai", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}
