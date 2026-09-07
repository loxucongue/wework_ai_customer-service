"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Bot,
  CalendarClock,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  CircleSlash2,
  Clock3,
  Filter,
  ImageIcon,
  Layers3,
  ListTree,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  UserRoundCheck,
  UsersRound,
  Video,
  X,
} from "lucide-react";

type JsonRecord = Record<string, unknown>;

type BusinessSummary = {
  last_customer_message?: string;
  last_customer_message_at?: string;
  customer_need?: string;
  silence_barrier?: string;
  precedence?: string;
  eligible?: boolean | null;
  planned_media_count?: number;
  available_media_count?: number;
};

type WorkflowNode = {
  key: string;
  label: string;
  status: string;
  elapsed_ms?: number;
  model?: string;
  prompt_version?: string;
  attempt_count?: number;
  input?: JsonRecord;
  output?: JsonRecord;
};

type MaterialStep = {
  step: number;
  scene?: string;
  source_ids?: string[];
  required_asset?: JsonRecord;
  source_asset_options?: JsonRecord[];
  available_asset_count?: number;
  planned_media_count?: number;
  planned_text_count?: number;
  task_status?: string;
  delivery_state?: string;
};

type ObservabilityView = {
  decision?: JsonRecord;
  customer_context?: JsonRecord;
  materials?: {
    summary?: JsonRecord;
    recent_delivery?: JsonRecord;
    steps?: MaterialStep[];
  };
  workflow_nodes?: WorkflowNode[];
  data_availability?: JsonRecord;
};

type RunSummary = {
  workflow_run_id: string;
  plan_id?: string;
  first_task_id?: string;
  second_task_id?: string;
  corp_id?: string;
  wechat?: string;
  customer_id?: string;
  external_userid?: string;
  status?: string;
  reason_code?: string;
  final_decision?: string;
  first_scene?: string;
  second_scene?: string;
  first_task_status?: string;
  second_task_status?: string;
  model_attempt_count?: number;
  retry_count?: number;
  duration_ms?: number;
  started_at?: string;
  finished_at?: string;
  error_node?: string;
  error_type?: string;
  error_message?: string;
  business_summary?: BusinessSummary;
};

type RunDetail = RunSummary & {
  input_snapshot?: JsonRecord;
  workflow?: JsonRecord;
  final_plan?: JsonRecord;
  tasks?: JsonRecord[];
  events?: JsonRecord[];
  raw_redacted_at?: string;
  observability_view?: ObservabilityView;
};

type Filters = {
  customer_id: string;
  external_userid: string;
  corp_id: string;
  wechat: string;
  plan_id: string;
  status: string;
  reason_code: string;
  first_scene: string;
  second_scene: string;
  failed: string;
  started_from: string;
  started_to: string;
};

type FirstDaySettings = {
  enabled: boolean;
  silence_minutes: number;
  wechat_allowlist: string[];
  wechat_allowlist_raw: string;
  empty_allowlist_means_all_allowed?: boolean;
  eligible_after?: string;
  contact_age_limited?: boolean;
};

const EMPTY_FILTERS: Filters = {
  customer_id: "",
  external_userid: "",
  corp_id: "",
  wechat: "",
  plan_id: "",
  status: "",
  reason_code: "",
  first_scene: "",
  second_scene: "",
  failed: "",
  started_from: "",
  started_to: "",
};

const TABS = ["业务全景", "客户上下文", "执行节点", "发送记录", "原始记录"] as const;
type Tab = (typeof TABS)[number];

const STATUS_LABELS: Record<string, string> = {
  running: "运行中",
  created: "等待发送",
  blocked: "已阻断",
  sent: "已发送",
  cancelled: "已取消",
  failed: "失败",
  completed: "已完成",
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

const REASON_LABELS: Record<string, string> = {
  first_task_sent: "首步已发送，等待客户回复",
  plan_created: "计划已创建，等待执行",
  human_mode: "人工接待中，已跳过",
  ai_outreach_not_allowed: "当前会话不允许 AI 主动触达",
  ai_mode_unknown: "无法确认 AI 接待状态",
  ai_mode_status_unavailable: "接待状态接口暂不可用",
  customer_never_spoke: "客户尚未真实开口",
  customer_replied: "客户已经回复",
  customer_deleted: "客户关系已失效",
  manual_takeover_active: "人工已经接管",
  stop_contact: "客户要求停止联系",
  health_risk: "存在健康风险，停止营销",
  order_state_changed: "订单状态已变化",
  outbound_before_activation: "早于启用时间，不回放",
  reply_wait_below_threshold: "尚未达到沉默阈值",
  outreach_cycle_completed_without_new_customer_reply: "本轮唤醒已完成，等待客户新回复",
  first_day_daily_plan_limit_reached: "已达到当天触达上限",
  first_day_daily_task_limit_reached: "已达到当天发送上限",
  daily_limit: "已达到当天发送上限",
  preflight_retry: "正在重新检查触达条件",
  before_send_check_failed: "发送前安全检查未通过",
  customer_relation_unavailable: "客户关系状态暂不可用",
  conversation_id_unavailable: "缺少有效会话标识",
  order_context_unavailable: "订单状态暂不可用",
  order_not_eligible: "当前订单状态无需唤醒",
  authoritative_fingerprint_already_logged: "相同会话状态已经记录",
  conversation_fingerprint_already_logged: "相同会话状态已经处理",
  conversation_fingerprint_already_evaluated: "相同会话状态已经评估",
  nonterminal_plan_exists: "已有进行中的唤醒计划",
  conversation_refresh_failed: "最新聊天拉取失败",
};

const NODE_STATUS: Record<string, string> = {
  completed: "完成",
  warning: "有警告",
  failed: "失败",
  skipped: "未运行",
  not_reached: "上游失败未到达",
};

export function FirstDayOutreachLogViewer() {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [items, setItems] = useState<RunSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [selectedNode, setSelectedNode] = useState<WorkflowNode | null>(null);
  const [nextCursor, setNextCursor] = useState("");
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [activeCursor, setActiveCursor] = useState("");
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("业务全景");
  const [showMobileDetail, setShowMobileDetail] = useState(false);
  const [filtersExpanded, setFiltersExpanded] = useState(false);

  const loadRuns = useCallback(async (cursor = "") => {
    setLoading(true);
    setError("");
    const search = new URLSearchParams({ limit: "50" });
    Object.entries(filters).forEach(([key, value]) => {
      if (!value) return;
      search.set(key, key === "started_from" || key === "started_to" ? new Date(value).toISOString() : value);
    });
    if (cursor) search.set("cursor", cursor);
    try {
      const response = await fetch(`/api/outreach/first-day-runs?${search.toString()}`, { cache: "no-store" });
      const data = (await response.json()) as { items?: RunSummary[]; next_cursor?: string; detail?: string };
      if (!response.ok) throw new Error(data.detail || "加载沉默唤醒日志失败");
      const nextItems = Array.isArray(data.items) ? data.items : [];
      setItems(nextItems);
      setNextCursor(data.next_cursor || "");
      setActiveCursor(cursor);
      setSelectedId((current) => nextItems.some((item) => item.workflow_run_id === current) ? current : nextItems[0]?.workflow_run_id || "");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载沉默唤醒日志失败");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const loadDetail = useCallback(async (workflowRunId: string) => {
    if (!workflowRunId) return;
    setDetailLoading(true);
    setDetail(null);
    setSelectedNode(null);
    setError("");
    try {
      const response = await fetch(`/api/outreach/first-day-runs/${encodeURIComponent(workflowRunId)}`, { cache: "no-store" });
      const data = (await response.json()) as RunDetail & { detail?: string };
      if (!response.ok) throw new Error(data.detail || "加载运行详情失败");
      setDetail(data);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载运行详情失败");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => { void loadRuns(); }, []); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { if (selectedId) void loadDetail(selectedId); }, [loadDetail, selectedId]);

  const selected = detail?.workflow_run_id === selectedId ? detail : items.find((item) => item.workflow_run_id === selectedId) || null;
  const selectedDetail = detail?.workflow_run_id === selectedId ? detail : null;
  const activeFilterCount = Object.values(filters).filter(Boolean).length;

  const runSearch = () => {
    setCursorHistory([]);
    setSelectedId("");
    setDetail(null);
    void loadRuns("");
  };

  return (
    <main className="flex min-h-screen bg-zinc-100 text-zinc-950 lg:h-screen lg:overflow-hidden">
      <aside className={`${showMobileDetail ? "hidden" : "flex"} min-h-screen w-full flex-col border-r border-zinc-200 bg-white lg:flex lg:min-h-0 lg:w-[390px] lg:min-w-[360px]`}>
        <header className="border-b border-zinc-200 px-4 py-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-lg font-semibold"><Sparkles className="h-5 w-5 text-blue-600" />沉默唤醒日志</div>
              <p className="mt-1 text-xs text-zinc-500">从客户沉默到素材选择、计划和发送结果</p>
            </div>
            <button type="button" title="刷新" onClick={() => { void loadRuns(activeCursor); if (selectedId) void loadDetail(selectedId); }} className="grid h-9 w-9 place-items-center rounded-lg border border-zinc-200 hover:bg-zinc-50">
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
          <Link href="/logs" className="mt-3 inline-flex items-center gap-1.5 text-xs font-medium text-zinc-600 hover:text-zinc-950"><ArrowLeft className="h-3.5 w-3.5" />返回 AI 回复日志</Link>
        </header>

        <FirstDaySettingsPanel />
        <RunOverview items={items} loading={loading} />

        <section className="border-b border-zinc-200">
          <button type="button" aria-expanded={filtersExpanded} onClick={() => setFiltersExpanded((value) => !value)} className="flex h-11 w-full items-center justify-between px-4 text-left hover:bg-zinc-50">
            <span className="flex items-center gap-2 text-xs font-semibold text-zinc-700"><Filter className="h-3.5 w-3.5" />筛选{activeFilterCount ? <span className="rounded-full bg-zinc-900 px-2 py-0.5 text-[11px] text-white">{activeFilterCount}</span> : <span className="font-normal text-zinc-400">按客户、状态或场景</span>}</span>
            {filtersExpanded ? <ChevronUp className="h-4 w-4 text-zinc-400" /> : <ChevronDown className="h-4 w-4 text-zinc-400" />}
          </button>
          {filtersExpanded ? <FilterPanel filters={filters} setFilters={setFilters} onSearch={runSearch} onReset={() => setFilters(EMPTY_FILTERS)} /> : null}
          {error ? <div className="mx-4 mb-3 flex gap-2 rounded-lg bg-red-50 p-2 text-xs text-red-700"><AlertCircle className="h-4 w-4 shrink-0" />{error}</div> : null}
        </section>

        <section className="min-h-0 flex-1 overflow-y-auto">
          {loading && items.length === 0 ? <EmptyState icon={<LoaderCircle className="h-5 w-5 animate-spin" />} text="正在加载运行记录" /> : null}
          {!loading && items.length === 0 ? <EmptyState icon={<Search className="h-5 w-5" />} text="当前筛选下没有记录" /> : null}
          {items.map((item) => <RunListItem key={item.workflow_run_id} item={item} selected={selectedId === item.workflow_run_id} onClick={() => { setSelectedId(item.workflow_run_id); setTab("业务全景"); setShowMobileDetail(true); }} />)}
        </section>

        <footer className="flex items-center justify-between border-t border-zinc-200 p-3 text-xs text-zinc-500">
          <span>每页 50 条</span>
          <div className="flex gap-1">
            <button type="button" title="上一页" disabled={cursorHistory.length === 0 || loading} onClick={() => { const history = [...cursorHistory]; const previous = history.pop() || ""; setCursorHistory(history); void loadRuns(previous); }} className="grid h-8 w-8 place-items-center rounded-lg border border-zinc-200 disabled:opacity-40"><ChevronLeft className="h-4 w-4" /></button>
            <button type="button" title="下一页" disabled={!nextCursor || loading} onClick={() => { setCursorHistory((value) => [...value, activeCursor]); void loadRuns(nextCursor); }} className="grid h-8 w-8 place-items-center rounded-lg border border-zinc-200 disabled:opacity-40"><ChevronRight className="h-4 w-4" /></button>
          </div>
        </footer>
      </aside>

      <section className={`${showMobileDetail ? "flex" : "hidden"} min-h-screen min-w-0 flex-1 flex-col lg:flex lg:min-h-0`}>
        <header className="border-b border-zinc-200 bg-white px-4 py-3 lg:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button type="button" title="返回列表" onClick={() => setShowMobileDetail(false)} className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-zinc-200 lg:hidden"><ChevronLeft className="h-4 w-4" /></button>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2"><h2 className="truncate text-sm font-semibold">{selected?.customer_id || selected?.external_userid || "选择一条运行记录"}</h2>{selected?.status ? <StatusBadge status={selected.status} /> : null}</div>
              {selected ? <p className="mt-1 truncate font-mono text-[11px] text-zinc-500">{selected.workflow_run_id} · {selected.wechat || "未知企微"} · {formatTime(selected.started_at)}</p> : null}
            </div>
            {detailLoading ? <LoaderCircle className="h-4 w-4 animate-spin text-zinc-400" /> : null}
          </div>
        </header>

        {selected ? <>
          <nav className="flex shrink-0 gap-1 overflow-x-auto border-b border-zinc-200 bg-white px-3 py-2 lg:px-6">
            {TABS.map((item) => <button key={item} type="button" onClick={() => setTab(item)} className={`h-8 shrink-0 rounded-lg px-3 text-sm ${tab === item ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-100"}`}>{item}</button>)}
          </nav>
          <div className="min-h-0 flex-1 overflow-y-auto p-4 lg:p-6">
            {selectedDetail ? <DetailTab tab={tab} detail={selectedDetail} onNode={setSelectedNode} /> : <EmptyState icon={<LoaderCircle className="h-5 w-5 animate-spin" />} text="正在加载详情" />}
          </div>
        </> : <EmptyState icon={<ListTree className="h-6 w-6" />} text="从左侧选择一条记录" />}
      </section>

      {selectedNode ? <NodeDrawer node={selectedNode} onClose={() => setSelectedNode(null)} /> : null}
    </main>
  );
}

function DetailTab({ tab, detail, onNode }: { tab: Tab; detail: RunDetail; onNode: (node: WorkflowNode) => void }) {
  if (tab === "业务全景") return <BusinessOverview detail={detail} onNode={onNode} />;
  if (tab === "客户上下文") return <ChatTab detail={detail} />;
  if (tab === "执行节点") return <WorkflowPanel detail={detail} onNode={onNode} expanded />;
  if (tab === "发送记录") return <TimelineTab detail={detail} />;
  return <RawRecord detail={detail} />;
}

function BusinessOverview({ detail, onNode }: { detail: RunDetail; onNode: (node: WorkflowNode) => void }) {
  const view = detail.observability_view || {};
  const decision = record(view.decision);
  const customer = record(view.customer_context);
  const lastMessage = record(customer.last_customer_message);
  const hardBoundary = record(decision.hard_boundary);
  const blocked = detail.status === "blocked" || hardBoundary.active === true;
  return <div className="mx-auto max-w-6xl space-y-5">
    <section className={`rounded-xl border p-4 ${blocked ? "border-amber-200 bg-amber-50" : detail.status === "failed" ? "border-red-200 bg-red-50" : "border-blue-200 bg-blue-50"}`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2 text-sm font-semibold">{blocked ? <ShieldCheck className="h-4 w-4 text-amber-700" /> : <Sparkles className="h-4 w-4 text-blue-700" />}{blocked ? "本轮为什么不触达" : "本轮为什么这样触达"}</div>
          <p className="mt-2 text-sm leading-6 text-zinc-800">{decisionText(detail, decision)}</p>
        </div>
        <div className="shrink-0 text-right text-xs text-zinc-500"><div>总耗时 {formatDuration(detail.duration_ms)}</div><div className="mt-1">模型调用 {detail.model_attempt_count || 0} 次</div></div>
      </div>
    </section>

    <section className="grid gap-3 lg:grid-cols-[1.25fr_1fr]">
      <Panel title="客户最后说了什么" icon={<MessageSquareText className="h-4 w-4" />}>
        <blockquote className="rounded-lg bg-zinc-100 px-4 py-3 text-sm leading-6 text-zinc-800">{str(lastMessage.text) || detail.business_summary?.last_customer_message || "没有留存客户原话"}</blockquote>
        <div className="mt-3 grid gap-3 sm:grid-cols-2"><Fact label="客户当前需要" value={str(decision.customer_need) || "未记录"} /><Fact label="沉默卡点" value={str(decision.silence_barrier) || "未识别到明确卡点"} /><Fact label="下一业务动作" value={str(decision.next_business_action) || "未记录"} /><Fact label="判定依据" value={precedenceLabel(record(decision.precedence).row_id)} /></div>
      </Panel>
      <Panel title="两步节奏" icon={<CalendarClock className="h-4 w-4" />}>
        <div className="space-y-3"><StepArrow index={1} scene={str(decision.first_scene) || detail.first_scene} objective={str(decision.first_objective)} /><StepArrow index={2} scene={str(decision.second_scene) || detail.second_scene} objective={str(decision.second_objective)} /></div>
      </Panel>
    </section>

    <MaterialFlow detail={detail} />
    <Panel title="最终生成并执行的任务" icon={<Send className="h-4 w-4" />}><TaskList tasks={detail.tasks || []} /></Panel>
    <WorkflowPanel detail={detail} onNode={onNode} />
    {detail.error_message ? <section className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-800"><div className="font-semibold">{detail.error_node || "运行失败"} · {detail.error_type}</div><p className="mt-2 break-words text-xs leading-5">{detail.error_message}</p></section> : null}
  </div>;
}

function MaterialFlow({ detail }: { detail: RunDetail }) {
  const materials = detail.observability_view?.materials || {};
  const summary = record(materials.summary);
  const steps = Array.isArray(materials.steps) ? materials.steps : [];
  return <Panel title="素材从候选到发送" icon={<Layers3 className="h-4 w-4" />}>
    <div className="grid gap-2 sm:grid-cols-4">
      <MiniMetric label="素材候选" value={String(num(summary.total_count))} />
      <MiniMetric label="本轮可发送" value={String(num(summary.available_count))} tone="blue" />
      <MiniMetric label="近期已发送" value={String(num(summary.recently_sent_count))} />
      <MiniMetric label="图片 / 视频" value={`${num(summary.image_count)} / ${num(summary.video_count)}`} />
    </div>
    {!num(summary.total_count) ? <div className="mt-3 rounded-lg bg-amber-50 p-3 text-sm text-amber-800">本条历史日志没有保存素材目录，不能显示为候选 0。</div> : null}
    <div className="mt-4 grid gap-3 xl:grid-cols-2">{steps.map((step) => <article key={step.step} className="rounded-xl border border-zinc-200 bg-zinc-50 p-4">
      <div className="flex items-start justify-between gap-3"><div><div className="text-xs font-medium text-zinc-500">第 {step.step} 步</div><div className="mt-1 font-semibold">{sceneLabel(step.scene)}</div></div><MaterialBadge state={step.delivery_state || "text_only"} /></div>
      <div className="mt-3 space-y-2 text-xs"><FactRow label="选中来源" value={(step.source_ids || []).join("、") || "未记录"} mono /><FactRow label="来源内可用素材" value={`${step.available_asset_count || 0} 个`} /><FactRow label="最终任务" value={`${step.planned_text_count || 0} 条文字 · ${step.planned_media_count || 0} 个媒体`} /><FactRow label="素材 ID" value={str(record(step.required_asset).asset_id) || "无"} mono /></div>
      {(step.source_asset_options || []).length ? <div className="mt-3 flex flex-wrap gap-1.5">{(step.source_asset_options || []).slice(0, 8).map((asset) => <span key={str(asset.asset_id)} className={`inline-flex items-center gap-1 rounded-full border px-2 py-1 text-[11px] ${asset.available_to_send === false ? "border-zinc-200 bg-white text-zinc-400" : "border-blue-200 bg-blue-50 text-blue-700"}`}>{str(asset.type) === "video" ? <Video className="h-3 w-3" /> : <ImageIcon className="h-3 w-3" />}{str(asset.name) || str(asset.asset_id)}</span>)}</div> : null}
    </article>)}</div>
  </Panel>;
}

function WorkflowPanel({ detail, onNode, expanded = false }: { detail: RunDetail; onNode: (node: WorkflowNode) => void; expanded?: boolean }) {
  const nodes = detail.observability_view?.workflow_nodes || [];
  return <Panel title="模型执行链" icon={<Bot className="h-4 w-4" />} description="点击节点查看它看到了什么、输出了什么">
    <div className={expanded ? "grid gap-3 md:grid-cols-2 xl:grid-cols-3" : "flex gap-2 overflow-x-auto pb-1"}>{nodes.map((node, index) => <button key={node.key} type="button" onClick={() => onNode(node)} className={`${expanded ? "min-h-32" : "min-w-44"} rounded-xl border border-zinc-200 bg-white p-3 text-left hover:border-zinc-400 hover:shadow-sm`}>
      <div className="flex items-center justify-between gap-2"><span className="text-xs text-zinc-400">{String(index + 1).padStart(2, "0")}</span><NodeStatus status={node.status} /></div>
      <div className="mt-2 text-sm font-semibold">{node.label}</div>
      <div className="mt-2 text-xs text-zinc-500">{node.model || "未调用模型"}</div>
      <div className="mt-1 text-xs text-zinc-400">{formatDuration(node.elapsed_ms)}</div>
      {expanded ? <p className="mt-3 line-clamp-2 text-xs leading-5 text-zinc-600">{nodeOutputSummary(node)}</p> : null}
    </button>)}</div>
  </Panel>;
}

function NodeDrawer({ node, onClose }: { node: WorkflowNode; onClose: () => void }) {
  return <div className="fixed inset-0 z-50 flex justify-end bg-black/20" role="dialog" aria-modal="true" aria-label={`${node.label}详情`} onMouseDown={(event) => { if (event.currentTarget === event.target) onClose(); }}>
    <aside className="flex h-full w-full max-w-xl flex-col bg-white shadow-2xl">
      <header className="flex items-start justify-between gap-3 border-b border-zinc-200 p-5"><div><div className="flex items-center gap-2"><h3 className="font-semibold">{node.label}</h3><NodeStatus status={node.status} /></div><p className="mt-1 text-xs text-zinc-500">{node.model || "无模型"} · {formatDuration(node.elapsed_ms)} · {node.attempt_count || 0} 次尝试</p></div><button type="button" title="关闭" onClick={onClose} className="grid h-9 w-9 place-items-center rounded-lg border border-zinc-200"><X className="h-4 w-4" /></button></header>
      <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <section className="rounded-xl bg-zinc-100 p-4"><div className="text-xs font-semibold text-zinc-500">处理结果</div><p className="mt-2 text-sm leading-6">{nodeOutputSummary(node)}</p></section>
        <div className="mt-5 grid gap-3 sm:grid-cols-2"><Fact label="Prompt 版本" value={node.prompt_version || "未记录"} mono /><Fact label="运行状态" value={NODE_STATUS[node.status] || node.status} /></div>
        <details className="mt-5 rounded-xl border border-zinc-200"><summary className="cursor-pointer px-4 py-3 text-sm font-semibold">查看原始输入</summary><pre className="max-h-[45vh] overflow-auto border-t border-zinc-200 bg-zinc-950 p-4 text-xs leading-5 text-zinc-100">{pretty(node.input)}</pre></details>
        <details className="mt-3 rounded-xl border border-zinc-200"><summary className="cursor-pointer px-4 py-3 text-sm font-semibold">查看原始输出</summary><pre className="max-h-[45vh] overflow-auto border-t border-zinc-200 bg-zinc-950 p-4 text-xs leading-5 text-zinc-100">{pretty(node.output)}</pre></details>
      </div>
    </aside>
  </div>;
}

function ChatTab({ detail }: { detail: RunDetail }) {
  const messages = array(detail.input_snapshot?.recent_messages).filter((item): item is JsonRecord => isRecord(item));
  if (!messages.length) return <EmptyState icon={<MessageSquareText className="h-5 w-5" />} text={detail.raw_redacted_at ? `原始聊天已于 ${formatTime(detail.raw_redacted_at)} 按保留策略清除` : "没有聊天上下文"} />;
  return <div className="mx-auto max-w-4xl space-y-3">{messages.map((message, index) => {
    const role = str(message.role || message.sender_type).toLowerCase();
    const customer = ["customer", "user", "external"].includes(role) || ["customer", "inbound"].includes(str(message.direction).toLowerCase());
    return <article key={`${str(message.msgid) || "message"}-${index}`} className={`flex ${customer ? "justify-start" : "justify-end"}`}><div className={`max-w-[88%] rounded-xl border p-3 ${customer ? "border-zinc-200 bg-white" : "border-blue-200 bg-blue-50"}`}><div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500"><span className="font-medium text-zinc-700">{customer ? "客户" : role === "assistant" ? "AI" : "客服"}</span><span>{formatTime(str(message.created_at || message.msgtime))}</span><span>{str(message.msgtype || message.type) || "text"}</span></div><div className="whitespace-pre-wrap break-words text-sm leading-6">{messageText(message)}</div></div></article>;
  })}</div>;
}

function TimelineTab({ detail }: { detail: RunDetail }) {
  const entries = [
    { at: detail.started_at, type: "workflow_started", summary: "沉默唤醒判断开始", payload: {} },
    ...(detail.events || []).map((event) => ({ at: str(event.created_at), type: str(event.event_type) || "event", summary: str(event.event_summary), payload: event.payload || {} })),
    ...(detail.tasks || []).map((task) => ({ at: str(task.sent_at || task.updated_at || task.scheduled_at), type: `task_${str(task.status) || "unknown"}`, summary: `第 ${str(task.step_index) || "-"} 步：${taskLabel(str(task.status))}`, payload: task })),
  ].filter((entry) => entry.at).sort((a, b) => str(a.at).localeCompare(str(b.at)));
  return <div className="mx-auto max-w-4xl rounded-xl border border-zinc-200 bg-white p-5"><div className="relative ml-2 border-l border-zinc-300 pl-6">{entries.map((entry, index) => <article key={`${entry.type}-${entry.at}-${index}`} className="relative pb-6 last:pb-0"><span className="absolute -left-[29px] top-1 h-2.5 w-2.5 rounded-full border-2 border-white bg-zinc-700" /><div className="text-xs text-zinc-500">{formatTime(str(entry.at))}</div><div className="mt-1 text-sm font-semibold">{eventLabel(entry.type)}</div><div className="mt-1 text-sm text-zinc-600">{entry.summary}</div><details className="mt-2"><summary className="cursor-pointer text-xs text-zinc-500">查看事件数据</summary><pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-zinc-950 p-3 text-xs text-zinc-100">{pretty(entry.payload)}</pre></details></article>)}</div></div>;
}

function RawRecord({ detail }: { detail: RunDetail }) {
  return <div className="mx-auto max-w-5xl"><div className="mb-3 rounded-xl border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">原始记录用于技术排障，已脱敏且可能因保留期被截断。业务判断优先看“业务全景”。</div><details className="rounded-xl border border-zinc-200 bg-white"><summary className="cursor-pointer px-4 py-3 text-sm font-semibold">展开完整 JSON</summary><pre className="max-h-[75vh] overflow-auto border-t border-zinc-200 bg-zinc-950 p-4 text-xs leading-5 text-zinc-100">{pretty(detail)}</pre></details></div>;
}

function TaskList({ tasks }: { tasks: JsonRecord[] }) {
  if (!tasks.length) return <p className="mt-3 text-sm text-zinc-500">本次运行未创建计划任务。</p>;
  return <div className="mt-3 grid gap-3 xl:grid-cols-2">{tasks.map((task, index) => {
    const messages = array(task.reply_messages).filter((item): item is JsonRecord => isRecord(item));
    return <article key={str(task.id) || index} className="rounded-xl border border-zinc-200 bg-zinc-50 p-4"><div className="flex flex-wrap items-start justify-between gap-2"><div><div className="text-xs font-medium text-zinc-500">第 {str(task.step_index) || index + 1} 步</div><div className="mt-1 font-semibold">{sceneLabel(taskScene(task))}</div></div><div className="text-right text-xs"><StatusBadge status={str(task.status) || "pending"} /><div className="mt-1 text-zinc-400">{formatTime(str(task.scheduled_at))}</div></div></div><div className="mt-3 space-y-2">{messages.map((message, messageIndex) => <MessagePreview key={messageIndex} message={message} />)}</div>{task.error_message ? <div className="mt-3 rounded-lg bg-red-50 p-2 text-xs text-red-700">{str(task.error_message)}</div> : null}</article>;
  })}</div>;
}

function MessagePreview({ message }: { message: JsonRecord }) {
  const type = str(message.type) || "text";
  if (type === "text") return <div className="rounded-lg bg-white p-3 text-sm leading-6 shadow-sm">{messageText(message)}</div>;
  const content = record(message.content);
  const url = str(content.url);
  return <div className="flex items-center justify-between gap-3 rounded-lg border border-blue-200 bg-blue-50 p-3 text-sm text-blue-800"><span className="flex items-center gap-2">{type === "video" ? <Video className="h-4 w-4" /> : <ImageIcon className="h-4 w-4" />}{type === "video" ? "视频素材" : type === "payment_collection" ? "预约金卡" : "图片素材"}</span>{url.startsWith("http") ? <a href={url} target="_blank" rel="noreferrer" className="text-xs underline">打开素材</a> : null}</div>;
}

function FilterPanel({ filters, setFilters, onSearch, onReset }: { filters: Filters; setFilters: (value: Filters | ((current: Filters) => Filters)) => void; onSearch: () => void; onReset: () => void }) {
  const field = (key: keyof Filters, value: string) => setFilters((current) => ({ ...current, [key]: value }));
  return <div className="border-t border-zinc-100 px-4 pb-4 pt-3"><div className="grid grid-cols-2 gap-2"><FilterInput label="客户 ID" value={filters.customer_id} onChange={(value) => field("customer_id", value)} /><FilterInput label="企微号" value={filters.wechat} onChange={(value) => field("wechat", value)} /><FilterSelect label="运行状态" value={filters.status} onChange={(value) => field("status", value)} options={Object.entries(STATUS_LABELS)} /><FilterSelect label="失败记录" value={filters.failed} onChange={(value) => field("failed", value)} options={[["true", "仅失败"], ["false", "排除失败"]]} /><FilterSelect label="第一场景" value={filters.first_scene} onChange={(value) => field("first_scene", value)} options={Object.entries(SCENE_LABELS)} /><FilterSelect label="第二场景" value={filters.second_scene} onChange={(value) => field("second_scene", value)} options={Object.entries(SCENE_LABELS)} /><FilterInput label="external_userid" value={filters.external_userid} onChange={(value) => field("external_userid", value)} /><FilterInput label="计划 ID" value={filters.plan_id} onChange={(value) => field("plan_id", value)} /><FilterInput label="原因码" value={filters.reason_code} onChange={(value) => field("reason_code", value)} /><FilterInput label="企业 ID" value={filters.corp_id} onChange={(value) => field("corp_id", value)} /><FilterInput label="开始时间" type="datetime-local" value={filters.started_from} onChange={(value) => field("started_from", value)} /><FilterInput label="结束时间" type="datetime-local" value={filters.started_to} onChange={(value) => field("started_to", value)} /></div><div className="mt-3 flex gap-2"><button type="button" onClick={onReset} className="h-9 rounded-lg border border-zinc-200 px-3 text-sm">重置</button><button type="button" onClick={onSearch} className="inline-flex h-9 flex-1 items-center justify-center gap-2 rounded-lg bg-zinc-900 px-3 text-sm text-white"><Search className="h-4 w-4" />查询</button></div></div>;
}

function RunOverview({ items, loading }: { items: RunSummary[]; loading: boolean }) {
  const plans = items.filter((item) => Boolean(item.plan_id)).length;
  const sent = items.filter((item) => item.first_task_status === "sent" || item.second_task_status === "sent").length;
  const guarded = items.filter((item) => ["human_mode", "manual_takeover_active", "stop_contact", "health_risk"].includes(item.reason_code || "")).length;
  return <section className="grid grid-cols-4 gap-px border-b border-zinc-200 bg-zinc-200" aria-label="当前页运行概览"><OverviewCell label="记录" value={loading ? "…" : String(items.length)} /><OverviewCell label="建计划" value={String(plans)} /><OverviewCell label="已触达" value={String(sent)} /><OverviewCell label="安全阻断" value={String(guarded)} /></section>;
}

function RunListItem({ item, selected, onClick }: { item: RunSummary; selected: boolean; onClick: () => void }) {
  const summary = item.business_summary || {};
  return <button type="button" onClick={onClick} className={`w-full border-b border-zinc-100 p-4 text-left transition-colors ${selected ? "bg-blue-50" : "hover:bg-zinc-50"}`}><div className="flex items-start justify-between gap-3"><span className="truncate text-sm font-semibold">{item.customer_id || item.external_userid || "未知客户"}</span><StatusBadge status={item.status || "running"} /></div><p className="mt-2 line-clamp-2 text-sm leading-5 text-zinc-700">{summary.last_customer_message || reasonLabel(item.reason_code)}</p><div className="mt-2 flex items-center gap-1.5 text-xs text-zinc-500"><span>{sceneLabel(item.first_scene)}</span><ChevronRight className="h-3 w-3" /><span>{sceneLabel(item.second_scene)}</span>{summary.planned_media_count ? <span className="ml-auto inline-flex items-center gap-1 text-blue-600"><ImageIcon className="h-3 w-3" />{summary.planned_media_count}</span> : null}</div><div className="mt-2 flex items-center justify-between gap-2 text-[11px] text-zinc-400"><span className="truncate">{item.wechat || "未知企微"} · {formatDuration(item.duration_ms)}</span><span className="shrink-0">{formatTime(item.started_at)}</span></div></button>;
}

function FirstDaySettingsPanel() {
  const [expanded, setExpanded] = useState(false);
  const [settings, setSettings] = useState<FirstDaySettings | null>(null);
  const [enabled, setEnabled] = useState(false);
  const [silenceMinutes, setSilenceMinutes] = useState("1");
  const [allowlist, setAllowlist] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const loadSettings = useCallback(async () => { setLoading(true); setError(""); try { const response = await fetch("/api/outreach/first-day-settings", { cache: "no-store" }); const data = (await response.json()) as FirstDaySettings & { detail?: string; error?: string }; if (!response.ok) throw new Error(data.detail || data.error || "加载配置失败"); setSettings(data); setEnabled(Boolean(data.enabled)); setSilenceMinutes(String(data.silence_minutes || 1)); setAllowlist(data.wechat_allowlist_raw || ""); } catch (cause) { setError(cause instanceof Error ? cause.message : "加载配置失败"); } finally { setLoading(false); } }, []);
  useEffect(() => { void loadSettings(); }, [loadSettings]);
  const saveSettings = async () => { setSaving(true); setError(""); setMessage(""); try { const response = await fetch("/api/outreach/first-day-settings", { method: "PUT", headers: { "Content-Type": "application/json; charset=utf-8" }, body: JSON.stringify({ enabled, silence_minutes: Number(silenceMinutes), wechat_allowlist: allowlist }) }); const data = (await response.json()) as FirstDaySettings & { detail?: string; error?: string }; if (!response.ok) throw new Error(data.detail || data.error || "保存配置失败"); setSettings(data); setMessage("配置已写入；worker 重启后生效"); } catch (cause) { setError(cause instanceof Error ? cause.message : "保存配置失败"); } finally { setSaving(false); } };
  const summary = settings ? `${settings.enabled ? "已开启" : "已关闭"} · ${settings.silence_minutes} 分钟 · ${settings.wechat_allowlist?.length ? `${settings.wechat_allowlist.length} 个账号` : "全部账号"} · 仅 AI` : "加载中";
  return <section className="border-b border-zinc-200"><button type="button" onClick={() => setExpanded((value) => !value)} className="flex min-h-11 w-full items-center justify-between gap-3 px-4 py-2 text-left hover:bg-zinc-50"><span className="flex min-w-0 items-center gap-2 text-xs font-semibold text-zinc-700"><ShieldCheck className="h-3.5 w-3.5" />运行规则<span className="truncate rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] text-emerald-700">{summary}</span></span>{expanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}</button>{expanded ? <div className="space-y-3 border-t border-zinc-100 p-4 text-xs"><div className="grid grid-cols-2 gap-2"><RuleFact icon={<Clock3 className="h-3.5 w-3.5" />} label="沉默门槛" value={`${settings?.silence_minutes || 1} 分钟`} /><RuleFact icon={<UsersRound className="h-3.5 w-3.5" />} label="账号范围" value={settings?.wechat_allowlist?.length ? `${settings.wechat_allowlist.length} 个账号` : "全部企微"} /><RuleFact icon={<UserRoundCheck className="h-3.5 w-3.5" />} label="接待状态" value="仅 AI" /><RuleFact icon={<CalendarClock className="h-3.5 w-3.5" />} label="加微时间" value="不限" /></div><label className="flex items-center justify-between rounded-lg border border-zinc-200 p-3"><span><span className="block font-medium">启用沉默唤醒</span><span className="text-zinc-500">人工接待和状态未知仍会阻断</span></span><input type="checkbox" checked={enabled} onChange={(event) => setEnabled(event.target.checked)} className="h-4 w-4" /></label><label className="block text-zinc-600">沉默分钟数<input type="number" min={1} max={120} value={silenceMinutes} onChange={(event) => setSilenceMinutes(event.target.value)} className="mt-1 h-9 w-full rounded-lg border border-zinc-200 px-2" /></label><label className="block text-zinc-600">限定企微号（留空为全部）<textarea value={allowlist} onChange={(event) => setAllowlist(event.target.value)} rows={3} className="mt-1 w-full rounded-lg border border-zinc-200 p-2" /></label>{error ? <div className="rounded-lg bg-red-50 p-2 text-red-700">{error}</div> : null}{message ? <div className="rounded-lg bg-emerald-50 p-2 text-emerald-700">{message}</div> : null}<div className="flex gap-2"><button type="button" onClick={() => void loadSettings()} className="h-9 rounded-lg border border-zinc-200 px-3">刷新</button><button type="button" onClick={saveSettings} disabled={saving || loading} className="h-9 flex-1 rounded-lg bg-zinc-900 px-3 text-white disabled:opacity-50">{saving ? "保存中" : "保存配置"}</button></div></div> : null}</section>;
}

function Panel({ title, icon, description, children }: { title: string; icon: ReactNode; description?: string; children: ReactNode }) { return <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm"><div className="mb-3 flex items-start justify-between gap-3"><div><h3 className="flex items-center gap-2 text-sm font-semibold">{icon}{title}</h3>{description ? <p className="mt-1 text-xs text-zinc-500">{description}</p> : null}</div></div>{children}</section>; }
function Fact({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div><div className="text-xs text-zinc-500">{label}</div><div className={`mt-1 break-words text-sm leading-5 ${mono ? "font-mono text-xs" : "font-medium"}`}>{value || "-"}</div></div>; }
function FactRow({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div className="grid grid-cols-[96px_1fr] gap-2"><span className="text-zinc-500">{label}</span><span className={`break-all text-zinc-800 ${mono ? "font-mono" : ""}`}>{value}</span></div>; }
function MiniMetric({ label, value, tone = "zinc" }: { label: string; value: string; tone?: "zinc" | "blue" }) { return <div className={`rounded-lg p-3 ${tone === "blue" ? "bg-blue-50" : "bg-zinc-100"}`}><div className="text-xs text-zinc-500">{label}</div><div className="mt-1 text-lg font-semibold tabular-nums">{value}</div></div>; }
function StepArrow({ index, scene, objective }: { index: number; scene?: string; objective?: string }) { return <div className="flex gap-3"><span className="grid h-7 w-7 shrink-0 place-items-center rounded-full bg-zinc-900 text-xs font-semibold text-white">{index}</span><div><div className="text-sm font-semibold">{sceneLabel(scene)}</div><p className="mt-1 text-xs leading-5 text-zinc-600">{objective || "未记录步骤目标"}</p></div></div>; }
function MaterialBadge({ state }: { state: string }) { const map: Record<string, [string, string]> = { sent: ["已发送素材", "bg-emerald-100 text-emerald-700"], planned: ["已编入任务", "bg-blue-100 text-blue-700"], missing: ["素材未落入任务", "bg-red-100 text-red-700"], text_only: ["纯文字", "bg-zinc-200 text-zinc-600"] }; const [label, tone] = map[state] || [state, "bg-zinc-200 text-zinc-600"]; return <span className={`rounded-full px-2 py-1 text-[11px] font-medium ${tone}`}>{label}</span>; }
function NodeStatus({ status }: { status: string }) { const tone = status === "completed" ? "text-emerald-700" : status === "failed" ? "text-red-700" : status === "warning" ? "text-amber-700" : "text-zinc-400"; const Icon = status === "completed" ? CheckCircle2 : status === "failed" || status === "warning" ? AlertCircle : CircleSlash2; return <span className={`inline-flex items-center gap-1 text-[11px] font-medium ${tone}`}><Icon className="h-3 w-3" />{NODE_STATUS[status] || status}</span>; }
function StatusBadge({ status }: { status: string }) { const tone = status === "failed" ? "bg-red-100 text-red-700" : status === "blocked" || status === "cancelled" ? "bg-amber-100 text-amber-800" : status === "sent" || status === "completed" || status === "created" ? "bg-emerald-100 text-emerald-700" : "bg-zinc-200 text-zinc-700"; return <span className={`shrink-0 rounded-full px-2 py-0.5 text-[11px] font-medium ${tone}`}>{STATUS_LABELS[status] || status}</span>; }
function OverviewCell({ label, value }: { label: string; value: string }) { return <div className="bg-white px-1 py-3 text-center"><div className="text-[10px] text-zinc-500">{label}</div><div className="mt-1 text-lg font-semibold tabular-nums">{value}</div></div>; }
function RuleFact({ icon, label, value }: { icon: ReactNode; label: string; value: string }) { return <div className="rounded-lg border border-zinc-200 p-2.5"><div className="flex items-center gap-1 text-[11px] text-zinc-500">{icon}{label}</div><div className="mt-1 font-semibold">{value}</div></div>; }
function EmptyState({ icon, text }: { icon: ReactNode; text: string }) { return <div className="flex min-h-32 flex-col items-center justify-center gap-2 p-4 text-center text-sm text-zinc-500">{icon}<span>{text}</span></div>; }
function FilterInput({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (value: string) => void; type?: string }) { return <label className="min-w-0 text-xs text-zinc-600"><span>{label}</span><input type={type} value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 h-8 w-full min-w-0 rounded-lg border border-zinc-200 px-2 text-xs" /></label>; }
function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[][] }) { return <label className="text-xs text-zinc-600"><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 h-8 w-full rounded-lg border border-zinc-200 px-2 text-xs"><option value="">全部</option>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>; }

function decisionText(detail: RunDetail, decision: JsonRecord): string { if (detail.status === "failed") return detail.error_message || "运行失败，未形成可执行计划。"; if (detail.status === "blocked") return reasonLabel(detail.reason_code); const need = str(decision.customer_need); const barrier = str(decision.silence_barrier); const next = str(decision.next_business_action); return [need && `客户当前需要：${need}`, barrier && `沉默卡点：${barrier}`, next && `因此下一步：${next}`].filter(Boolean).join("；") || reasonLabel(detail.reason_code); }
function nodeOutputSummary(node: WorkflowNode): string { const output = record(node.output); if (!Object.keys(output).length) return NODE_STATUS[node.status] || "没有输出"; if (node.key.includes("scene_analyst")) { const mainline = record(output.customer_mainline); return `${output.eligible === false ? "停止触达" : "允许触达"}；${sceneLabel(output.step1_scene)} → ${sceneLabel(output.step2_scene)}；${str(mainline.silence_barrier) || "未识别明确卡点"}`; } if (node.key.includes("plan_writer")) return `${str(output.plan_goal) || "已生成计划"}；共 ${array(output.steps).length} 步`; if (node.key.includes("verifier")) return `审核结论：${str(output.decision) || "未记录"}；发现 ${array(output.violations).length} 个问题`; return "节点已返回结构化结果"; }
function precedenceLabel(value: unknown): string { const key = str(value); const labels: Record<string, string> = { no_blocker_sop_progression: "无明确卡点，继续主线", effect_need: "客户需要效果证据", distance_after_store: "门店后距离顾虑", time_deposit_objection: "时间或预约金顾虑", payment_intent: "客户出现付款意向", hard_boundary: "命中安全边界" }; return labels[key] || key || "未记录"; }
function sceneLabel(value: unknown): string { const key = str(value); return SCENE_LABELS[key] || key || "未选择"; }
function reasonLabel(value?: string): string { const key = str(value); return REASON_LABELS[key] || key || "无阻断，继续处理"; }
function taskLabel(value?: string): string { const key = str(value); return ({ pending: "待执行", checking: "检查中", sending: "发送中", sent: "已发送", skipped: "已取消", failed: "失败", check_failed: "检查失败" } as Record<string, string>)[key] || key || "未创建"; }
function eventLabel(value: string): string { return ({ workflow_started: "唤醒判断启动", plan_created: "计划创建", plan_auto_approved: "计划进入发送队列", task_sent: "任务发送", task_skipped_customer_replied: "客户回复，取消任务", task_skipped_non_ai_mode: "转为人工接待，取消任务", ai_mode_check_failed: "AI 状态确认失败", task_failed: "任务失败", plan_cycle_completed: "两步计划完成" } as Record<string, string>)[value] || value; }
function taskScene(task: JsonRecord): unknown { const metadata = array(task.content_source_metadata).filter((item): item is JsonRecord => isRecord(item)); return metadata.find((item) => "scene" in item)?.scene || ""; }
function messageText(message: JsonRecord): string { const value = message.content ?? message.text ?? ""; if (typeof value === "string") return value; if (isRecord(value)) return str(value.text || value.content || value.url) || pretty(value); return pretty(value); }
function formatDuration(value?: number): string { if (!value) return "-"; return value < 1000 ? `${Math.round(value)} ms` : `${(value / 1000).toFixed(1)} s`; }
function formatTime(value?: string): string { if (!value) return "-"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false }); }
function record(value: unknown): JsonRecord { return isRecord(value) ? value : {}; }
function array(value: unknown): unknown[] { return Array.isArray(value) ? value : []; }
function isRecord(value: unknown): value is JsonRecord { return Boolean(value) && typeof value === "object" && !Array.isArray(value); }
function str(value: unknown): string { return value == null ? "" : String(value).trim(); }
function num(value: unknown): number { const parsed = Number(value); return Number.isFinite(parsed) ? parsed : 0; }
function pretty(value: unknown): string { try { return JSON.stringify(value, null, 2); } catch { return String(value); } }
