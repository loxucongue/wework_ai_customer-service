"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  CircleDot,
  Clock3,
  Database,
  History,
  Inbox,
  LoaderCircle,
  RefreshCw,
  Search,
  Send,
  UserRound,
  XCircle,
} from "lucide-react";

type JsonRecord = Record<string, unknown>;

type IdentifierItem = {
  key: string;
  value: string;
  source: string;
};

type RunTask = {
  task_id: string;
  sequence: number;
  sequence_state: "selected" | "skipped" | "untouched" | "pending" | "legacy";
  decision?: string;
  reason?: string;
  evidence_refs?: string[];
  consume_status?: number | null;
  consume_remark?: string;
  consume?: { strategy?: string[]; attempted?: boolean; latest_status?: number | null; terminal?: boolean; content_exhausted?: boolean | null; attempts?: unknown[] };
  send?: { decision?: string; submitted?: boolean; delivery_status?: string; error?: string };
  rule_name?: string;
  scene?: { name?: string; code?: string; raw?: JsonRecord };
  use_ai_copy?: boolean | null;
  scheduled_at?: string | number;
  platform_status?: string;
  event_status?: string;
  task_status?: string;
  original_messages?: unknown[];
  error?: string;
};

type RunItem = {
  run_id: string;
  log_version: "batch_v2" | "legacy_single" | "platform_pending";
  version_label: string;
  batch_key?: string;
  biz_type?: string;
  customer_id?: string;
  external_userid?: string;
  corp_id?: string;
  user_id?: string;
  wechat?: string;
  occurred_at?: string | number;
  updated_at?: string;
  status: string;
  status_label: string;
  summary_text: string;
  selected_task_id?: string;
  task_count: number;
  tasks: RunTask[];
  customer_state?: {
    management_mode?: string | null;
    management_source?: string | null;
    customer_opened?: boolean | null;
    same_day_unopened?: boolean | null;
    timeline_structure?: JsonRecord;
  };
  transition_text?: string;
  original_messages?: unknown[];
  final_messages?: unknown[];
  delivery?: {
    status?: string;
    callback_required?: boolean;
    confirmed_at?: string;
    error?: string;
    response?: JsonRecord;
  };
  send?: { decision?: string; submitted?: boolean; delivery_status?: string; error?: string };
  consume?: {
    results?: unknown[];
    completed_count?: number;
    pending_count?: number;
  };
  identifiers?: IdentifierItem[];
  missing_fields?: string[];
  raw_debug?: JsonRecord;
  raw_data?: { platform_tasks?: unknown[]; local_audit?: JsonRecord; message_delivery?: JsonRecord; consume_attempts?: unknown[] };
};

type Summary = {
  visible_total?: number;
  pending?: number;
  processing?: number;
  delivery_pending?: number;
  consume_pending?: number;
  completed?: number;
  no_send?: number;
  exception?: number;
  batch_v2?: number;
  legacy_single?: number;
  platform_pending?: number;
};

type Worker = {
  running?: boolean;
  queue_depth?: number;
  queue_capacity?: number;
  in_flight_count?: number;
  pending_total?: number;
  oldest_due_lag_seconds?: number;
  last_poll_at?: string;
  last_poll_error?: string;
  processing_mode?: string;
  quiet_hours?: { enabled?: boolean; start_hour?: number; end_hour?: number };
};

type ApiResult = {
  schema_version?: string;
  summary?: Summary;
  platform?: { refreshed?: boolean; error?: string; online_service_total?: number; store_visit_total?: number };
  worker?: Worker;
  runs?: RunItem[];
  error?: string;
};

const INITIAL_FILTERS = {
  query: "",
  customer_id: "",
  wechat: "",
  status: "",
  log_version: "",
  biz_type: "",
  date_from: "",
  date_to: "",
  limit: "100",
  refresh_platform: "true",
};

const STATUS_OPTIONS = [
  ["", "全部"],
  ["unfinished", "未完成"],
  ["completed", "已发送"],
  ["no_send", "无需发送"],
  ["exception", "异常"],
] as const;

const VERSION_OPTIONS = [
  ["", "全部版本"],
  ["batch_v2", "顺序批次"],
  ["legacy_single", "历史单任务"],
  ["platform_pending", "平台实时待处理"],
] as const;

export function SopPlatformLogViewer() {
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [data, setData] = useState<ApiResult>({});
  const [selectedId, setSelectedId] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const runs = useMemo(() => data.runs || [], [data.runs]);
  const selected = useMemo(
    () => runs.find((run) => run.run_id === selectedId) || runs[0] || null,
    [runs, selectedId]
  );

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const search = new URLSearchParams(filters);
      const response = await fetch(`/api/logs/sop-platform-runs?${search.toString()}`, { cache: "no-store" });
      const payload = (await response.json()) as ApiResult;
      if (!response.ok) throw new Error(payload.error || "加载第三方 SOP 日志失败");
      const nextRuns = Array.isArray(payload.runs) ? payload.runs : [];
      setData(payload);
      setSelectedId((current) => (nextRuns.some((run) => run.run_id === current) ? current : nextRuns[0]?.run_id || ""));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "加载第三方 SOP 日志失败");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void load();
  }, [load]);

  const summary = data.summary || {};
  const worker = data.worker || {};

  return (
    <main className="flex min-h-screen flex-col bg-slate-50 text-slate-950">
      <header className="flex min-h-16 flex-wrap items-center justify-between gap-3 border-b bg-white px-5 py-3">
        <div>
          <h1 className="text-lg font-semibold">第三方 SOP 运行日志</h1>
          <div className="mt-1 flex flex-wrap items-center gap-3 text-xs text-slate-500">
            <span className="inline-flex items-center gap-1.5">
              <span className={`h-2 w-2 rounded-full ${worker.running ? "bg-emerald-500" : "bg-slate-300"}`} />
              {worker.running ? "任务服务运行中" : "任务服务已关闭"}
            </span>
            <span>队列 {worker.queue_depth || 0}/{worker.queue_capacity || 0}</span>
            <span>执行中 {worker.in_flight_count || 0}</span>
            <span>最近拉取 {formatTime(worker.last_poll_at)}</span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading}
          className="inline-flex h-9 items-center gap-2 rounded-md bg-slate-950 px-4 text-sm text-white disabled:opacity-60"
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          刷新
        </button>
      </header>

      <section className="border-b bg-white px-5 py-3">
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-md border bg-slate-200 sm:grid-cols-4 xl:grid-cols-7 [&>*:last-child]:col-span-2 sm:[&>*:last-child]:col-span-4 xl:[&>*:last-child]:col-span-1">
          <Metric label="等待处理" value={summary.pending} icon={<Inbox className="h-4 w-4" />} />
          <Metric label="处理中" value={summary.processing} icon={<LoaderCircle className="h-4 w-4" />} />
          <Metric label="等待回调" value={summary.delivery_pending} icon={<Send className="h-4 w-4" />} />
          <Metric label="等待消费" value={summary.consume_pending} icon={<Clock3 className="h-4 w-4" />} />
          <Metric label="发送完成" value={summary.completed} icon={<CheckCircle2 className="h-4 w-4" />} />
          <Metric label="无需发送" value={summary.no_send} icon={<XCircle className="h-4 w-4" />} />
          <Metric label="异常" value={summary.exception} icon={<AlertTriangle className="h-4 w-4" />} />
        </div>

        <div className="mt-3 grid gap-2 lg:grid-cols-[minmax(220px,1.3fr)_minmax(150px,.7fr)_repeat(4,minmax(130px,.55fr))_auto]">
          <SearchInput
            value={filters.query}
            placeholder="批次、任务、规则、客户"
            onChange={(value) => setFilters((current) => ({ ...current, query: value }))}
          />
          <CompactInput
            value={filters.customer_id}
            placeholder="客户 ID"
            onChange={(value) => setFilters((current) => ({ ...current, customer_id: value }))}
          />
          <CompactInput
            value={filters.wechat}
            placeholder="企微账号"
            onChange={(value) => setFilters((current) => ({ ...current, wechat: value }))}
          />
          <CompactSelect
            value={filters.status}
            options={STATUS_OPTIONS}
            onChange={(value) => setFilters((current) => ({ ...current, status: value }))}
          />
          <CompactSelect
            value={filters.log_version}
            options={VERSION_OPTIONS}
            onChange={(value) => setFilters((current) => ({ ...current, log_version: value }))}
          />
          <CompactSelect
            value={filters.biz_type}
            options={[["", "全部队列"], ["online_service", "线上客服"], ["store_visit", "门店回访"]]}
            onChange={(value) => setFilters((current) => ({ ...current, biz_type: value }))}
          />
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex h-9 items-center justify-center gap-2 rounded-md border bg-white px-4 text-sm hover:bg-slate-50"
          >
            <Search className="h-4 w-4" />
            查询
          </button>
        </div>

        <details className="mt-2 text-xs text-slate-500">
          <summary className="w-fit cursor-pointer select-none py-1">更多筛选</summary>
          <div className="mt-2 flex flex-wrap gap-2">
            <CompactInput
              type="datetime-local"
              value={filters.date_from}
              onChange={(value) => setFilters((current) => ({ ...current, date_from: value }))}
            />
            <CompactInput
              type="datetime-local"
              value={filters.date_to}
              onChange={(value) => setFilters((current) => ({ ...current, date_to: value }))}
            />
            <CompactSelect
              value={filters.refresh_platform}
              options={[["true", "实时队列 + 历史"], ["false", "仅历史记录"]]}
              onChange={(value) => setFilters((current) => ({ ...current, refresh_platform: value }))}
            />
            <CompactSelect
              value={filters.limit}
              options={[["50", "50 条"], ["100", "100 条"], ["200", "200 条"], ["500", "500 条"]]}
              onChange={(value) => setFilters((current) => ({ ...current, limit: value }))}
            />
          </div>
        </details>

        {data.platform?.error ? <Notice tone="warning">平台实时队列读取失败，当前仅显示历史记录：{data.platform.error}</Notice> : null}
        {worker.last_poll_error ? <Notice tone="warning">最近一次拉取异常：{worker.last_poll_error}</Notice> : null}
        {error ? <Notice tone="error">{error}</Notice> : null}
      </section>

      <section className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[360px_minmax(0,1fr)]">
        <aside className="max-h-[calc(100vh-250px)] overflow-y-auto border-r bg-white">
          <div className="sticky top-0 z-10 flex items-center justify-between border-b bg-white px-4 py-2.5 text-xs text-slate-500">
            <span>{summary.visible_total || runs.length} 个处理批次</span>
            <span>新版 {summary.batch_v2 || 0} · 历史 {summary.legacy_single || 0}</span>
          </div>
          {runs.map((run) => (
            <button
              key={run.run_id}
              type="button"
              onClick={() => setSelectedId(run.run_id)}
              className={`w-full border-b px-4 py-3 text-left hover:bg-slate-50 ${selected?.run_id === run.run_id ? "bg-slate-100" : "bg-white"}`}
            >
              <div className="flex items-center justify-between gap-2">
                <VersionBadge version={run.log_version} label={run.version_label} />
                <StatusBadge status={run.status} label={run.status_label} />
              </div>
              <div className="mt-2 line-clamp-2 text-sm font-medium leading-5">{run.summary_text}</div>
              <div className="mt-2 flex items-center justify-between gap-3 text-xs text-slate-500">
                <span className="truncate">{run.customer_id || run.external_userid || "身份未记录"}</span>
                <span className="shrink-0">{formatTime(run.occurred_at)}</span>
              </div>
              <div className="mt-1 flex items-center justify-between gap-3 text-xs text-slate-400">
                <span>{run.wechat || "企微未记录"}</span>
                <span>{bizTypeLabel(run.biz_type)} · {run.task_count} 条</span>
              </div>
            </button>
          ))}
          {!loading && runs.length === 0 ? <div className="p-10 text-center text-sm text-slate-500">暂无匹配日志</div> : null}
        </aside>

        <div className="max-h-[calc(100vh-250px)] overflow-y-auto">
          {selected ? <RunDetail run={selected} /> : <div className="p-10 text-sm text-slate-500">请选择一个处理批次</div>}
        </div>
      </section>
    </main>
  );
}

function RunDetail({ run }: { run: RunItem }) {
  const state = run.customer_state || {};
  const timeline = state.timeline_structure || {};
  const selectedTask = run.tasks.find((task) => task.task_id === run.selected_task_id);
  const callbackRecorded = Boolean(run.delivery?.status || run.delivery?.confirmed_at || run.delivery?.error);
  const consumeCompleted = (run.consume?.pending_count || 0) === 0 && (run.consume?.completed_count || 0) > 0;

  return (
    <div className="space-y-0">
      <section className="border-b bg-white px-6 py-5">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <VersionBadge version={run.log_version} label={run.version_label} />
              <StatusBadge status={run.status} label={run.status_label} />
              <span className="text-xs text-slate-500">{bizTypeLabel(run.biz_type)}</span>
            </div>
            <h2 className="mt-3 text-lg font-semibold">{run.summary_text}</h2>
            <div className="mt-1 break-all font-mono text-xs text-slate-400">{run.run_id}</div>
          </div>
          <div className="grid grid-cols-2 gap-x-8 gap-y-2 text-sm sm:grid-cols-3">
            <Fact label="客户 ID" value={run.customer_id} />
            <Fact label="企微账号" value={run.wechat} />
            <Fact label="user_wechat_id" value={run.user_id} />
            <Fact label="发生时间" value={formatTime(run.occurred_at)} />
          </div>
        </div>
      </section>

      {run.missing_fields?.length ? (
        <section className="border-b border-amber-200 bg-amber-50 px-6 py-3 text-sm text-amber-900">
          <div className="flex items-start gap-2">
            <History className="mt-0.5 h-4 w-4 shrink-0" />
            <div>该历史版本未记录：{run.missing_fields.join("、")}</div>
          </div>
        </section>
      ) : null}

      <section className="border-b bg-white px-6 py-5">
        <div className="flex items-center justify-between gap-3">
          <h3 className="text-sm font-semibold">全部标识 ID</h3>
          <span className="text-xs text-slate-500">{run.identifiers?.length || 0} 项</span>
        </div>
        <IdentifierPanel identifiers={run.identifiers || []} />
      </section>

      <section className="border-b bg-white px-6 py-5">
        <h3 className="text-sm font-semibold">处理链路</h3>
        <div className="mt-4 grid gap-2 md:grid-cols-5">
          <ProcessStep icon={<Inbox />} label="任务到期" detail={`${run.task_count} 条`} done />
          <ProcessStep
            icon={state.management_mode === "human" ? <UserRound /> : <Bot />}
            label="会话状态"
            detail={managementModeLabel(state.management_mode)}
            done={state.management_mode !== undefined && state.management_mode !== null}
            unknown={run.log_version === "legacy_single"}
          />
          <ProcessStep
            icon={<CircleDot />}
            label="顺序判断"
            detail={run.selected_task_id ? `选中 #${run.selected_task_id}` : run.status === "no_send" ? "均无需发送" : "未完成"}
            done={run.log_version === "batch_v2" && (Boolean(run.selected_task_id) || run.status === "no_send")}
            unknown={run.log_version !== "batch_v2"}
          />
          <ProcessStep
            icon={<Send />}
            label="发送回调"
            detail={run.delivery?.status || (run.selected_task_id ? "尚未回调" : "未发送")}
            done={callbackRecorded || run.status === "completed"}
            unknown={run.log_version === "legacy_single" && !callbackRecorded}
          />
          <ProcessStep
            icon={<Database />}
            label="消费回传"
            detail={consumeCompleted ? "已完成" : run.consume?.pending_count ? `${run.consume.pending_count} 条待处理` : "未记录"}
            done={consumeCompleted}
            unknown={run.log_version === "legacy_single"}
          />
        </div>
      </section>

      <section className="grid border-b bg-white xl:grid-cols-[minmax(0,1.55fr)_minmax(280px,.75fr)]">
        <div className="border-b px-6 py-5 xl:border-b-0 xl:border-r">
          <div className="flex items-center justify-between gap-3">
            <h3 className="text-sm font-semibold">任务顺序</h3>
            <span className="text-xs text-slate-500">严格按计划时间处理</span>
          </div>
          <div className="mt-4 divide-y border-y">
            {run.tasks.map((task) => <TaskSequenceRow key={task.task_id} task={task} />)}
          </div>
        </div>
        <div className="px-6 py-5">
          <h3 className="text-sm font-semibold">会话判断事实</h3>
          <div className="mt-4 grid gap-4 sm:grid-cols-2 xl:grid-cols-1">
            <Fact label="托管状态" value={managementModeLabel(state.management_mode)} />
            <Fact label="状态来源" value={nullableText(state.management_source)} />
            <Fact label="客户是否开口" value={booleanLabel(state.customer_opened)} />
            <Fact label="加微当天未开口" value={booleanLabel(state.same_day_unopened)} />
            <Fact label="会话消息" value={numberFact(timeline.message_count)} />
            <Fact label="客户消息" value={numberFact(timeline.customer_message_count)} />
          </div>
        </div>
      </section>

      <section className="border-b bg-white px-6 py-5">
        <h3 className="text-sm font-semibold">消息内容</h3>
        <div className="mt-4 grid gap-4 xl:grid-cols-2">
          <MessagePanel
            title={selectedTask ? `选中任务 #${selectedTask.task_id} 原始内容` : "平台原始内容"}
            messages={run.original_messages || []}
            empty={run.selected_task_id ? "原始内容未记录" : "本批次没有发送任务"}
          />
          <MessagePanel
            title="实际发送内容"
            messages={run.final_messages || []}
            empty={run.status === "no_send" ? "本批次无需发送" : "尚未形成或未记录实际发送内容"}
          />
        </div>
        {run.transition_text ? (
          <div className="mt-4 border-l-4 border-blue-500 bg-blue-50 px-4 py-3">
            <div className="text-xs font-medium text-blue-700">独立过渡句</div>
            <div className="mt-1 whitespace-pre-wrap text-sm leading-6 text-slate-800">{run.transition_text}</div>
          </div>
        ) : null}
      </section>

      <section className="grid border-b bg-white xl:grid-cols-2">
        <div className="border-b px-6 py-5 xl:border-b-0 xl:border-r">
          <h3 className="text-sm font-semibold">发送结果</h3>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Fact label="是否决定发送" value={sendDecisionLabel(run.send?.decision)} />
            <Fact label="是否实际提交" value={booleanLabel(run.send?.submitted)} />
            <Fact label="实际送达状态" value={run.delivery?.status || run.send?.delivery_status || "未记录"} />
            <Fact label="是否要求送达回调" value={booleanLabel(run.delivery?.callback_required)} />
            <Fact label="送达确认时间" value={formatTime(run.delivery?.confirmed_at)} />
            <Fact label="发送异常" value={run.delivery?.error || "无"} />
          </div>
        </div>
        <div className="px-6 py-5">
          <h3 className="text-sm font-semibold">平台消费回传</h3>
          <div className="mt-4 flex items-center gap-6 text-sm">
            <span>已结束 <strong>{run.consume?.completed_count || 0}</strong></span>
            <span>待处理 <strong>{run.consume?.pending_count || 0}</strong></span>
          </div>
          <ConsumeResults results={run.consume?.results || []} tasks={run.tasks} />
        </div>
      </section>

      <section className="bg-white px-6 py-5">
        <h3 className="text-sm font-semibold">完整原始数据</h3>
        <RawJsonDetails title="第三方原始任务" value={run.raw_data?.platform_tasks || []} />
        <RawJsonDetails title="本地处理审计" value={run.raw_data?.local_audit || run.raw_debug || {}} />
        <RawJsonDetails title="消息发送原始数据" value={run.raw_data?.message_delivery || run.delivery?.response || {}} />
        <RawJsonDetails title="SOP 消费回传原始数据" value={run.raw_data?.consume_attempts || run.consume?.results || []} />
      </section>
    </div>
  );
}

function TaskSequenceRow({ task }: { task: RunTask }) {
  const state = taskSequenceLabel(task.sequence_state);
  return (
    <div className={`grid gap-3 py-4 sm:grid-cols-[36px_minmax(0,1fr)_auto] ${task.sequence_state === "selected" ? "bg-emerald-50/60" : ""}`}>
      <div className="flex h-7 w-7 items-center justify-center rounded-full border bg-white text-xs font-semibold">{task.sequence}</div>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-xs font-semibold">#{task.task_id}</span>
          <span className="text-sm font-medium">{task.rule_name || "未命名任务"}</span>
          <span className="text-xs text-slate-400">{formatTime(task.scheduled_at)}</span>
        </div>
        <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1 text-xs text-slate-500">
          <span>场景：{task.scene?.name || "未记录"}</span>
          <span>编码：{task.scene?.code || "未记录"}</span>
          <span>AI 改写：{booleanLabel(task.use_ai_copy)}</span>
        </div>
        <MessagePreview messages={task.original_messages || []} />
        {task.reason ? <div className="mt-2 text-xs leading-5 text-slate-600">判断：{task.reason}</div> : null}
        {task.error ? <div className="mt-2 text-xs text-red-700">异常：{task.error}</div> : null}
      </div>
      <div className="flex flex-row items-start gap-2 sm:flex-col sm:items-end">
        <span className={`rounded-md border px-2 py-1 text-xs ${state.tone}`}>{state.label}</span>
        <ConsumeBadge status={task.consume_status} />
        <span className="max-w-48 text-right text-xs text-slate-400">{consumeStrategyLabel(task.consume?.strategy)}</span>
      </div>
    </div>
  );
}

function MessagePreview({ messages }: { messages: unknown[] }) {
  const summary = messages.slice(0, 2).map((message) => {
    const item = isRecord(message) ? message : {};
    const type = String(item.type || "unknown");
    const content = item.content;
    if (type === "text") return isRecord(content) ? String(content.text || "") : String(content || "");
    return `[${type}]`;
  }).join(" · ");
  return summary ? <div className="mt-1 line-clamp-2 text-xs leading-5 text-slate-500">{summary}</div> : null;
}

function ConsumeResults({ results, tasks }: { results: unknown[]; tasks: RunTask[] }) {
  const normalized = results.filter(isRecord);
  return (
    <div className="mt-3 space-y-3">
      <div className="grid gap-3 border bg-slate-50 p-3 text-xs sm:grid-cols-3">
        <Fact label="是否已回传" value={booleanLabel(normalized.length > 0)} />
        <Fact label="终态任务" value={`${tasks.filter((task) => task.consume?.terminal).length}/${tasks.length}`} />
        <Fact label="内容是否耗尽" value={triStateLabel(tasks.find((task) => task.consume?.content_exhausted !== null && task.consume?.content_exhausted !== undefined)?.consume?.content_exhausted)} />
      </div>
      <div className="divide-y border-y">
      {normalized.map((result, index) => (
        <div key={`${String(result.attempt_id || result.task_id || "")}-${index}`} className="grid gap-2 py-3 text-xs sm:grid-cols-[minmax(100px,.7fr)_minmax(120px,.8fr)_minmax(100px,.7fr)_minmax(160px,1.3fr)]">
          <span className="font-mono">#{String(result.task_id || "-")}</span>
          <span>{consumeStatusLabel(Number(result.status || 0))}</span>
          <span>{result.success === false ? "回传失败" : result.success === true ? "回传成功" : "历史未记录"}</span>
          <span className={result.error ? "text-red-700" : "text-slate-500"}>{String(result.error || result.remark || result.phase || "-")}</span>
        </div>
      ))}
      {!normalized.length ? <div className="py-3 text-xs text-slate-500">未记录逐条消费响应</div> : null}
      </div>
    </div>
  );
}

function RawJsonDetails({ title, value }: { title: string; value: unknown }) {
  return <details className="mt-3 border"><summary className="cursor-pointer bg-slate-50 px-4 py-3 text-sm font-medium">{title}</summary><pre className="max-h-[32rem] overflow-auto bg-slate-950 p-4 text-xs leading-5 text-slate-100">{JSON.stringify(value, null, 2)}</pre></details>;
}

function IdentifierPanel({ identifiers }: { identifiers: IdentifierItem[] }) {
  if (!identifiers.length) return <div className="mt-3 text-sm text-slate-500">该历史版本未记录标识信息</div>;
  const grouped = identifiers.reduce<Record<string, IdentifierItem[]>>((result, item) => {
    const source = item.source || "其他";
    (result[source] ||= []).push(item);
    return result;
  }, {});
  return (
    <div className="mt-4 grid gap-4 xl:grid-cols-2">
      {Object.entries(grouped).map(([source, items]) => (
        <div key={source} className="min-w-0 border">
          <div className="border-b bg-slate-50 px-3 py-2 text-xs font-medium text-slate-600">{source}</div>
          <div className="divide-y">
            {items.map((item, index) => (
              <div key={`${item.key}-${item.value}-${index}`} className="grid gap-1 px-3 py-2 text-xs sm:grid-cols-[minmax(150px,.8fr)_minmax(0,1.2fr)]">
                <span className="break-all font-mono text-slate-500">{item.key}</span>
                <span className="break-all font-mono text-slate-900">{item.value}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function Metric({ label, value, icon }: { label: string; value?: number; icon: ReactNode }) {
  return (
    <div className="bg-white px-3 py-2.5">
      <div className="flex items-center gap-2 text-xs text-slate-500">{icon}{label}</div>
      <div className="mt-1 text-lg font-semibold tabular-nums">{value || 0}</div>
    </div>
  );
}

function ProcessStep({ icon, label, detail, done, unknown = false }: { icon: ReactNode; label: string; detail: string; done: boolean; unknown?: boolean }) {
  return (
    <div className={`min-w-0 border px-3 py-3 ${done ? "bg-slate-50" : "bg-white"}`}>
      <div className="flex items-center gap-2 text-sm font-medium">
        <span className={`[&>svg]:h-4 [&>svg]:w-4 ${done ? "text-emerald-600" : "text-slate-400"}`}>{icon}</span>
        {label}
      </div>
      <div className="mt-2 truncate text-xs text-slate-500">{unknown ? "历史版本未记录" : detail}</div>
    </div>
  );
}

function MessagePanel({ title, messages, empty }: { title: string; messages: unknown[]; empty: string }) {
  return (
    <div className="border">
      <div className="border-b bg-slate-50 px-4 py-2.5 text-sm font-medium">{title}</div>
      <div className="space-y-3 p-4">
        {messages.map((message, index) => <MessageItem key={index} message={message} index={index} />)}
        {!messages.length ? <div className="py-6 text-center text-sm text-slate-500">{empty}</div> : null}
      </div>
    </div>
  );
}

function MessageItem({ message, index }: { message: unknown; index: number }) {
  const item = isRecord(message) ? message : {};
  const type = String(item.type || "unknown");
  const content = item.content;
  if (type === "text") {
    const text = isRecord(content) ? String(content.text || "") : String(content || "");
    return <div className="bg-slate-50 p-3 text-sm leading-6"><div className="mb-1 text-xs text-slate-400">#{index + 1} 文字</div><div className="whitespace-pre-wrap">{text}</div></div>;
  }
  const url = isRecord(content) ? String(content.url || "") : String(content || "");
  return (
    <div className="bg-slate-50 p-3">
      <div className="mb-2 text-xs text-slate-400">#{index + 1} {type}</div>
      {/* eslint-disable-next-line @next/next/no-img-element -- URLs are runtime audit evidence, not managed site assets. */}
      {type === "image" && url ? <img src={url} alt="SOP 素材" className="mb-2 max-h-56 max-w-full border object-contain" /> : null}
      {type === "video" && url ? <video src={url} controls preload="metadata" className="mb-2 max-h-56 max-w-full border bg-black" /> : null}
      {url ? <a href={url} target="_blank" rel="noreferrer" className="break-all text-xs text-blue-600 hover:underline">{url}</a> : <div className="text-xs text-slate-500">{displayValue(content)}</div>}
    </div>
  );
}

function SearchInput({ value, placeholder, onChange }: { value: string; placeholder: string; onChange: (value: string) => void }) {
  return (
    <label className="relative block">
      <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
      <input value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} className="h-9 w-full rounded-md border bg-white pl-9 pr-3 text-sm" />
    </label>
  );
}

function CompactInput({ value, placeholder = "", type = "text", onChange }: { value: string; placeholder?: string; type?: string; onChange: (value: string) => void }) {
  return <input type={type} value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} className="h-9 min-w-36 rounded-md border bg-white px-3 text-sm" />;
}

function CompactSelect({ value, options, onChange }: { value: string; options: readonly (readonly [string, string])[]; onChange: (value: string) => void }) {
  return (
    <select value={value} onChange={(event) => onChange(event.target.value)} className="h-9 min-w-32 rounded-md border bg-white px-3 text-sm">
      {options.map(([optionValue, label]) => <option key={optionValue} value={optionValue}>{label}</option>)}
    </select>
  );
}

function Notice({ tone, children }: { tone: "warning" | "error"; children: ReactNode }) {
  const styles = tone === "error" ? "border-red-200 bg-red-50 text-red-700" : "border-amber-200 bg-amber-50 text-amber-800";
  return <div className={`mt-3 flex items-center gap-2 rounded-md border px-3 py-2 text-sm ${styles}`}><AlertTriangle className="h-4 w-4 shrink-0" />{children}</div>;
}

function StatusBadge({ status, label }: { status: string; label: string }) {
  const tones: Record<string, string> = {
    pending: "border-slate-200 bg-slate-100 text-slate-700",
    processing: "border-blue-200 bg-blue-50 text-blue-700",
    delivery_pending: "border-indigo-200 bg-indigo-50 text-indigo-700",
    consume_pending: "border-violet-200 bg-violet-50 text-violet-700",
    completed: "border-emerald-200 bg-emerald-50 text-emerald-700",
    no_send: "border-amber-200 bg-amber-50 text-amber-800",
    exception: "border-red-200 bg-red-50 text-red-700",
  };
  return <span className={`rounded-md border px-2 py-1 text-xs ${tones[status] || tones.pending}`}>{label}</span>;
}

function VersionBadge({ version, label }: { version: string; label: string }) {
  const tones: Record<string, string> = {
    batch_v2: "border-cyan-200 bg-cyan-50 text-cyan-800",
    legacy_single: "border-slate-200 bg-white text-slate-600",
    platform_pending: "border-blue-200 bg-blue-50 text-blue-700",
  };
  return <span className={`rounded-md border px-2 py-1 text-xs ${tones[version] || tones.legacy_single}`}>{label}</span>;
}

function ConsumeBadge({ status }: { status?: number | null }) {
  if (!status) return <span className="text-xs text-slate-400">未回传</span>;
  const tone = status === 30 ? "text-emerald-700" : status === 70 ? "text-amber-700" : "text-blue-700";
  return <span className={`text-xs ${tone}`}>{consumeStatusLabel(status)}</span>;
}

function Fact({ label, value }: { label: string; value?: string }) {
  return <div className="min-w-0"><div className="text-xs text-slate-500">{label}</div><div className="mt-1 break-all text-sm">{value || "未记录"}</div></div>;
}

function taskSequenceLabel(state: RunTask["sequence_state"]) {
  const labels = {
    selected: { label: "本轮发送", tone: "border-emerald-200 bg-emerald-50 text-emerald-700" },
    skipped: { label: "过滤并消费", tone: "border-amber-200 bg-amber-50 text-amber-800" },
    untouched: { label: "本轮未判断", tone: "border-slate-200 bg-white text-slate-600" },
    pending: { label: "等待处理", tone: "border-blue-200 bg-blue-50 text-blue-700" },
    legacy: { label: "历史单任务", tone: "border-slate-200 bg-slate-100 text-slate-600" },
  };
  return labels[state] || labels.legacy;
}

function managementModeLabel(value: unknown) {
  if (value === "ai") return "AI 托管";
  if (value === "human") return "人工接管";
  return "未记录";
}

function booleanLabel(value: unknown) {
  if (value === true) return "是";
  if (value === false) return "否";
  return "未记录";
}

function triStateLabel(value: unknown) {
  if (value === true) return "是";
  if (value === false) return "否";
  return "未记录";
}

function sendDecisionLabel(value?: string) {
  if (value === "send") return "是";
  if (value === "no_send" || value === "skip") return "否";
  return "未记录";
}

function numberFact(value: unknown) {
  return typeof value === "number" ? `${value} 条` : "未记录";
}

function nullableText(value: unknown) {
  return typeof value === "string" && value ? value : "未记录";
}

function bizTypeLabel(value?: string) {
  return value === "store_visit" ? "门店回访" : value === "online_service" ? "线上客服" : "队列未记录";
}

function consumeStatusLabel(status: number) {
  if (status === 20) return "20 发送中";
  if (status === 30) return "30 发送成功";
  if (status === 70) return "70 无需发送";
  return status ? String(status) : "未回传";
}

function consumeStrategyLabel(strategy?: string[]) {
  if (!strategy?.length) return "无回传策略";
  return strategy.map((item) => item === "20_before_send" ? "发送前回传 20" : item === "30_after_delivery" ? "送达后回传 30" : item === "70_without_send" ? "无需发送回传 70" : item).join(" → ");
}

function formatTime(value?: string | number) {
  if (value === undefined || value === null || value === "") return "未记录";
  const raw = typeof value === "number" || /^\d+(\.\d+)?$/.test(String(value)) ? Number(value) : value;
  const date = typeof raw === "number" ? new Date(raw > 10_000_000_000 ? raw : raw * 1000) : new Date(raw);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false }).format(date);
}

function isRecord(value: unknown): value is JsonRecord {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function displayValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value, null, 2);
}
