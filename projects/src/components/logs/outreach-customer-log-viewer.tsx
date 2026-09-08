"use client";

import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  CalendarClock,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleSlash2,
  ClipboardList,
  Clock3,
  FileJson2,
  Filter,
  LoaderCircle,
  RefreshCw,
  Search,
  Send,
  Settings2,
  UsersRound,
  XCircle,
} from "lucide-react";

type JsonRecord = Record<string, unknown>;

type TaskSummary = {
  total: number;
  handled: number;
  sent: number;
  sent_without_message_id: number;
  consumed: number;
  failed: number;
  pending: number;
  processing: number;
  status_counts: Record<string, number>;
  next_task?: NextTask;
};

type NextTask = {
  task_id: string;
  plan_id: string;
  step_index: number;
  status: string;
  scheduled_at: string;
  message_goal: string;
};

type Identity = {
  corp_id?: string;
  wechat?: string;
  external_userid?: string;
  customer_id?: string;
  user_id?: string;
  customer_add_wechat_id?: string;
  conversation_id?: string;
  customer_name?: string;
  customer_ids?: string[];
  user_ids?: string[];
  customer_add_wechat_ids?: string[];
  conversation_ids?: string[];
};

type OutreachRecord = {
  record_id: string;
  record_type: "plan" | "no_plan";
  plan_id?: string;
  workflow_run_id?: string;
  source_type: string;
  status: string;
  reason_code: string;
  plan_goal?: string;
  customer_stage?: string;
  customer_psychology?: string;
  created_at: string;
  updated_at?: string;
  event_summary?: string;
  record_count?: number;
  cycle_customer_message_at?: string;
  task_summary: TaskSummary;
};

type CustomerItem = {
  contact_key: string;
  identity_state: "complete" | "incomplete";
  identity: Identity;
  latest_at: string;
  latest_record: Pick<OutreachRecord, "record_id" | "record_type" | "plan_id" | "source_type" | "status" | "reason_code" | "plan_goal" | "created_at">;
  plan_count: number;
  no_plan_count: number;
  task_summary: TaskSummary;
  next_task?: NextTask;
};

type Metrics = {
  customer_count: number;
  identity_incomplete_count: number;
  plan_count: number;
  no_plan_count: number;
  task_count: number;
  sent_count: number;
  sent_without_message_id_count: number;
  consumed_count: number;
  pending_count: number;
  processing_count: number;
  failed_count: number;
};

type CustomerLogResponse = {
  items: CustomerItem[];
  metrics: Metrics;
  next_cursor: string;
  has_more: boolean;
};

type CustomerDetail = {
  contact_key: string;
  identity: Identity;
  history: OutreachRecord[];
};

type OutreachTask = JsonRecord & {
  id?: string;
  step_index?: number;
  status?: string;
  scheduled_at?: string;
  sent_at?: string;
  send_status?: string;
  system_msgid?: string;
  error_message?: string;
  message_goal?: string;
  actual_send?: boolean;
  reply_messages?: unknown[];
  content_sources?: unknown[];
  content_source_metadata?: unknown;
  before_send_check?: boolean;
};

type PlanDetail = {
  contact_key: string;
  identity: Identity;
  source_type: string;
  reason_code: string;
  plan: JsonRecord;
  task_summary: TaskSummary;
  tasks: OutreachTask[];
  events: JsonRecord[];
  technical?: JsonRecord;
};

type Filters = {
  started_from: string;
  started_to: string;
  corp_id: string;
  wechat: string;
  identity_query: string;
  customer_id: string;
  external_userid: string;
  source_type: string;
  plan_status: string;
  task_status: string;
  reason_code: string;
  identity_state: string;
};

type FirstDaySettings = {
  enabled: boolean;
  silence_minutes: number;
  wechat_allowlist: string[];
  wechat_allowlist_raw: string;
};

const EMPTY_METRICS: Metrics = {
  customer_count: 0,
  identity_incomplete_count: 0,
  plan_count: 0,
  no_plan_count: 0,
  task_count: 0,
  sent_count: 0,
  sent_without_message_id_count: 0,
  consumed_count: 0,
  pending_count: 0,
  processing_count: 0,
  failed_count: 0,
};

const SOURCE_LABELS: Record<string, string> = {
  first_day: "沉默客户唤醒",
  followup_strategy: "自动跟进策略",
  closing_sequence: "自动成交序列",
  auto_approved: "自动审批计划",
};

const PLAN_STATUS_LABELS: Record<string, string> = {
  active: "执行中",
  created: "已生成",
  completed: "已完成",
  cancelled: "已取消",
  blocked: "已拦截",
  waiting: "等待下一任务",
  draft: "待审批",
  paused: "已暂停",
  failed: "失败",
  no_plan: "未生成计划",
};

const TASK_STATUS_LABELS: Record<string, string> = {
  pending: "待执行",
  checking: "发送前检查中",
  sending: "发送中",
  sent: "已发送",
  skipped: "已消费/无需发送",
  cancelled: "已消费/无需发送",
  completed_without_send: "已消费/无需发送",
  shadow_no_send: "已消费/无需发送",
  shadowed: "已处理/未发送",
  failed: "发送失败",
  check_failed: "发送前检查失败",
  partial_failed: "部分发送失败",
};

const REASON_LABELS: Record<string, string> = {
  human_takeover: "人工接管",
  manual_takeover_active: "人工接管",
  customer_deleted: "客户关系失效",
  customer_replied: "客户已回复",
  customer_relation_unavailable: "客户关系状态不可用",
  stop_contact: "客户已退订",
  plan_rejected: "计划未通过",
  workflow_failed: "计划生成失败",
  missing_identity: "客户身份不完整",
  activity_followup: "自动策略跟进",
  first_day_opened_silence: "客户开口后沉默唤醒",
  outreach_cycle_completed_without_new_customer_reply: "本轮计划已完成，等待客户再次开口",
  conversation_fingerprint_already_evaluated: "同一轮客户消息已评估",
  conversation_fingerprint_already_logged: "同一轮客户消息已有记录",
  no_plan: "未生成计划",
};

function localDateTime(daysAgo: number): string {
  const date = new Date(Date.now() - daysAgo * 24 * 60 * 60 * 1000);
  const timezoneOffset = date.getTimezoneOffset() * 60_000;
  return new Date(date.getTime() - timezoneOffset).toISOString().slice(0, 16);
}

const DEFAULT_FILTERS: Filters = {
  started_from: localDateTime(7),
  started_to: localDateTime(0),
  corp_id: "",
  wechat: "",
  identity_query: "",
  customer_id: "",
  external_userid: "",
  source_type: "",
  plan_status: "",
  task_status: "",
  reason_code: "",
  identity_state: "",
};

export function OutreachCustomerLogViewer() {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [items, setItems] = useState<CustomerItem[]>([]);
  const [metrics, setMetrics] = useState<Metrics>(EMPTY_METRICS);
  const [selectedContactKey, setSelectedContactKey] = useState("");
  const [customerDetail, setCustomerDetail] = useState<CustomerDetail | null>(null);
  const [selectedPlanId, setSelectedPlanId] = useState("");
  const [planDetail, setPlanDetail] = useState<PlanDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [customerLoading, setCustomerLoading] = useState(false);
  const [planLoading, setPlanLoading] = useState(false);
  const [error, setError] = useState("");
  const [nextCursor, setNextCursor] = useState("");
  const [activeCursor, setActiveCursor] = useState("");
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [filtersExpanded, setFiltersExpanded] = useState(false);

  const loadCustomers = useCallback(async (cursor = "") => {
    setLoading(true);
    setError("");
    const query = new URLSearchParams({ limit: "50" });
    for (const [key, value] of Object.entries(filters)) {
      if (!value) continue;
      query.set(
        key,
        key === "started_from" || key === "started_to" ? new Date(value).toISOString() : value,
      );
    }
    if (cursor) query.set("cursor", cursor);
    try {
      const response = await fetch(`/api/outreach/customer-logs?${query.toString()}`, { cache: "no-store" });
      const data = (await response.json()) as CustomerLogResponse & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "加载沉默唤醒客户日志失败");
      const nextItems = Array.isArray(data.items) ? data.items : [];
      setItems(nextItems);
      setMetrics(data.metrics || EMPTY_METRICS);
      setNextCursor(data.next_cursor || "");
      setActiveCursor(cursor);
      setSelectedContactKey((current) => (
        nextItems.some((item) => item.contact_key === current) ? current : nextItems[0]?.contact_key || ""
      ));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载沉默唤醒客户日志失败");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const loadCustomer = useCallback(async (contactKey: string) => {
    if (!contactKey) {
      setCustomerDetail(null);
      setSelectedPlanId("");
      return;
    }
    if (contactKey.startsWith("run:") || contactKey.startsWith("event:")) {
      setCustomerDetail({ contact_key: contactKey, identity: {}, history: [] });
      setSelectedPlanId("");
      setPlanDetail(null);
      return;
    }
    setCustomerLoading(true);
    setCustomerDetail(null);
    setPlanDetail(null);
    setError("");
    const query = new URLSearchParams({
      started_from: new Date(filters.started_from).toISOString(),
      started_to: new Date(filters.started_to).toISOString(),
    });
    try {
      const response = await fetch(
        `/api/outreach/customer-logs/${encodeURIComponent(contactKey)}?${query.toString()}`,
        { cache: "no-store" },
      );
      const data = (await response.json()) as CustomerDetail & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "加载客户计划时间线失败");
      const history = Array.isArray(data.history) ? data.history : [];
      setCustomerDetail({ ...data, history });
      setSelectedPlanId((current) => (
        history.some((record) => record.plan_id === current)
          ? current
          : history.find((record) => record.record_type === "plan")?.plan_id || ""
      ));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载客户计划时间线失败");
    } finally {
      setCustomerLoading(false);
    }
  }, [filters.started_from, filters.started_to]);

  const loadPlan = useCallback(async (contactKey: string, planId: string) => {
    if (!contactKey || !planId) {
      setPlanDetail(null);
      return;
    }
    setPlanLoading(true);
    setPlanDetail(null);
    setError("");
    try {
      const response = await fetch(
        `/api/outreach/customer-logs/${encodeURIComponent(contactKey)}/plans/${encodeURIComponent(planId)}`,
        { cache: "no-store" },
      );
      const data = (await response.json()) as PlanDetail & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "加载计划任务详情失败");
      setPlanDetail(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载计划任务详情失败");
    } finally {
      setPlanLoading(false);
    }
  }, []);

  useEffect(() => { void loadCustomers(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { void loadCustomer(selectedContactKey); }, [loadCustomer, selectedContactKey]);
  useEffect(() => { void loadPlan(selectedContactKey, selectedPlanId); }, [loadPlan, selectedContactKey, selectedPlanId]);

  const selectedCustomer = items.find((item) => item.contact_key === selectedContactKey) || null;
  const activeFilterCount = useMemo(
    () => Object.entries(filters).filter(([key, value]) => key !== "started_from" && key !== "started_to" && Boolean(value)).length,
    [filters],
  );

  const search = () => {
    setCursorHistory([]);
    setSelectedContactKey("");
    setCustomerDetail(null);
    setSelectedPlanId("");
    setPlanDetail(null);
    void loadCustomers();
  };

  const refresh = () => {
    void loadCustomers(activeCursor);
    if (selectedContactKey) void loadCustomer(selectedContactKey);
    if (selectedContactKey && selectedPlanId) void loadPlan(selectedContactKey, selectedPlanId);
  };

  return (
    <main className="min-h-screen bg-zinc-100 px-3 py-4 text-zinc-950 sm:px-5 lg:px-7">
      <div className="mx-auto max-w-[1680px]">
        <header className="flex flex-col gap-3 border-b border-zinc-200 pb-4 sm:flex-row sm:items-end sm:justify-between">
          <div>
            <div className="flex items-center gap-2 text-xl font-semibold"><UsersRound className="h-5 w-5 text-blue-700" />千人千面沉默唤醒</div>
            <p className="mt-1 text-sm text-zinc-600">按客户查看自动沉默触达计划、任务处理结果与下一步，不包含人工计划。</p>
          </div>
          <button type="button" title="刷新" onClick={refresh} disabled={loading} className="inline-flex h-9 items-center justify-center gap-2 self-start rounded-md bg-zinc-900 px-3 text-sm font-medium text-white disabled:opacity-50 sm:self-auto">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />刷新
          </button>
        </header>

        <section className="mt-4 border border-zinc-200 bg-white p-4 shadow-sm">
          <div className="flex flex-col gap-3 xl:flex-row xl:items-end">
            <div className="grid flex-1 gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <Field label="开始时间"><input type="datetime-local" value={filters.started_from} onChange={(event) => setFilters((value) => ({ ...value, started_from: event.target.value }))} className={inputClassName} /></Field>
              <Field label="结束时间"><input type="datetime-local" value={filters.started_to} onChange={(event) => setFilters((value) => ({ ...value, started_to: event.target.value }))} className={inputClassName} /></Field>
              <Field label="企微企业"><input value={filters.corp_id} onChange={(event) => setFilters((value) => ({ ...value, corp_id: event.target.value }))} placeholder="corp_id" className={inputClassName} /></Field>
              <Field label="接待企微"><input value={filters.wechat} onChange={(event) => setFilters((value) => ({ ...value, wechat: event.target.value }))} placeholder="企微账号" className={inputClassName} /></Field>
              <Field label="客户 / 外部联系人 ID"><input value={filters.identity_query} onChange={(event) => setFilters((value) => ({ ...value, identity_query: event.target.value }))} placeholder="精确查询" className={inputClassName} /></Field>
            </div>
            <div className="flex flex-wrap gap-2">
              {[7, 30, 90].map((days) => <button key={days} type="button" onClick={() => setFilters((value) => ({ ...value, started_from: localDateTime(days), started_to: localDateTime(0) }))} className="h-9 rounded-md border border-zinc-200 px-3 text-sm hover:bg-zinc-50">近 {days} 天</button>)}
              <button type="button" onClick={search} className="inline-flex h-9 items-center gap-2 rounded-md bg-blue-700 px-3 text-sm font-medium text-white"><Search className="h-4 w-4" />查询</button>
            </div>
          </div>
          <button type="button" aria-expanded={filtersExpanded} onClick={() => setFiltersExpanded((value) => !value)} className="mt-3 inline-flex items-center gap-2 text-sm text-zinc-700 hover:text-zinc-950">
            <Filter className="h-4 w-4" />更多筛选{activeFilterCount ? <span className="rounded-full bg-zinc-900 px-2 py-0.5 text-xs text-white">{activeFilterCount}</span> : null}{filtersExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </button>
          {filtersExpanded ? <AdvancedFilters filters={filters} setFilters={setFilters} onReset={() => setFilters(DEFAULT_FILTERS)} /> : null}
        </section>

        <OutreachSettingsPanel />
        <MetricsOverview metrics={metrics} />
        {metrics.sent_without_message_id_count > 0 ? <div className="mt-3 flex gap-2 border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{metrics.sent_without_message_id_count} 条任务状态为“已发送”但未记录平台消息 ID，未计入实际发送，需要在技术记录中核验。</div> : null}
        {metrics.identity_incomplete_count > 0 ? <div className="mt-3 flex gap-2 border border-zinc-300 bg-zinc-50 px-3 py-2 text-sm text-zinc-700"><CircleSlash2 className="mt-0.5 h-4 w-4 shrink-0" />{metrics.identity_incomplete_count} 条记录身份不完整，已单独展示，不会与任何客户合并。</div> : null}
        {error ? <div className="mt-3 flex gap-2 border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-800"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />{error}</div> : null}

        <section className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.1fr)_minmax(0,0.95fr)_minmax(0,1fr)]">
          <CustomerPanel items={items} selectedContactKey={selectedContactKey} loading={loading} onSelect={(contactKey) => { setSelectedContactKey(contactKey); setSelectedPlanId(""); }} />
          <TimelinePanel customer={selectedCustomer} detail={customerDetail} loading={customerLoading} selectedPlanId={selectedPlanId} onSelectPlan={setSelectedPlanId} />
          <PlanDetailPanel detail={planDetail} loading={planLoading} />
        </section>

        <footer className="mt-4 flex items-center justify-between border-t border-zinc-200 pt-3 text-sm text-zinc-600">
          <span>每页 50 位客户</span>
          <div className="flex gap-2">
            <button type="button" title="上一页" disabled={!cursorHistory.length || loading} onClick={() => { const previous = cursorHistory.at(-1) || ""; setCursorHistory((history) => history.slice(0, -1)); void loadCustomers(previous); }} className={iconButtonClass}><ChevronLeft className="h-4 w-4" /></button>
            <button type="button" title="下一页" disabled={!nextCursor || loading} onClick={() => { setCursorHistory((history) => [...history, activeCursor]); void loadCustomers(nextCursor); }} className={iconButtonClass}><ChevronRight className="h-4 w-4" /></button>
          </div>
        </footer>
      </div>
    </main>
  );
}

function AdvancedFilters({ filters, setFilters, onReset }: { filters: Filters; setFilters: (updater: (value: Filters) => Filters) => void; onReset: () => void }) {
  return <div className="mt-3 grid gap-3 border-t border-zinc-100 pt-3 sm:grid-cols-2 xl:grid-cols-5">
    <SelectField label="触达来源" value={filters.source_type} onChange={(source_type) => setFilters((value) => ({ ...value, source_type }))} options={Object.entries(SOURCE_LABELS)} />
    <SelectField label="计划状态" value={filters.plan_status} onChange={(plan_status) => setFilters((value) => ({ ...value, plan_status }))} options={Object.entries(PLAN_STATUS_LABELS)} />
    <SelectField label="任务状态" value={filters.task_status} onChange={(task_status) => setFilters((value) => ({ ...value, task_status }))} options={Object.entries(TASK_STATUS_LABELS)} />
    <Field label="原因码"><input value={filters.reason_code} onChange={(event) => setFilters((value) => ({ ...value, reason_code: event.target.value }))} placeholder="如 human_takeover" className={inputClassName} /></Field>
    <div className="flex items-end gap-2"><SelectField label="身份状态" value={filters.identity_state} onChange={(identity_state) => setFilters((value) => ({ ...value, identity_state }))} options={[["complete", "身份完整"], ["incomplete", "身份不完整"]]} /><button type="button" onClick={onReset} className="h-9 shrink-0 rounded-md border border-zinc-200 px-3 text-sm hover:bg-zinc-50">重置</button></div>
  </div>;
}

function OutreachSettingsPanel() {
  const [expanded, setExpanded] = useState(false);
  const [settings, setSettings] = useState<FirstDaySettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [silenceMinutes, setSilenceMinutes] = useState("1");
  const [allowlist, setAllowlist] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const loadSettings = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/outreach/first-day-settings", { cache: "no-store" });
      const data = (await response.json()) as FirstDaySettings & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "加载运行规则失败");
      setSettings(data);
      setEnabled(Boolean(data.enabled));
      setSilenceMinutes(String(data.silence_minutes || 1));
      setAllowlist(data.wechat_allowlist_raw || "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载运行规则失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { if (expanded && !settings) void loadSettings(); }, [expanded, loadSettings, settings]);

  const save = async () => {
    setSaving(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch("/api/outreach/first-day-settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json; charset=utf-8" },
        body: JSON.stringify({ enabled, silence_minutes: Number(silenceMinutes), wechat_allowlist: allowlist }),
      });
      const data = (await response.json()) as FirstDaySettings & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "保存运行规则失败");
      setSettings(data);
      setMessage("运行规则已保存。");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "保存运行规则失败");
    } finally {
      setSaving(false);
    }
  };

  const summary = settings
    ? `${settings.enabled ? "已启用" : "已关闭"}，沉默 ${settings.silence_minutes} 分钟，${settings.wechat_allowlist?.length ? `${settings.wechat_allowlist.length} 个企微账号` : "全部企微账号"}`
    : "展开后读取当前配置";

  return <section className="mt-4 border border-zinc-200 bg-white">
    <button type="button" onClick={() => setExpanded((value) => !value)} className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left hover:bg-zinc-50">
      <span className="flex min-w-0 items-center gap-2"><Settings2 className="h-4 w-4 shrink-0 text-zinc-600" /><span><span className="block text-sm font-semibold">运行规则</span><span className="block truncate text-xs font-normal text-zinc-500">{summary}</span></span></span>
      {expanded ? <ChevronUp className="h-4 w-4 shrink-0" /> : <ChevronDown className="h-4 w-4 shrink-0" />}
    </button>
    {expanded ? <div className="grid gap-3 border-t border-zinc-200 p-4 md:grid-cols-[1.1fr_0.7fr_1.4fr_auto] md:items-end">
      <label className="flex h-9 items-center justify-between gap-3 border border-zinc-200 px-3 text-sm"><span>启用沉默客户唤醒</span><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} className="h-4 w-4" /></label>
      <Field label="沉默分钟数"><input type="number" min={1} max={120} value={silenceMinutes} onChange={(event) => setSilenceMinutes(event.target.value)} className={inputClassName} /></Field>
      <Field label="企微账号范围（留空为全部）"><input value={allowlist} onChange={(event) => setAllowlist(event.target.value)} placeholder="SL8003, DY258" className={inputClassName} /></Field>
      <div className="flex gap-2"><button type="button" title="刷新配置" onClick={() => void loadSettings()} disabled={loading} className={iconButtonClass}><RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} /></button><button type="button" onClick={() => void save()} disabled={saving || loading} className="h-9 rounded-md bg-zinc-900 px-3 text-sm font-medium text-white disabled:opacity-50">{saving ? "保存中" : "保存"}</button></div>
      {error ? <div className="text-sm text-red-700 md:col-span-4">{error}</div> : null}{message ? <div className="text-sm text-emerald-700 md:col-span-4">{message}</div> : null}
    </div> : null}
  </section>;
}

function MetricsOverview({ metrics }: { metrics: Metrics }) {
  const items = [
    ["触达客户", metrics.customer_count, UsersRound, "zinc"],
    ["已生成计划", metrics.plan_count, ClipboardList, "blue"],
    ["计划任务", metrics.task_count, CalendarClock, "zinc"],
    ["已发送", metrics.sent_count, Send, "emerald"],
    ["已消费/无需发送", metrics.consumed_count, CircleSlash2, "amber"],
    ["待执行", metrics.pending_count + metrics.processing_count, Clock3, "blue"],
    ["失败", metrics.failed_count, XCircle, "red"],
  ] as const;
  return <section className="mt-4 grid gap-px overflow-hidden border border-zinc-200 bg-zinc-200 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
    {items.map(([label, value, Icon, tone]) => <div key={label} className="bg-white px-4 py-3"><div className="flex items-center justify-between gap-2 text-xs text-zinc-500"><span>{label}</span><Icon className={`h-4 w-4 ${metricIconTone(tone)}`} /></div><div className="mt-2 text-2xl font-semibold tabular-nums">{value}</div></div>)}
  </section>;
}

function CustomerPanel({ items, selectedContactKey, loading, onSelect }: { items: CustomerItem[]; selectedContactKey: string; loading: boolean; onSelect: (contactKey: string) => void }) {
  return <section className="min-w-0 border border-zinc-200 bg-white">
    <PanelHeader icon={<UsersRound className="h-4 w-4" />} title="客户" subtitle="每行代表一个严格隔离的客户接待边界" />
    <div className="max-h-[720px] overflow-y-auto">
      {loading && !items.length ? <EmptyState icon={<LoaderCircle className="h-5 w-5 animate-spin" />} text="正在加载客户日志" /> : null}
      {!loading && !items.length ? <EmptyState icon={<Search className="h-5 w-5" />} text="当前筛选范围没有自动触达记录" /> : null}
      {items.map((item) => <button key={item.contact_key} type="button" onClick={() => onSelect(item.contact_key)} className={`block w-full border-b border-zinc-100 px-4 py-3 text-left last:border-b-0 ${item.contact_key === selectedContactKey ? "border-l-2 border-l-blue-600 bg-blue-50 pl-[14px]" : "hover:bg-zinc-50"}`}>
        <div className="flex items-start justify-between gap-3"><div className="min-w-0"><div className="break-all text-sm font-semibold">{identityName(item.identity) || "不可归属记录"}</div>{item.identity.customer_name ? <div className="mt-1 text-xs text-zinc-600">{item.identity.customer_name}</div> : null}</div><CustomerStatusPill item={item} /></div>
        <div className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 border-t border-zinc-200 pt-3 text-xs">
          <CompactIdentity label="客户 ID" value={identityValues(item.identity.customer_ids, item.identity.customer_id)} />
          <CompactIdentity label="加微 ID" value={identityValues(item.identity.customer_add_wechat_ids, item.identity.customer_add_wechat_id)} />
          <CompactIdentity label="外部联系人" value={item.identity.external_userid || "未记录"} />
          <CompactIdentity label="接待人员" value={identityValues(item.identity.user_ids, item.identity.user_id)} />
          <CompactIdentity label="企微" value={item.identity.wechat || "未记录"} />
          <CompactIdentity label="企业" value={item.identity.corp_id || "未记录"} />
          <div className="col-span-2"><CompactIdentity label="会话 ID" value={identityValues(item.identity.conversation_ids, item.identity.conversation_id)} /></div>
        </div>
        <div className="mt-3 grid grid-cols-3 gap-2 text-xs"><SummaryValue label="计划" value={String(item.plan_count)} /><SummaryValue label="已处理" value={`${item.task_summary.handled}/${item.task_summary.total}`} /><SummaryValue label="实际发送" value={String(item.task_summary.sent)} /></div>
        <div className="mt-2 truncate text-xs text-zinc-600">{item.next_task?.scheduled_at ? `下一任务：${formatTime(item.next_task.scheduled_at)}` : item.no_plan_count ? `另有 ${item.no_plan_count} 次扫描未建计划` : "当前无待执行任务"}</div>
      </button>)}
    </div>
  </section>;
}

function TimelinePanel({ customer, detail, loading, selectedPlanId, onSelectPlan }: { customer: CustomerItem | null; detail: CustomerDetail | null; loading: boolean; selectedPlanId: string; onSelectPlan: (planId: string) => void }) {
  const history = detail?.history || [];
  const plans = history.filter((record) => record.record_type === "plan");
  const evaluations = history.filter((record) => record.record_type === "no_plan");
  return <section className="min-w-0 border border-zinc-200 bg-white">
    <PanelHeader icon={<CalendarClock className="h-4 w-4" />} title="客户触达记录" subtitle={customer ? `真实计划 ${plans.length} 个 · 未建计划评估 ${evaluations.length} 次` : "选择一位客户后查看"} />
    <div className="max-h-[720px] overflow-y-auto p-3">
      {loading ? <EmptyState icon={<LoaderCircle className="h-5 w-5 animate-spin" />} text="正在加载客户计划" /> : null}
      {!loading && !customer ? <EmptyState icon={<UsersRound className="h-5 w-5" />} text="从左侧选择客户" /> : null}
      {!loading && customer && !history.length ? <EmptyState icon={<CircleSlash2 className="h-5 w-5" />} text="筛选范围内没有可展示的计划" /> : null}
      {plans.length ? <div className="mb-2 text-xs font-medium text-zinc-500">真实创建的计划</div> : null}
      {plans.map((record) => <TimelineRecord key={record.record_id} record={record} selected={record.plan_id === selectedPlanId} onSelect={onSelectPlan} />)}
      {evaluations.length ? <NoPlanEvaluations records={evaluations} /> : null}
    </div>
  </section>;
}

function TimelineRecord({ record, selected, onSelect }: { record: OutreachRecord; selected: boolean; onSelect: (planId: string) => void }) {
  const selectable = record.record_type === "plan" && Boolean(record.plan_id);
  const content = <><div className="flex items-start justify-between gap-2"><div className="min-w-0"><div className="truncate text-sm font-semibold">{record.record_type === "no_plan" ? "未生成计划" : SOURCE_LABELS[record.source_type] || record.source_type}</div><div className="mt-1 text-xs text-zinc-500">计划创建：{formatTime(record.created_at)}</div></div><StatusPill status={record.status} noPlan={record.record_type === "no_plan"} /></div>{record.cycle_customer_message_at ? <div className="mt-2 border-l-2 border-blue-500 bg-blue-50 px-2 py-1.5 text-xs text-blue-900">本轮客户最后开口：{formatTime(record.cycle_customer_message_at)}</div> : null}<div className="mt-3 text-sm leading-5 text-zinc-700">{record.plan_goal || reasonLabel(record.reason_code) || record.event_summary || "未记录业务摘要"}</div><div className="mt-3 flex flex-wrap gap-2 text-xs text-zinc-600"><span>任务 {record.task_summary.total}</span><span>实际发送 {record.task_summary.sent}</span><span>已消费 {record.task_summary.consumed}</span><span>失败 {record.task_summary.failed}</span></div>{record.reason_code ? <div className="mt-2 text-xs text-zinc-500">原因：{reasonLabel(record.reason_code)}</div> : null}</>;
  const className = `w-full border p-3 text-left ${selected ? "border-blue-500 bg-blue-50" : "border-zinc-200 bg-white"}`;
  if (!selectable) return <div className={`${className} mb-3 opacity-80`}>{content}</div>;
  return <button type="button" onClick={() => onSelect(record.plan_id || "")} className={`${className} mb-3 hover:border-zinc-400`}>{content}</button>;
}

function PlanDetailPanel({ detail, loading }: { detail: PlanDetail | null; loading: boolean }) {
  if (loading) return <section className="border border-zinc-200 bg-white"><PanelHeader icon={<ClipboardList className="h-4 w-4" />} title="计划任务详情" subtitle="正在加载" /><EmptyState icon={<LoaderCircle className="h-5 w-5 animate-spin" />} text="正在加载任务与审计结果" /></section>;
  if (!detail) return <section className="border border-zinc-200 bg-white"><PanelHeader icon={<ClipboardList className="h-4 w-4" />} title="计划任务详情" subtitle="选择一条已生成计划后查看" /><EmptyState icon={<ClipboardList className="h-5 w-5" />} text="计划任务、实际发送和消费原因会在这里展示" /></section>;
  const plan = detail.plan || {};
  return <section className="min-w-0 border border-zinc-200 bg-white">
    <PanelHeader icon={<ClipboardList className="h-4 w-4" />} title="计划任务详情" subtitle={`${SOURCE_LABELS[detail.source_type] || detail.source_type} · ${reasonLabel(detail.reason_code)}`} />
    <div className="max-h-[720px] overflow-y-auto p-4">
      <IdentityFacts identity={detail.identity} />
      <section className="border-b border-zinc-200 pb-4"><div className="text-sm font-semibold">{text(plan.plan_goal) || "未记录计划目标"}</div><div className="mt-2 grid gap-2 text-xs sm:grid-cols-2"><Fact label="客户阶段" value={text(plan.customer_stage) || "未记录"} /><Fact label="当前状态" value={planStatusLabel(text(plan.status))} /><Fact label="任务进度" value={`${detail.task_summary.handled}/${detail.task_summary.total} 已处理`} /><Fact label="实际发送" value={`${detail.task_summary.sent} 条`} /></div></section>
      <section className="mt-4"><h3 className="text-sm font-semibold">任务</h3><div className="mt-2 space-y-3">{detail.tasks.length ? detail.tasks.map((task) => <TaskCard key={text(task.id)} task={task} />) : <EmptyState icon={<CircleSlash2 className="h-4 w-4" />} text="该计划没有任务" />}</div></section>
      <section className="mt-5 border-t border-zinc-200 pt-4"><h3 className="text-sm font-semibold">审计时间线</h3><div className="mt-3 space-y-3">{detail.events.length ? detail.events.map((event, index) => <AuditEvent key={`${text(event.id)}-${index}`} event={event} />) : <div className="text-sm text-zinc-500">未记录额外审计事件。</div>}</div></section>
      <details className="mt-5 border-t border-zinc-200 pt-4"><summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-zinc-700"><FileJson2 className="h-4 w-4" />技术记录</summary><p className="mt-2 text-xs leading-5 text-zinc-500">仅用于排查，已沿用后台脱敏结果；不作为业务发送凭据。</p><pre className="mt-3 max-h-80 overflow-auto bg-zinc-950 p-3 text-xs leading-5 text-zinc-100">{pretty({ technical: detail.technical, events: detail.events, task_statuses: detail.tasks.map((task) => ({ id: task.id, status: task.status, send_status: task.send_status, system_msgid: task.system_msgid, error_message: task.error_message })) })}</pre></details>
    </div>
  </section>;
}

function IdentityFacts({ identity }: { identity: Identity }) {
  const facts: Array<[string, string | undefined]> = [
    ["客户 ID", identity.customer_id],
    ["外部联系人 ID", identity.external_userid],
    ["加微关系 ID", identity.customer_add_wechat_id],
    ["接待人员 ID", identity.user_id],
    ["企微账号", identity.wechat],
    ["企业 ID", identity.corp_id],
    ["会话 ID", identity.conversation_id],
  ];
  return <section className="mb-4 border-b border-zinc-200 pb-4"><h3 className="text-sm font-semibold">客户身份</h3><div className="mt-2 grid gap-2 text-xs sm:grid-cols-2">{facts.map(([label, value]) => <Fact key={label} label={label} value={value || "未记录"} />)}</div></section>;
}

function TaskCard({ task }: { task: OutreachTask }) {
  const sentVerified = task.actual_send === true;
  const status = text(task.status);
  const messages = taskMessageItems(task);
  return <article className="border border-zinc-200 p-3"><div className="flex items-start justify-between gap-3"><div><div className="text-sm font-semibold">第 {number(task.step_index, 0)} 步</div><div className="mt-1 text-xs text-zinc-500">计划时间 {formatTime(text(task.scheduled_at))}</div></div><TaskStatusPill status={status} verified={sentVerified} /></div><div className="mt-3 grid gap-2 text-xs sm:grid-cols-2"><Fact label="任务 ID" value={text(task.id) || "未记录"} /><Fact label="平台消息 ID" value={text(task.system_msgid) || "未记录"} /><Fact label="实际时间" value={formatTime(text(task.sent_at))} /><Fact label="发送结果" value={sentVerified ? "已记录平台消息 ID" : status === "sent" ? "状态已发送，待核验" : taskOutcome(status)} /><Fact label="任务目标" value={text(task.message_goal) || "未记录"} /><Fact label="发送前复核" value={task.before_send_check ? "需要" : "未要求"} /><div className="sm:col-span-2"><Fact label="内容来源" value={taskSourceSummary(task)} /></div></div>{messages.length ? <section className="mt-3 space-y-2"><div className="text-xs font-medium text-zinc-500">本任务生成/发送的内容</div>{messages.map((message, index) => <TaskMessage key={`${message.type}-${index}`} message={message} />)}</section> : <div className="mt-3 border border-zinc-200 bg-zinc-50 px-3 py-2 text-xs text-zinc-500">没有保存可展示的客户可见内容。</div>}{text(task.error_message) ? <div className="mt-3 border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-800">失败原因：{text(task.error_message)}</div> : null}</article>;
}

function NoPlanEvaluations({ records }: { records: OutreachRecord[] }) {
  const groups = groupNoPlanRecords(records);
  const total = records.reduce((sum, record) => sum + Math.max(1, number(record.record_count, 1)), 0);
  return <details className="mt-4 border border-zinc-200 bg-zinc-50"><summary className="cursor-pointer px-3 py-2 text-sm font-medium text-zinc-700">扫描/评估但未建计划：{total} 次 <span className="font-normal text-zinc-500">（不等于创建计划）</span></summary><div className="space-y-2 border-t border-zinc-200 p-3">{groups.map((group) => <div key={group.key} className="bg-white p-3 text-xs"><div className="font-medium text-zinc-800">{reasonLabel(group.reasonCode)}</div><div className="mt-1 text-zinc-500">{group.count} 次 · {formatTime(group.firstAt)} 至 {formatTime(group.latestAt)}</div></div>)}</div></details>;
}

type TaskMessageItem = { type: string; text: string; url: string };

function TaskMessage({ message }: { message: TaskMessageItem }) {
  const mediaLabel = message.type === "image" ? "图片" : message.type === "video" ? "视频" : message.type === "file" ? "文件" : "结构化消息";
  if (message.text) return <div className="border-l-2 border-blue-500 bg-blue-50 px-3 py-2 text-sm leading-6 text-zinc-800">{message.text}</div>;
  return <div className="border border-zinc-200 bg-white px-3 py-2 text-sm"><span className="font-medium text-zinc-800">{mediaLabel}</span>{message.url ? <a href={message.url} target="_blank" rel="noreferrer" className="ml-2 text-blue-700 underline underline-offset-2">查看素材</a> : <span className="ml-2 text-zinc-500">已记录，未保存可预览地址</span>}</div>;
}

function AuditEvent({ event }: { event: JsonRecord }) {
  return <div className="border-l-2 border-zinc-300 pl-3"><div className="flex items-center justify-between gap-3"><div className="text-sm font-medium">{text(event.event_summary) || text(event.event_type) || "审计事件"}</div><div className="shrink-0 text-xs text-zinc-500">{formatTime(text(event.created_at))}</div></div><div className="mt-1 text-xs text-zinc-500">{text(event.event_type)}</div></div>;
}

function PanelHeader({ icon, title, subtitle }: { icon: ReactNode; title: string; subtitle: string }) { return <header className="border-b border-zinc-200 px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold">{icon}{title}</h2><p className="mt-1 truncate text-xs text-zinc-500">{subtitle}</p></header>; }
function Field({ label, children }: { label: string; children: ReactNode }) { return <label className="min-w-0 text-xs text-zinc-600"><span>{label}</span><span className="mt-1 block">{children}</span></label>; }
function SelectField({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[][] }) { return <Field label={label}><select value={value} onChange={(event) => onChange(event.target.value)} className={inputClassName}><option value="">全部</option>{options.map(([key, name]) => <option key={key} value={key}>{name}</option>)}</select></Field>; }
function SummaryValue({ label, value }: { label: string; value: string }) { return <div><div className="text-zinc-500">{label}</div><div className="mt-0.5 font-semibold tabular-nums text-zinc-900">{value}</div></div>; }
function CompactIdentity({ label, value }: { label: string; value: string }) { return <div className="min-w-0"><div className="text-zinc-500">{label}</div><div className="mt-0.5 break-all leading-4 text-zinc-800">{value || "未记录"}</div></div>; }
function Fact({ label, value }: { label: string; value: string }) { return <div><div className="text-zinc-500">{label}</div><div className="mt-1 break-words text-zinc-800">{value || "-"}</div></div>; }
function EmptyState({ icon, text }: { icon: ReactNode; text: string }) { return <div className="flex min-h-40 flex-col items-center justify-center gap-2 p-5 text-center text-sm text-zinc-500">{icon}<span>{text}</span></div>; }
function StatusPill({ status, noPlan = false }: { status: string; noPlan?: boolean }) { const tone = noPlan || status === "blocked" || status === "cancelled" ? "bg-amber-100 text-amber-800" : status === "failed" ? "bg-red-100 text-red-700" : status === "completed" || status === "sent" ? "bg-emerald-100 text-emerald-700" : "bg-blue-100 text-blue-700"; return <span className={`shrink-0 rounded-full px-2 py-1 text-xs font-medium ${tone}`}>{noPlan ? "未生成" : planStatusLabel(status)}</span>; }
function CustomerStatusPill({ item }: { item: CustomerItem }) { if (item.next_task?.scheduled_at || item.task_summary.pending + item.task_summary.processing > 0) return <StatusPill status="active" />; if (item.plan_count > 0 && item.task_summary.total > 0 && item.task_summary.handled >= item.task_summary.total) return <StatusPill status="completed" />; if (item.plan_count > 0) return <StatusPill status="generated" />; return <StatusPill status={item.latest_record.status} noPlan />; }
function TaskStatusPill({ status, verified }: { status: string; verified: boolean }) { const tone = verified ? "bg-emerald-100 text-emerald-700" : status === "sent" ? "bg-amber-100 text-amber-800" : status === "failed" || status === "check_failed" ? "bg-red-100 text-red-700" : status === "pending" || status === "checking" || status === "sending" ? "bg-blue-100 text-blue-700" : "bg-zinc-100 text-zinc-700"; return <span className={`rounded-full px-2 py-1 text-xs font-medium ${tone}`}>{verified ? "已发送" : taskOutcome(status)}</span>; }

const inputClassName = "h-9 w-full min-w-0 rounded-md border border-zinc-200 bg-white px-2 text-sm text-zinc-900 outline-none focus:border-blue-600 focus:ring-1 focus:ring-blue-600";
const iconButtonClass = "grid h-9 w-9 place-items-center rounded-md border border-zinc-200 bg-white hover:bg-zinc-50 disabled:opacity-40";

function metricIconTone(tone: string): string { return ({ blue: "text-blue-700", emerald: "text-emerald-700", amber: "text-amber-700", red: "text-red-700", zinc: "text-zinc-500" } as Record<string, string>)[tone] || "text-zinc-500"; }
function identityName(identity: Identity): string { return identity.external_userid || identity.customer_id || ""; }
function planStatusLabel(status: string): string { return PLAN_STATUS_LABELS[status] || status || "未记录"; }
function taskOutcome(status: string): string { return TASK_STATUS_LABELS[status] || status || "未记录"; }
function reasonLabel(reason: string): string { return REASON_LABELS[reason] || reason || "未记录"; }
function formatTime(value: string): string { if (!value) return "-"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-CN", { hour12: false }); }
function text(value: unknown): string { return typeof value === "string" ? value.trim() : typeof value === "number" || typeof value === "boolean" ? String(value) : ""; }
function number(value: unknown, fallback: number): number { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : fallback; }
function pretty(value: unknown): string { try { return JSON.stringify(value, null, 2); } catch { return String(value); } }
function identityValues(values: string[] | undefined, fallback?: string): string { const unique = [...new Set([...(values || []), fallback || ""].map((value) => text(value)).filter(Boolean))]; return unique.length ? unique.join(" / ") : "未记录"; }
function taskSourceSummary(task: OutreachTask): string { const sources = Array.isArray(task.content_sources) ? task.content_sources : []; const labels = sources.map((source) => { if (typeof source === "string") return source.trim(); if (!source || typeof source !== "object" || Array.isArray(source)) return ""; const record = source as JsonRecord; return text(record.name) || text(record.title) || text(record.source_id) || text(record.id) || text(record.type); }).filter(Boolean); return labels.length ? labels.join("、") : sources.length ? `${sources.length} 个结构化来源` : "未记录"; }
function taskMessageItems(task: OutreachTask): TaskMessageItem[] { const messages = Array.isArray(task.reply_messages) ? task.reply_messages : []; return messages.map((message): TaskMessageItem | null => { if (typeof message === "string") return message.trim() ? { type: "text", text: message.trim(), url: "" } : null; if (!message || typeof message !== "object" || Array.isArray(message)) return null; const record = message as JsonRecord; const content = record.content && typeof record.content === "object" && !Array.isArray(record.content) ? record.content as JsonRecord : {}; const directContent = text(record.content); const messageText = text(content.text) || text(content.content) || directContent || text(record.text) || text(record.message); const url = text(content.url) || text(content.media_url) || text(record.url) || text(record.media_url); return { type: text(record.type) || (url ? "media" : "text"), text: messageText, url }; }).filter((message): message is TaskMessageItem => Boolean(message)); }
function groupNoPlanRecords(records: OutreachRecord[]): Array<{ key: string; reasonCode: string; count: number; firstAt: string; latestAt: string }> { const groups = new Map<string, { key: string; reasonCode: string; count: number; firstAt: string; latestAt: string }>(); for (const record of records) { const key = `${record.source_type}|${record.reason_code}`; const count = Math.max(1, number(record.record_count, 1)); const existing = groups.get(key); if (!existing) { groups.set(key, { key, reasonCode: record.reason_code, count, firstAt: record.created_at, latestAt: record.created_at }); continue; } existing.count += count; if (record.created_at < existing.firstAt) existing.firstAt = record.created_at; if (record.created_at > existing.latestAt) existing.latestAt = record.created_at; } return [...groups.values()].sort((left, right) => right.latestAt.localeCompare(left.latestAt)); }
