"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  BellRing,
  CalendarClock,
  Check,
  CheckCircle2,
  ChevronRight,
  Clock3,
  ExternalLink,
  FileClock,
  ImageIcon,
  Info,
  LoaderCircle,
  MessageCircle,
  RefreshCw,
  Route,
  Send,
  Settings2,
  ShieldCheck,
  UserRoundCheck,
  UsersRound,
} from "lucide-react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { cn } from "@/lib/utils";

type JsonRecord = Record<string, unknown>;

type FunnelStage = {
  key: string;
  label: string;
  count: number;
  next_count?: number | null;
  next_rate?: number | null;
  drop_count?: number | null;
  drop_rate?: number | null;
};

type TrendPoint = {
  bucket: string;
  sent: number;
  reopened: number;
  reopen_rate: number;
};

type ReasonItem = { key: string; label: string; count: number };

type QueueItem = {
  workflow_run_id: string;
  plan_id?: string;
  customer_id?: string;
  external_userid?: string;
  corp_id?: string;
  wechat?: string;
  started_at?: string;
  status?: string;
  reason_code?: string;
  reason_label?: string;
  phase: string;
  silence_minutes?: number;
  last_customer_message?: string;
  customer_need?: string;
  first_scene?: string;
  second_scene?: string;
  plan_goal?: string;
  next_touch_at?: string;
  sent_steps?: number;
  task_count?: number;
  reopened_24h?: boolean;
};

type WechatBreakdown = {
  wechat: string;
  scanned: number;
  planned: number;
  sent: number;
  reopened: number;
  reopen_rate: number;
};

type OutreachSettings = {
  enabled?: boolean;
  silence_minutes?: number;
  wechat_allowlist?: string[];
  empty_allowlist_means_all_allowed?: boolean;
  contact_age_limited?: boolean;
  eligible_after?: string;
};

type DashboardData = {
  range?: { started_from?: string; started_to?: string; bucket?: string; timezone?: string };
  funnel?: FunnelStage[];
  trend?: TrendPoint[];
  reason_breakdown?: ReasonItem[];
  queue?: QueueItem[];
  queue_total?: number;
  wechat_breakdown?: WechatBreakdown[];
  outcomes?: {
    sent_plans?: number;
    reopened_24h?: number;
    reopened_24h_rate?: number;
    transaction_progress_7d?: number;
    transaction_progress_7d_rate?: number;
  };
  freshness?: {
    latest_run_at?: string;
    latest_sent_at?: string;
    latest_customer_reply_at?: string;
  };
  data_quality?: JsonRecord;
  settings?: OutreachSettings;
  runtime?: JsonRecord;
  runtime_source?: string;
  runtime_error?: string;
};

type WorkflowNode = {
  key?: string;
  label?: string;
  status?: string;
  elapsed_ms?: number;
  model?: string;
};

type RunDetail = QueueItem & {
  final_decision?: string;
  input_snapshot?: JsonRecord;
  final_plan?: JsonRecord;
  tasks?: JsonRecord[];
  events?: JsonRecord[];
  error_message?: string;
  observability_view?: {
    decision?: JsonRecord;
    customer_context?: JsonRecord;
    materials?: {
      summary?: JsonRecord;
      recent_delivery?: JsonRecord;
      steps?: JsonRecord[];
    };
    workflow_nodes?: WorkflowNode[];
    data_availability?: JsonRecord;
  };
};

const RANGE_OPTIONS = [
  { key: "24h", label: "24 小时", hours: 24 },
  { key: "7d", label: "7 天", hours: 24 * 7 },
  { key: "30d", label: "30 天", hours: 24 * 30 },
] as const;

const PHASE_LABELS: Record<string, string> = {
  blocked: "已阻断",
  failed: "异常",
  waiting_first_touch: "等待首触达",
  waiting_next_touch: "等待后续触达",
  planned: "已计划",
  completed: "已完成",
  reopened: "客户已开口",
};

const SCENE_LABELS: Record<string, string> = {
  store_area_request: "询问门店区域",
  effect_proof: "效果证明",
  activity_intro: "活动介绍",
  objection_resolution: "异议处理",
  deposit_close: "预约金推进",
  trust_repair: "信任修复",
  health_hold: "健康暂停",
  suppress: "停止触达",
};

const TASK_STATUS_LABELS: Record<string, string> = {
  pending: "待执行",
  checking: "检查中",
  sending: "发送中",
  sent: "已发送",
  skipped: "已取消",
  failed: "失败",
  check_failed: "检查失败",
};

const EVENT_LABELS: Record<string, string> = {
  workflow_started: "唤醒判断启动",
  plan_created: "计划创建",
  plan_auto_approved: "计划进入发送队列",
  task_sent: "任务已发送",
  task_skipped_customer_replied: "客户回复，取消旧任务",
  task_skipped_non_ai_mode: "切换人工，取消旧任务",
  ai_mode_check_failed: "AI 接待状态确认失败",
  task_skipped_order_state_changed: "预约或支付状态变化，取消旧任务",
  task_failed: "任务发送失败",
  plan_cycle_completed: "唤醒计划完成",
};

export function ActiveWakeupDashboard() {
  const [rangeKey, setRangeKey] = useState<(typeof RANGE_OPTIONS)[number]["key"]>("24h");
  const [wechat, setWechat] = useState("");
  const [wechatOptions, setWechatOptions] = useState<string[]>([]);
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");
  const [detailTab, setDetailTab] = useState<"plan" | "record" | "context">("plan");
  const [configOpen, setConfigOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    const option = RANGE_OPTIONS.find((item) => item.key === rangeKey) || RANGE_OPTIONS[0];
    const end = new Date();
    const start = new Date(end.getTime() - option.hours * 60 * 60 * 1000);
    const params = new URLSearchParams({
      started_from: start.toISOString(),
      started_to: end.toISOString(),
      queue_limit: "50",
    });
    if (wechat) params.set("wechat", wechat);
    try {
      const response = await fetch(`/api/outreach/dashboard?${params}`, { cache: "no-store" });
      const payload = (await response.json()) as DashboardData & { detail?: string; error?: string };
      if (!response.ok) throw new Error(payload.detail || payload.error || "主动唤醒数据加载失败");
      setData(payload);
      setWechatOptions((current) => [...new Set([
        ...current,
        ...(payload.wechat_breakdown || []).map((item) => item.wechat).filter(Boolean),
      ])].sort());
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "主动唤醒数据加载失败");
    } finally {
      setLoading(false);
    }
  }, [rangeKey, wechat]);

  useEffect(() => {
    void load();
    const timer = window.setInterval(() => void load(), 60_000);
    return () => window.clearInterval(timer);
  }, [load]);

  const loadDetail = useCallback(async (workflowRunId: string) => {
    setSelectedId(workflowRunId);
    setDetailTab("plan");
    setDetailLoading(true);
    setDetailError("");
    setDetail(null);
    try {
      const response = await fetch(`/api/outreach/first-day-runs/${encodeURIComponent(workflowRunId)}`, { cache: "no-store" });
      const payload = (await response.json()) as RunDetail & { detail?: string };
      if (!response.ok) throw new Error(payload.detail || "唤醒明细加载失败");
      setDetail(payload);
    } catch (caught) {
      setDetailError(caught instanceof Error ? caught.message : "唤醒明细加载失败");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  const runtimeState = useMemo(() => effectiveRuntime(data), [data]);
  const queue = data?.queue || [];
  const selectedQueue = queue.find((item) => item.workflow_run_id === selectedId);

  return (
    <div className="mx-auto max-w-[1600px] space-y-5 p-4 lg:p-6">
      <section className="flex flex-col justify-between gap-4 border-b border-zinc-200 pb-5 xl:flex-row xl:items-end">
        <div>
          <div className="flex items-center gap-2 text-lg font-semibold"><BellRing className="size-5" />主动唤醒</div>
          <p className="mt-1 text-sm text-zinc-500">监控沉默客户从识别、生成计划、触达到重新开口；数据每 60 秒刷新。</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="flex rounded-md border bg-white p-1">
            {RANGE_OPTIONS.map((item) => (
              <Button key={item.key} size="sm" variant={rangeKey === item.key ? "default" : "ghost"} onClick={() => setRangeKey(item.key)}>
                {item.label}
              </Button>
            ))}
          </div>
          <select
            value={wechat}
            onChange={(event) => setWechat(event.target.value)}
            aria-label="筛选企微号"
            className="h-9 min-w-40 rounded-md border border-zinc-200 bg-white px-3 text-sm outline-none focus:border-zinc-400"
          >
            <option value="">全部企微号</option>
            {wechatOptions.map((item) => <option key={item} value={item}>{item}</option>)}
          </select>
          <Button variant="outline" size="icon" onClick={() => void load()} disabled={loading} title="刷新">
            <RefreshCw className={cn("size-4", loading && "animate-spin")} />
          </Button>
        </div>
      </section>

      {error ? <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"><AlertTriangle className="mt-0.5 size-4 shrink-0" />{error}</div> : null}

      <RuntimeStrip data={data} state={runtimeState} onConfig={() => setConfigOpen(true)} />
      <Funnel stages={data?.funnel || []} loading={loading} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(300px,0.8fr)]">
        <TrendPanel data={data} loading={loading} />
        <ReasonPanel items={data?.reason_breakdown || []} loading={loading} />
      </div>

      <QueuePanel
        rows={queue}
        total={data?.queue_total || 0}
        loading={loading}
        selectedId={selectedId}
        onSelect={(row) => void loadDetail(row.workflow_run_id)}
        onConfig={() => setConfigOpen(true)}
      />

      <DetailSheet
        open={Boolean(selectedId)}
        onOpenChange={(open) => { if (!open) { setSelectedId(""); setDetail(null); } }}
        summary={selectedQueue}
        detail={detail}
        loading={detailLoading}
        error={detailError}
        tab={detailTab}
        onTab={setDetailTab}
      />
      <RuntimeDialog open={configOpen} onOpenChange={setConfigOpen} data={data} state={runtimeState} />
    </div>
  );
}

function RuntimeStrip({ data, state, onConfig }: { data: DashboardData | null; state: RuntimeState; onConfig: () => void }) {
  const settings = data?.settings || {};
  const runtime = record(data?.runtime);
  return (
    <section className={cn("flex flex-wrap items-center gap-x-5 gap-y-2 rounded-md border bg-white px-4 py-3 text-sm shadow-sm", state.tone === "danger" && "border-red-200 bg-red-50", state.tone === "warning" && "border-amber-200 bg-amber-50")}>
      <span className="inline-flex items-center gap-2 font-semibold"><span className={cn("size-2 rounded-full", state.tone === "success" ? "bg-emerald-500" : state.tone === "warning" ? "bg-amber-500" : "bg-red-500")} />{state.label}</span>
      <span>{settings.wechat_allowlist?.length ? `${settings.wechat_allowlist.length} 个指定账号` : "全账号启用"}</span>
      <span className="inline-flex items-center gap-1.5"><UserRoundCheck className="size-4 text-zinc-500" />仅 AI 接待客户</span>
      <span className="inline-flex items-center gap-1.5"><Clock3 className="size-4 text-zinc-500" />候选阈值 {text(settings.silence_minutes ?? runtime.threshold_minutes) || "未记录"} 分钟</span>
      <span>加微时间不限</span>
      <span className="ml-auto text-xs text-zinc-500">最后扫描 {formatTime(record(runtime.monitor).last_iteration_finished_at || data?.freshness?.latest_run_at)}</span>
      <Button variant="ghost" size="sm" onClick={onConfig}><Settings2 className="size-4" />配置详情</Button>
    </section>
  );
}

function Funnel({ stages, loading }: { stages: FunnelStage[]; loading: boolean }) {
  const fallback = ["进入扫描", "符合条件", "计划生成", "成功触达", "客户开口", "预约/支付进展"];
  const items: FunnelStage[] = stages.length ? stages : fallback.map((label, index) => ({ key: String(index), label, count: 0 }));
  return (
    <section className="grid grid-cols-2 overflow-hidden rounded-md border border-zinc-200 bg-zinc-200 shadow-sm xl:grid-cols-6">
      {items.map((item, index) => (
        <div key={item.key} className="relative min-w-0 bg-white p-4">
          <div className="text-xs font-medium text-zinc-500">{item.label}</div>
          <div className="mt-2 text-2xl font-semibold tabular-nums">{loading && !stages.length ? "—" : formatNumber(item.count)}</div>
          <div className="mt-2 min-h-4 text-[11px] text-zinc-400">
            {item.drop_count != null && item.next_count != null ? `流失 ${formatNumber(item.drop_count)}（${percent(item.drop_rate)}）` : "最终结果"}
          </div>
          {index < items.length - 1 ? <div className="absolute right-2 top-1/2 hidden -translate-y-1/2 items-center text-xs font-medium text-blue-600 xl:flex"><span>{item.next_rate == null ? "—" : percent(item.next_rate)}</span><ChevronRight className="size-4" /></div> : null}
        </div>
      ))}
    </section>
  );
}

function TrendPanel({ data, loading }: { data: DashboardData | null; loading: boolean }) {
  const rows = (data?.trend || []).map((item) => ({
    ...item,
    reopen_percent: Math.round((item.reopen_rate || 0) * 1000) / 10,
    label: trendLabel(item.bucket, data?.range?.bucket),
  }));
  return (
    <Panel title="唤醒量与开口率" subtitle="成功发送后的 24 小时客户开口情况">
      <div className="mb-3 flex flex-wrap gap-4 text-xs text-zinc-500"><LegendDot tone="blue" label="成功触达" /><LegendDot tone="green" label="24h 开口率" /></div>
      <div className="h-72 min-w-0">
        {loading && !data ? <LoadingBlock /> : rows.length ? (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
              <CartesianGrid stroke="#e4e4e7" strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="label" tick={{ fontSize: 11 }} tickLine={false} minTickGap={26} />
              <YAxis yAxisId="count" allowDecimals={false} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <YAxis yAxisId="rate" orientation="right" domain={[0, 100]} tickFormatter={(value) => `${value}%`} tick={{ fontSize: 11 }} tickLine={false} axisLine={false} />
              <Tooltip formatter={(value, name) => name === "24h 开口率" ? `${value}%` : value} />
              <Line yAxisId="count" type="monotone" dataKey="sent" name="成功触达" stroke="#2563eb" strokeWidth={2} dot={false} />
              <Line yAxisId="rate" type="monotone" dataKey="reopen_percent" name="24h 开口率" stroke="#16a34a" strokeWidth={2} dot={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : <EmptyBlock text="当前范围暂无成功触达数据" />}
      </div>
      <div className="mt-4 grid grid-cols-3 gap-px overflow-hidden rounded-md border bg-zinc-200">
        <MiniMetric label="成功触达" value={formatNumber(data?.outcomes?.sent_plans)} />
        <MiniMetric label="24h 开口" value={`${formatNumber(data?.outcomes?.reopened_24h)} · ${percent(data?.outcomes?.reopened_24h_rate)}`} />
        <MiniMetric label="7d 预约/支付进展" value={`${formatNumber(data?.outcomes?.transaction_progress_7d)} · ${percent(data?.outcomes?.transaction_progress_7d_rate)}`} />
      </div>
    </Panel>
  );
}

function ReasonPanel({ items, loading }: { items: ReasonItem[]; loading: boolean }) {
  return (
    <Panel title="未触达原因" subtitle="阻断、取消或失败的主要原因">
      {loading && !items.length ? <LoadingBlock /> : items.length ? (
        <div className="space-y-1">
          {items.slice(0, 10).map((item, index) => (
            <div key={item.key} className="flex items-center gap-3 border-b border-zinc-100 py-2.5 last:border-0">
              <span className={cn("grid size-6 shrink-0 place-items-center rounded-full text-xs font-semibold", index === 0 ? "bg-red-50 text-red-700" : index < 3 ? "bg-amber-50 text-amber-700" : "bg-zinc-100 text-zinc-600")}>{index + 1}</span>
              <span className="min-w-0 flex-1 truncate text-sm" title={item.label}>{item.label}</span>
              <strong className="tabular-nums">{formatNumber(item.count)}</strong>
            </div>
          ))}
        </div>
      ) : <EmptyBlock text="当前范围没有未触达记录" />}
    </Panel>
  );
}

function QueuePanel({ rows, total, loading, selectedId, onSelect, onConfig }: { rows: QueueItem[]; total: number; loading: boolean; selectedId: string; onSelect: (row: QueueItem) => void; onConfig: () => void }) {
  return (
    <section className="overflow-hidden rounded-md border bg-white shadow-sm">
      <div className="flex flex-col justify-between gap-3 border-b px-4 py-4 sm:flex-row sm:items-center">
        <div><h2 className="text-sm font-semibold">当前唤醒队列</h2><p className="mt-1 text-xs text-zinc-500">当前范围共 {formatNumber(total)} 条，点击记录查看完整判断和计划。</p></div>
        <div className="flex flex-wrap gap-2"><Button variant="default" size="sm" asChild><Link href="/logs/outreach-first-day"><FileClock className="size-4" />查看全部运行记录</Link></Button><Button variant="outline" size="sm" onClick={onConfig}><Settings2 className="size-4" />查看配置</Button></div>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] text-left text-sm">
          <thead className="bg-zinc-50 text-xs font-medium text-zinc-500"><tr><th className="px-4 py-3">客户摘要</th><th className="px-3 py-3">企微号</th><th className="px-3 py-3">沉默时长</th><th className="px-3 py-3">当前阶段</th><th className="px-3 py-3">计划主题</th><th className="px-3 py-3">下一触达</th><th className="px-4 py-3">状态 / 原因</th></tr></thead>
          <tbody className="divide-y divide-zinc-100">
            {rows.map((row) => (
              <tr key={row.workflow_run_id} tabIndex={0} onClick={() => onSelect(row)} onKeyDown={(event) => { if (event.key === "Enter") onSelect(row); }} className={cn("cursor-pointer outline-none hover:bg-zinc-50 focus:bg-blue-50", selectedId === row.workflow_run_id && "bg-blue-50")}>
                <td className="max-w-[280px] px-4 py-3"><div className="truncate font-medium">{customerLabel(row)}</div><div className="mt-1 truncate text-xs text-zinc-500" title={row.last_customer_message}>{row.last_customer_message || row.customer_need || "未记录客户原话"}</div></td>
                <td className="px-3 py-3 text-zinc-600">{row.wechat || "未记录"}</td>
                <td className="px-3 py-3 tabular-nums">{row.silence_minutes ? `${row.silence_minutes} 分钟` : "未记录"}</td>
                <td className="px-3 py-3"><PhaseBadge phase={row.phase} /></td>
                <td className="px-3 py-3 text-zinc-600">{sceneLabel(row.first_scene) || row.plan_goal || "未生成"}</td>
                <td className="px-3 py-3 text-zinc-600">{formatTime(row.next_touch_at, true)}</td>
                <td className="px-4 py-3"><div className="flex items-center justify-between gap-2"><span className="truncate text-zinc-600" title={row.reason_label}>{row.phase === "reopened" ? "24h 内已开口" : row.reason_label || PHASE_LABELS[row.phase] || row.status || "处理中"}</span><ChevronRight className="size-4 shrink-0 text-zinc-400" /></div></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {loading && !rows.length ? <LoadingBlock /> : null}
      {!loading && !rows.length ? <EmptyBlock text="当前筛选范围没有唤醒运行记录" /> : null}
      <div className="flex items-center gap-2 border-t bg-zinc-50 px-4 py-3 text-xs text-zinc-500"><Info className="size-4" />人工接待、明确退订、已预约/支付或安全状态未知的客户不会被自动触达。</div>
    </section>
  );
}

function DetailSheet({ open, onOpenChange, summary, detail, loading, error, tab, onTab }: { open: boolean; onOpenChange: (open: boolean) => void; summary?: QueueItem; detail: RunDetail | null; loading: boolean; error: string; tab: "plan" | "record" | "context"; onTab: (tab: "plan" | "record" | "context") => void }) {
  const source: QueueItem | undefined = summary ? { ...summary, ...(detail || {}) } : detail || undefined;
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent className="w-full gap-0 p-0 sm:max-w-[540px]">
        <SheetHeader className="border-b px-5 py-4 pr-12">
          <SheetTitle className="text-base">{source ? `${customerLabel(source)} · 唤醒明细` : "唤醒明细"}</SheetTitle>
          <SheetDescription className="flex flex-wrap items-center gap-2"><span>{source?.wechat || "企微号未记录"}</span><span>·</span><span>{source?.silence_minutes ? `沉默 ${source.silence_minutes} 分钟` : "沉默时长未记录"}</span>{source?.phase ? <PhaseBadge phase={source.phase} /> : null}</SheetDescription>
        </SheetHeader>
        <div className="flex border-b px-4 py-2">
          {([['plan', '唤醒计划'], ['record', '运行记录'], ['context', '客户上下文']] as const).map(([key, label]) => <Button key={key} variant={tab === key ? "default" : "ghost"} size="sm" onClick={() => onTab(key)}>{label}</Button>)}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
          {loading ? <LoadingBlock text="正在加载完整运行明细" /> : error ? <div className="rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">{error}</div> : detail ? <DetailContent detail={detail} tab={tab} /> : <EmptyBlock text="未取得运行明细" />}
        </div>
      </SheetContent>
    </Sheet>
  );
}

function DetailContent({ detail, tab }: { detail: RunDetail; tab: "plan" | "record" | "context" }) {
  if (tab === "record") return <RunRecord detail={detail} />;
  if (tab === "context") return <CustomerContext detail={detail} />;
  return <WakeupPlan detail={detail} />;
}

function WakeupPlan({ detail }: { detail: RunDetail }) {
  const view = detail.observability_view || {};
  const decision = record(view.decision);
  const hardBoundary = record(decision.hard_boundary);
  const blocked = detail.status === "blocked" || decision.eligible === false || hardBoundary.active === true;
  const tasks = arrayOfRecords(detail.tasks);
  const materialSteps = arrayOfRecords(view.materials?.steps);
  const nodes = view.workflow_nodes || [];
  return <div className="space-y-5">
    <div className={cn("flex items-start gap-3 rounded-md border p-3 text-sm", blocked ? "border-amber-200 bg-amber-50 text-amber-900" : "border-emerald-200 bg-emerald-50 text-emerald-900")}>
      {blocked ? <ShieldCheck className="mt-0.5 size-4 shrink-0" /> : <CheckCircle2 className="mt-0.5 size-4 shrink-0" />}
      <div><div className="font-semibold">{blocked ? "本轮已安全阻断" : "本轮已完成判断"}</div><p className="mt-1 leading-5">{blocked ? detail.reason_label || text(decision.reason_code) || "当前状态不允许自动触达" : decisionSummary(detail, decision)}</p></div>
    </div>
    <Lifecycle nodes={nodes} blocked={blocked} hasPlan={Boolean(detail.plan_id)} hasSent={tasks.some((task) => text(task.status) === "sent")} reopened={detail.reopened_24h} />
    <Section title="本次唤醒计划" icon={<CalendarClock className="size-4" />}>
      {tasks.length ? <div className="space-y-3">{tasks.map((task, index) => <TaskCard key={text(task.id) || index} task={task} material={materialSteps.find((item) => number(item.step) === number(task.step_index))} />)}</div> : <EmptyBlock text={blocked ? "阻断发生在计划生成前，没有创建触达任务" : "本轮没有留存可展示的触达任务"} />}
    </Section>
    <Section title="为什么这样做" icon={<Route className="size-4" />}>
      <div className="grid gap-3 sm:grid-cols-2"><Fact label="客户当前需要" value={text(decision.customer_need) || "未记录"} /><Fact label="沉默卡点" value={text(decision.silence_barrier) || "未识别到明确卡点"} /><Fact label="第一步主题" value={sceneLabel(decision.first_scene || detail.first_scene) || "未选择"} /><Fact label="下一业务动作" value={text(decision.next_business_action) || "未记录"} /></div>
    </Section>
    <Section title="阻断与取消条件" icon={<ShieldCheck className="size-4" />}>
      <div className="grid gap-2 sm:grid-cols-2"><Guard label="客户真实回复" detail="立即取消尚未发送的旧计划" /><Guard label="切换人工接待" detail="AI 不再自动处理该客户" /><Guard label="已预约 / 已支付" detail="订单状态变化后停止唤醒" /><Guard label="明确退订或风险" detail="停止商业营销并保留审计记录" /></div>
    </Section>
  </div>;
}

function TaskCard({ task, material }: { task: JsonRecord; material?: JsonRecord }) {
  const messages = arrayOfRecords(task.reply_messages);
  const media = messages.filter((message) => ["image", "video"].includes(text(message.type)));
  const texts = messages.filter((message) => text(message.type) === "text");
  const sourceOptions = arrayOfRecords(material?.source_asset_options);
  const mediaLinks = [...new Set([...media, ...sourceOptions].map(mediaUrl).filter(Boolean))].slice(0, 5);
  return <article className="rounded-md border border-zinc-200 p-4">
    <div className="flex items-start justify-between gap-3"><div><div className="font-semibold">第 {number(task.step_index) || 1} 条 · {sceneLabel(taskScene(task)) || sceneLabel(material?.scene) || "计划触达"}</div><div className="mt-1 text-xs text-zinc-500">计划时间 {formatTime(task.scheduled_at)} · {formatNumber(messages.length)} 条消息</div></div><StatusBadge status={text(task.status)} /></div>
    <div className="mt-3 space-y-2">{texts.map((message, index) => <p key={index} className="rounded-md bg-zinc-50 px-3 py-2 text-sm leading-6">{messageText(message) || "文本未记录"}</p>)}</div>
    {(media.length || sourceOptions.length) ? <div className="mt-3 rounded-md border border-blue-100 bg-blue-50 p-3"><div className="flex items-center gap-2 text-sm font-medium text-blue-900"><ImageIcon className="size-4" />素材 {formatNumber(media.length || sourceOptions.length)} 项</div><div className="mt-2 flex flex-wrap gap-2">{mediaLinks.length ? mediaLinks.map((url, index) => <a key={`${url}-${index}`} href={url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded-md bg-white px-2 py-1 text-xs text-blue-700 ring-1 ring-blue-200">打开素材 {index + 1}<ExternalLink className="size-3" /></a>) : <span className="text-xs text-blue-800">素材地址已脱敏、过期或未留存</span>}</div></div> : null}
  </article>;
}

function RunRecord({ detail }: { detail: RunDetail }) {
  const events = arrayOfRecords(detail.events).map((item) => ({ ...item, kind: "event", at: text(item.created_at) }));
  const tasks = arrayOfRecords(detail.tasks).map((item) => ({ ...item, kind: "task", at: text(item.sent_at || item.updated_at || item.scheduled_at) }));
  const timeline: JsonRecord[] = [...events, ...tasks].sort((a, b) => text(a.at).localeCompare(text(b.at)));
  return <div className="space-y-5">
    <Section title="执行节点" icon={<Route className="size-4" />}>
      <div className="space-y-2">{(detail.observability_view?.workflow_nodes || []).map((node) => <div key={node.key} className="flex items-center gap-3 rounded-md border p-3"><NodeDot status={node.status} /><div className="min-w-0 flex-1"><div className="text-sm font-medium">{node.label || node.key}</div><div className="mt-1 truncate text-xs text-zinc-500">{node.model || "未调用模型"}{node.elapsed_ms ? ` · ${duration(node.elapsed_ms)}` : ""}</div></div><span className="text-xs text-zinc-500">{nodeStatusLabel(node.status)}</span></div>)}</div>
    </Section>
    <Section title="事件与任务时间线" icon={<FileClock className="size-4" />}>
      {timeline.length ? <div className="space-y-0">{timeline.map((item, index) => <div key={`${item.kind}-${text(item.id)}-${index}`} className="grid grid-cols-[78px_12px_1fr] gap-3 pb-4 text-sm last:pb-0"><span className="text-xs text-zinc-500">{formatTime(item.at, true)}</span><span className="mt-1 size-2 rounded-full bg-zinc-400" /><div><div className="font-medium">{item.kind === "event" ? EVENT_LABELS[text(item.event_type)] || text(item.event_type) || "运行事件" : `第 ${number(item.step_index) || "?"} 条任务 · ${TASK_STATUS_LABELS[text(item.status)] || text(item.status) || "未记录"}`}</div><p className="mt-1 text-xs leading-5 text-zinc-500">{item.kind === "event" ? eventSummary(record(item.payload)) : messageSummary(arrayOfRecords(item.reply_messages))}</p></div></div>)}</div> : <EmptyBlock text="当前记录没有留存事件时间线" />}
    </Section>
  </div>;
}

function CustomerContext({ detail }: { detail: RunDetail }) {
  const snapshot = record(detail.input_snapshot);
  const messages = arrayOfRecords(snapshot.recent_messages);
  const relation = record(record(detail.observability_view?.customer_context).customer_relation);
  const activity = record(record(detail.observability_view?.customer_context).conversation_activity);
  return <div className="space-y-5">
    <Section title="客户状态" icon={<UsersRound className="size-4" />}>
      <div className="grid gap-3 sm:grid-cols-2"><Fact label="接待模式" value={text(relation.ai_mode || relation.service_mode) || "未记录"} /><Fact label="沉默时长" value={number(activity.reply_wait_minutes) ? `${number(activity.reply_wait_minutes)} 分钟` : detail.silence_minutes ? `${detail.silence_minutes} 分钟` : "未记录"} /><Fact label="企微号" value={detail.wechat || "未记录"} /><Fact label="订单状态" value={text(record(snapshot.order_context).state || record(snapshot.order_context).status) || "未记录"} /></div>
    </Section>
    <Section title="最近客户可见对话" icon={<MessageCircle className="size-4" />}>
      {messages.length ? <div className="space-y-3">{messages.map((message, index) => { const customer = isCustomerMessage(message); return <div key={index} className={cn("flex", customer ? "justify-start" : "justify-end")}><div className={cn("max-w-[88%] rounded-lg px-3 py-2 text-sm", customer ? "bg-zinc-100" : "bg-blue-600 text-white")}><div className="mb-1 text-[11px] opacity-70">{customer ? "客户" : "AI / 客服"} · {formatTime(message.created_at || message.timestamp, true)}</div><p className="whitespace-pre-wrap break-words leading-5">{messageText(message) || `[${text(message.type || message.msgtype) || "非文本消息"}]`}</p></div></div>; })}</div> : <EmptyBlock text="最近对话未留存或已超过保留期" />}
    </Section>
  </div>;
}

type RuntimeState = { label: string; tone: "success" | "warning" | "danger"; detail: string };

function RuntimeDialog({ open, onOpenChange, data, state }: { open: boolean; onOpenChange: (open: boolean) => void; data: DashboardData | null; state: RuntimeState }) {
  const settings = data?.settings || {};
  const runtime = record(data?.runtime);
  const tasks = record(runtime.tasks);
  return <Dialog open={open} onOpenChange={onOpenChange}><DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl"><DialogHeader><DialogTitle>主动唤醒配置</DialogTitle><DialogDescription>同时核对后台配置和 Worker 当前实际生效值。</DialogDescription></DialogHeader>
    <div className={cn("rounded-md border p-3 text-sm", state.tone === "success" ? "border-emerald-200 bg-emerald-50 text-emerald-900" : state.tone === "warning" ? "border-amber-200 bg-amber-50 text-amber-900" : "border-red-200 bg-red-50 text-red-900")}><div className="font-semibold">{state.label}</div><p className="mt-1 text-xs leading-5">{state.detail}</p></div>
    <div className="divide-y rounded-md border text-sm"><ConfigRow label="后台配置" configured={settings.enabled ? "已启用" : "已关闭"} effective={runtime.enabled === true ? "Worker 已启用" : runtime.enabled === false ? "Worker 未启用" : "Worker 未上报"} /><ConfigRow label="沉默候选阈值" configured={`${settings.silence_minutes ?? "未记录"} 分钟`} effective={`${runtime.threshold_minutes ?? "未上报"}${runtime.threshold_minutes != null ? " 分钟" : ""}`} /><ConfigRow label="账号范围" configured={settings.wechat_allowlist?.length ? settings.wechat_allowlist.join("、") : "全部企微号"} effective={runtime.wechat_scope === "all" ? "全部企微号" : runtime.wechat_scope === "allowlist" ? "指定账号" : "未上报"} /><ConfigRow label="客户边界" configured="仅 AI 接待；加微时间不限" effective="发送前再次检查接待、回复、订单与退订状态" /><ConfigRow label="计划模型" configured="由 Worker 配置决定" effective={text(runtime.decision_model) || "未上报"} /><ConfigRow label="计划扫描" configured="后台持续执行" effective={workerTaskLabel(record(tasks.outreach_plan_monitor))} /><ConfigRow label="消息执行" configured="到达计划时间后执行" effective={workerTaskLabel(record(tasks.outreach_task_executor))} /><ConfigRow label="最后扫描" configured="—" effective={formatTime(record(runtime.monitor).last_iteration_finished_at)} /></div>
    <div className="rounded-md bg-zinc-50 p-3 text-xs leading-5 text-zinc-600"><strong className="text-zinc-900">注意：</strong>1 分钟是进入候选判断的最低沉默阈值，不是保证 1 分钟后一定发送。系统还要确认 AI 接待、客户未回复、未成交、未退订，并由模型生成安全的触达计划。</div>
    <div className="flex justify-end"><Button asChild><Link href="/logs/outreach-first-day">查看和修改运行配置</Link></Button></div>
  </DialogContent></Dialog>;
}

function effectiveRuntime(data: DashboardData | null): RuntimeState {
  if (!data) return { label: "正在核对运行状态", tone: "warning", detail: "等待后台配置和 Worker 健康状态返回。" };
  const settings = data.settings || {};
  const runtime = record(data.runtime);
  if (data.runtime_source !== "worker_service") return { label: "Worker 状态不可用", tone: "danger", detail: data.runtime_error || "无法确认主动唤醒任务是否真正运行。" };
  const tasks = record(runtime.tasks);
  const monitor = record(tasks.outreach_plan_monitor);
  const executor = record(tasks.outreach_task_executor);
  if (!settings.enabled) return { label: "配置已关闭", tone: "danger", detail: "后台配置未启用主动唤醒。" };
  if (runtime.enabled !== true || monitor.running !== true || executor.running !== true) return { label: "运行不完整", tone: "danger", detail: "配置已开启，但计划扫描或消息执行任务没有同时运行。" };
  if (number(settings.silence_minutes) !== number(runtime.threshold_minutes)) return { label: "配置尚未生效", tone: "warning", detail: `后台配置为 ${settings.silence_minutes} 分钟，Worker 当前为 ${runtime.threshold_minutes} 分钟，需要重启 Worker。` };
  const configuredScope = settings.wechat_allowlist?.length ? "allowlist" : "all";
  if (text(runtime.wechat_scope) !== configuredScope) return { label: "账号范围不一致", tone: "warning", detail: "后台配置与 Worker 当前账号范围不一致，需要重启 Worker。" };
  return { label: "运行中", tone: "success", detail: "计划扫描和消息执行任务均正常，配置与 Worker 生效值一致。" };
}

function Lifecycle({ nodes, blocked, hasPlan, hasSent, reopened }: { nodes: WorkflowNode[]; blocked: boolean; hasPlan: boolean; hasSent: boolean; reopened?: boolean }) {
  const nodeRan = nodes.some((node) => node.status === "completed" || node.status === "warning");
  const steps = [
    { label: "识别沉默", done: true, current: false },
    { label: blocked ? "状态阻断" : "AI 判断", done: blocked || nodeRan, current: blocked },
    { label: "计划生成", done: hasPlan, current: !blocked && !hasPlan },
    { label: "真实触达", done: hasSent, current: hasPlan && !hasSent },
    { label: "客户结果", done: Boolean(reopened), current: hasSent && !reopened },
  ];
  return <div className="grid grid-cols-5 gap-1 rounded-md border bg-zinc-50 p-3">{steps.map((step, index) => <div key={step.label} className="relative text-center"><div className={cn("mx-auto grid size-7 place-items-center rounded-full border bg-white text-xs", step.done && "border-emerald-500 bg-emerald-500 text-white", step.current && !step.done && "border-blue-500 text-blue-600", blocked && index === 1 && "border-amber-500 bg-amber-500 text-white")}>{step.done ? <Check className="size-3.5" /> : index + 1}</div><div className="mt-1 text-[10px] font-medium text-zinc-600">{step.label}</div>{index < 4 ? <span className="absolute left-[62%] top-3.5 hidden h-px w-[76%] bg-zinc-300 sm:block" /> : null}</div>)}</div>;
}

function Panel({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) { return <section className="min-w-0 rounded-md border bg-white p-4 shadow-sm"><div className="mb-4"><h2 className="text-sm font-semibold">{title}</h2><p className="mt-1 text-xs text-zinc-500">{subtitle}</p></div>{children}</section>; }
function Section({ title, icon, children }: { title: string; icon: React.ReactNode; children: React.ReactNode }) { return <section><h3 className="mb-3 flex items-center gap-2 text-sm font-semibold">{icon}{title}</h3>{children}</section>; }
function Fact({ label, value }: { label: string; value: string }) { return <div className="rounded-md border p-3"><div className="text-xs text-zinc-500">{label}</div><div className="mt-1 text-sm font-medium leading-5">{value}</div></div>; }
function Guard({ label, detail }: { label: string; detail: string }) { return <div className="flex items-start gap-2 rounded-md bg-zinc-50 p-3"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-zinc-500" /><div><div className="text-sm font-medium">{label}</div><div className="mt-1 text-xs text-zinc-500">{detail}</div></div></div>; }
function ConfigRow({ label, configured, effective }: { label: string; configured: string; effective: string }) { return <div className="grid grid-cols-[110px_1fr] gap-3 px-3 py-3"><span className="text-zinc-500">{label}</span><div><div>{configured}</div><div className="mt-1 text-xs text-zinc-500">当前生效：{effective}</div></div></div>; }
function MiniMetric({ label, value }: { label: string; value: string }) { return <div className="bg-white p-3"><div className="text-[11px] text-zinc-500">{label}</div><div className="mt-1 text-sm font-semibold tabular-nums">{value}</div></div>; }
function LegendDot({ tone, label }: { tone: "blue" | "green"; label: string }) { return <span className="inline-flex items-center gap-1.5"><span className={cn("size-2 rounded-full", tone === "blue" ? "bg-blue-600" : "bg-emerald-600")} />{label}</span>; }
function LoadingBlock({ text: label = "正在加载" }: { text?: string }) { return <div className="flex min-h-32 items-center justify-center gap-2 p-4 text-sm text-zinc-500"><LoaderCircle className="size-4 animate-spin" />{label}</div>; }
function EmptyBlock({ text: label }: { text: string }) { return <div className="flex min-h-28 items-center justify-center p-4 text-center text-sm text-zinc-400">{label}</div>; }

function PhaseBadge({ phase }: { phase: string }) { const tone = phase === "failed" ? "bg-red-50 text-red-700" : phase === "blocked" ? "bg-amber-50 text-amber-700" : phase === "reopened" ? "bg-emerald-50 text-emerald-700" : phase.startsWith("waiting") ? "bg-blue-50 text-blue-700" : "bg-zinc-100 text-zinc-700"; return <span className={cn("inline-flex rounded-full px-2 py-0.5 text-[11px] font-medium", tone)}>{PHASE_LABELS[phase] || phase || "处理中"}</span>; }
function StatusBadge({ status }: { status: string }) { const tone = status === "sent" ? "bg-emerald-50 text-emerald-700" : status === "failed" || status === "check_failed" ? "bg-red-50 text-red-700" : status === "skipped" ? "bg-amber-50 text-amber-700" : "bg-blue-50 text-blue-700"; return <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-medium", tone)}>{TASK_STATUS_LABELS[status] || status || "未记录"}</span>; }
function NodeDot({ status }: { status?: string }) { return status === "completed" ? <CheckCircle2 className="size-4 text-emerald-600" /> : status === "failed" ? <AlertTriangle className="size-4 text-red-600" /> : status === "warning" ? <AlertTriangle className="size-4 text-amber-600" /> : <span className="size-4 rounded-full border border-zinc-300" />; }

function workerTaskLabel(task: JsonRecord): string { if (!Object.keys(task).length) return "未上报"; if (task.running === true) return "运行中"; if (task.cancelled === true) return "已取消"; if (task.done === true) return text(task.error) ? `异常：${text(task.error)}` : "已停止"; return "状态未知"; }
function nodeStatusLabel(status?: string): string { return ({ completed: "完成", warning: "有警告", failed: "失败", skipped: "跳过", not_reached: "未到达" } as Record<string, string>)[status || ""] || status || "未记录"; }
function customerLabel(row: Partial<QueueItem>): string { return row.customer_id || row.external_userid || "客户标识未记录"; }
function sceneLabel(value: unknown): string { const key = text(value); return SCENE_LABELS[key] || key; }
function taskScene(task: JsonRecord): unknown { return arrayOfRecords(task.content_source_metadata).find((item) => text(item.scene))?.scene || task.scene; }
function decisionSummary(detail: RunDetail, decision: JsonRecord): string { return [text(decision.customer_need) && `客户需要：${text(decision.customer_need)}`, text(decision.silence_barrier) && `当前卡点：${text(decision.silence_barrier)}`, text(decision.next_business_action) && `下一步：${text(decision.next_business_action)}`].filter(Boolean).join("；") || detail.reason_label || "本轮已形成可执行唤醒计划。"; }
function messageSummary(messages: JsonRecord[]): string { const texts = messages.map(messageText).filter(Boolean); return texts.length ? texts.join(" / ").slice(0, 240) : "任务内容未留存"; }
function eventSummary(payload: JsonRecord): string { return text(payload.reason || payload.reason_code || payload.message || payload.detail) || (Object.keys(payload).length ? "已记录结构化事件" : "无补充说明"); }
function isCustomerMessage(message: JsonRecord): boolean { const role = text(message.role || message.sender_type).toLowerCase(); const direction = text(message.direction).toLowerCase(); return ["user", "customer", "external"].includes(role) || ["customer", "inbound"].includes(direction); }
function messageText(message: JsonRecord): string { const value = message.content ?? message.text ?? ""; if (typeof value === "string") return value; return record(value).text ? text(record(value).text) : record(value).content ? text(record(value).content) : ""; }
function mediaUrl(message: JsonRecord): string { const value = message.url || record(message.content).url || message.oss_url || message.media_url; const url = text(value); return /^https?:\/\//i.test(url) && !url.includes("[REDACTED]") ? url : ""; }
function formatNumber(value: unknown): string { return new Intl.NumberFormat("zh-CN").format(number(value)); }
function percent(value: unknown): string { return `${(number(value) * 100).toFixed(1)}%`; }
function duration(value: unknown): string { const ms = number(value); return ms >= 1000 ? `${(ms / 1000).toFixed(1)} 秒` : `${ms} ms`; }
function trendLabel(value: string, bucket?: string): string { const date = new Date(value); if (Number.isNaN(date.getTime())) return value; return date.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: bucket === "hour" ? "2-digit" : undefined, hour12: false }); }
function formatTime(value: unknown, compact = false): string { const raw = text(value); if (!raw) return "未记录"; const date = new Date(raw); if (Number.isNaN(date.getTime())) return raw; return date.toLocaleString("zh-CN", compact ? { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hour12: false } : { hour12: false }); }
function number(value: unknown): number { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : 0; }
function text(value: unknown): string { return value == null ? "" : String(value).trim(); }
function record(value: unknown): JsonRecord { return value && typeof value === "object" && !Array.isArray(value) ? value as JsonRecord : {}; }
function arrayOfRecords(value: unknown): JsonRecord[] { return Array.isArray(value) ? value.filter((item): item is JsonRecord => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : []; }
