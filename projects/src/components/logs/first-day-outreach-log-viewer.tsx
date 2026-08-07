"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useCallback, useEffect, useState } from "react";
import {
  AlertCircle,
  ArrowLeft,
  Bot,
  ChevronLeft,
  ChevronRight,
  Clock3,
  Filter,
  ListTree,
  LoaderCircle,
  MessageSquareText,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
} from "lucide-react";

type JsonRecord = Record<string, unknown>;

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
};

type RunDetail = RunSummary & {
  input_snapshot?: JsonRecord;
  workflow?: JsonRecord;
  final_plan?: JsonRecord;
  tasks?: JsonRecord[];
  events?: JsonRecord[];
  raw_redacted_at?: string;
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

const TABS = ["执行摘要", "聊天上下文", "场景分析", "模型节点", "发送时间线", "原始 JSON"] as const;
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

export function FirstDayOutreachLogViewer() {
  const [filters, setFilters] = useState<Filters>(EMPTY_FILTERS);
  const [items, setItems] = useState<RunSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [nextCursor, setNextCursor] = useState("");
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [activeCursor, setActiveCursor] = useState("");
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<Tab>("执行摘要");
  const [showMobileDetail, setShowMobileDetail] = useState(false);

  const loadRuns = useCallback(async (cursor = "") => {
    setLoading(true);
    setError("");
    const search = new URLSearchParams({ limit: "50" });
    Object.entries(filters).forEach(([key, value]) => {
      if (!value) return;
      if (key === "started_from" || key === "started_to") {
        search.set(key, new Date(value).toISOString());
      } else {
        search.set(key, value);
      }
    });
    if (cursor) search.set("cursor", cursor);
    try {
      const response = await fetch(`/api/outreach/first-day-runs?${search.toString()}`, { cache: "no-store" });
      const data = (await response.json()) as { items?: RunSummary[]; next_cursor?: string; detail?: string };
      if (!response.ok) throw new Error(data.detail || "加载首日触达日志失败");
      const nextItems = Array.isArray(data.items) ? data.items : [];
      setItems(nextItems);
      setNextCursor(data.next_cursor || "");
      setActiveCursor(cursor);
      setSelectedId((current) => (nextItems.some((item) => item.workflow_run_id === current) ? current : nextItems[0]?.workflow_run_id || ""));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载首日触达日志失败");
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const loadDetail = useCallback(async (workflowRunId: string) => {
    if (!workflowRunId) return;
    setDetailLoading(true);
    setDetail(null);
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

  const runSearch = () => {
    setCursorHistory([]);
    setSelectedId("");
    setDetail(null);
    void loadRuns("");
  };

  const selectRun = (id: string) => {
    setSelectedId(id);
    setTab("执行摘要");
    setShowMobileDetail(true);
  };

  return (
    <main className="flex min-h-screen bg-zinc-50 text-zinc-950 md:h-screen md:overflow-hidden">
      <aside className={`${showMobileDetail ? "hidden" : "flex"} min-h-screen w-full flex-col border-r border-zinc-200 bg-white md:flex md:min-h-0 md:w-[410px] md:min-w-[360px]`}>
        <header className="border-b border-zinc-200 p-4">
          <div className="flex items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2 text-lg font-semibold"><ListTree className="h-5 w-5" />首日千人千面日志</div>
              <p className="mt-1 text-xs text-zinc-500">从触发判断到两步发送的完整运行记录</p>
            </div>
            <button type="button" title="刷新" onClick={() => {
              void loadRuns(activeCursor);
              if (selectedId) void loadDetail(selectedId);
            }} className="grid h-9 w-9 place-items-center rounded-md border border-zinc-200 hover:bg-zinc-50">
              <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            </button>
          </div>
          <nav className="mt-3 flex gap-2 text-sm">
            <Link href="/logs" className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 px-2.5 py-1.5 hover:bg-zinc-50"><ArrowLeft className="h-4 w-4" />运行日志</Link>
            <Link href="/outreach" className="inline-flex items-center gap-1.5 rounded-md border border-zinc-200 px-2.5 py-1.5 hover:bg-zinc-50"><Send className="h-4 w-4" />历史主动唤醒</Link>
          </nav>
        </header>

        <section className="border-b border-zinc-200 p-4">
          <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-zinc-600"><Filter className="h-3.5 w-3.5" />筛选条件</div>
          <div className="grid grid-cols-2 gap-2">
            <FilterInput label="客户 ID" value={filters.customer_id} onChange={(value) => setFilters((prev) => ({ ...prev, customer_id: value }))} />
            <FilterInput label="external_userid" value={filters.external_userid} onChange={(value) => setFilters((prev) => ({ ...prev, external_userid: value }))} />
            <FilterInput label="企业 ID" value={filters.corp_id} onChange={(value) => setFilters((prev) => ({ ...prev, corp_id: value }))} />
            <FilterInput label="接待账号" value={filters.wechat} onChange={(value) => setFilters((prev) => ({ ...prev, wechat: value }))} />
            <FilterSelect label="状态" value={filters.status} onChange={(value) => setFilters((prev) => ({ ...prev, status: value }))} options={Object.entries(STATUS_LABELS)} />
            <FilterSelect label="失败" value={filters.failed} onChange={(value) => setFilters((prev) => ({ ...prev, failed: value }))} options={[["true", "仅失败"], ["false", "排除失败"]]} />
            <FilterInput label="原因码" value={filters.reason_code} onChange={(value) => setFilters((prev) => ({ ...prev, reason_code: value }))} />
            <FilterInput label="计划 ID" value={filters.plan_id} onChange={(value) => setFilters((prev) => ({ ...prev, plan_id: value }))} />
            <FilterSelect label="第一场景" value={filters.first_scene} onChange={(value) => setFilters((prev) => ({ ...prev, first_scene: value }))} options={Object.entries(SCENE_LABELS)} />
            <FilterSelect label="第二场景" value={filters.second_scene} onChange={(value) => setFilters((prev) => ({ ...prev, second_scene: value }))} options={Object.entries(SCENE_LABELS)} />
            <FilterInput label="开始时间" type="datetime-local" value={filters.started_from} onChange={(value) => setFilters((prev) => ({ ...prev, started_from: value }))} />
            <FilterInput label="结束时间" type="datetime-local" value={filters.started_to} onChange={(value) => setFilters((prev) => ({ ...prev, started_to: value }))} />
          </div>
          <button type="button" onClick={runSearch} className="mt-3 inline-flex h-9 w-full items-center justify-center gap-2 rounded-md bg-zinc-900 px-3 text-sm text-white hover:bg-zinc-800"><Search className="h-4 w-4" />查询</button>
          {error ? <div className="mt-3 flex gap-2 border-l-2 border-red-500 bg-red-50 p-2 text-xs text-red-700"><AlertCircle className="h-4 w-4 shrink-0" />{error}</div> : null}
        </section>

        <section className="min-h-0 flex-1 overflow-y-auto">
          {loading && items.length === 0 ? <EmptyState icon={<LoaderCircle className="h-5 w-5 animate-spin" />} text="正在加载运行记录" /> : null}
          {!loading && items.length === 0 ? <EmptyState icon={<Search className="h-5 w-5" />} text="当前筛选条件下没有运行记录" /> : null}
          {items.map((item) => <RunListItem key={item.workflow_run_id} item={item} selected={selectedId === item.workflow_run_id} onClick={() => selectRun(item.workflow_run_id)} />)}
        </section>

        <footer className="flex items-center justify-between border-t border-zinc-200 p-3 text-xs text-zinc-500">
          <span>每页最多 50 条</span>
          <div className="flex gap-1">
            <button type="button" title="上一页" disabled={cursorHistory.length === 0 || loading} onClick={() => {
              const history = [...cursorHistory];
              const previous = history.pop() || "";
              setCursorHistory(history);
              void loadRuns(previous);
            }} className="grid h-8 w-8 place-items-center rounded-md border border-zinc-200 disabled:opacity-40"><ChevronLeft className="h-4 w-4" /></button>
            <button type="button" title="下一页" disabled={!nextCursor || loading} onClick={() => {
              setCursorHistory((prev) => [...prev, activeCursor]);
              void loadRuns(nextCursor);
            }} className="grid h-8 w-8 place-items-center rounded-md border border-zinc-200 disabled:opacity-40"><ChevronRight className="h-4 w-4" /></button>
          </div>
        </footer>
      </aside>

      <section className={`${showMobileDetail ? "flex" : "hidden"} min-h-screen min-w-0 flex-1 flex-col bg-white md:flex md:min-h-0`}>
        <header className="border-b border-zinc-200 px-4 py-3 md:px-6">
          <div className="flex min-w-0 items-center gap-3">
            <button type="button" title="返回列表" onClick={() => setShowMobileDetail(false)} className="grid h-9 w-9 shrink-0 place-items-center rounded-md border border-zinc-200 md:hidden"><ChevronLeft className="h-4 w-4" /></button>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="truncate font-mono text-sm font-semibold">{selected?.workflow_run_id || "选择一条运行记录"}</h2>
                {selected?.status ? <StatusBadge status={selected.status} /> : null}
              </div>
              {selected ? <p className="mt-1 truncate text-xs text-zinc-500">{formatTime(selected.started_at)} · {selected.wechat || "未知接待账号"} · {selected.customer_id || selected.external_userid}</p> : null}
            </div>
            {detailLoading ? <LoaderCircle className="h-4 w-4 animate-spin text-zinc-400" /> : null}
          </div>
        </header>

        {selected ? (
          <>
            <nav className="flex shrink-0 gap-1 overflow-x-auto border-b border-zinc-200 px-3 py-2 md:px-6">
              {TABS.map((item) => <button key={item} type="button" onClick={() => setTab(item)} className={`h-8 shrink-0 rounded-md px-3 text-sm ${tab === item ? "bg-zinc-900 text-white" : "text-zinc-600 hover:bg-zinc-100"}`}>{item}</button>)}
            </nav>
            <div className="min-h-0 flex-1 overflow-y-auto p-4 md:p-6">
              {selectedDetail ? <DetailTab tab={tab} detail={selectedDetail} /> : <EmptyState icon={<LoaderCircle className="h-5 w-5 animate-spin" />} text="正在加载详情" />}
            </div>
          </>
        ) : <EmptyState icon={<ListTree className="h-6 w-6" />} text="从左侧选择一条运行记录" />}
      </section>
    </main>
  );
}

function RunListItem({ item, selected, onClick }: { item: RunSummary; selected: boolean; onClick: () => void }) {
  return <button type="button" onClick={onClick} className={`w-full border-b border-zinc-100 p-4 text-left transition-colors ${selected ? "bg-zinc-100" : "hover:bg-zinc-50"}`}>
    <div className="flex items-start justify-between gap-3"><span className="truncate text-sm font-medium">{item.customer_id || item.external_userid || "未知客户"}</span><StatusBadge status={item.status || "running"} /></div>
    <div className="mt-2 flex items-center gap-1.5 text-xs text-zinc-600"><span>{sceneLabel(item.first_scene)}</span><ChevronRight className="h-3 w-3" /><span>{sceneLabel(item.second_scene)}</span></div>
    <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-zinc-500"><span>任务 1：{taskLabel(item.first_task_status)}</span><span>任务 2：{taskLabel(item.second_task_status)}</span><span>{item.model_attempt_count || 0} 次模型调用</span><span>{item.retry_count || 0} 次重试 · {formatDuration(item.duration_ms)}</span></div>
    <div className="mt-2 flex items-center justify-between gap-2 text-xs text-zinc-400"><span className="truncate font-mono">{item.reason_code || "-"}</span><span className="shrink-0">{formatTime(item.started_at)}</span></div>
  </button>;
}

function DetailTab({ tab, detail }: { tab: Tab; detail: RunDetail }) {
  if (tab === "执行摘要") return <SummaryTab detail={detail} />;
  if (tab === "聊天上下文") return <ChatTab detail={detail} />;
  if (tab === "场景分析") return <JsonSection value={sceneAnalysis(detail)} empty="没有场景分析结果" />;
  if (tab === "模型节点") return <ModelTab detail={detail} />;
  if (tab === "发送时间线") return <TimelineTab detail={detail} />;
  return <JsonSection value={detail} empty="没有原始 JSON" />;
}

function SummaryTab({ detail }: { detail: RunDetail }) {
  const sentMessages = (detail.tasks || []).flatMap((task) => task.status === "sent" && Array.isArray(task.reply_messages) ? task.reply_messages : []);
  const secondTask = (detail.tasks || []).find((task) => Number(task.step_index) === 2);
  return <div className="space-y-6">
    <section className="grid gap-px overflow-hidden rounded-md border border-zinc-200 bg-zinc-200 sm:grid-cols-2 xl:grid-cols-4">
      <SummaryFact label="为什么触发" value={String((detail.input_snapshot?.trigger_context as JsonRecord | undefined)?.trigger_type || detail.reason_code || "无记录")} />
      <SummaryFact label="场景选择" value={`${sceneLabel(detail.first_scene)} → ${sceneLabel(detail.second_scene)}`} />
      <SummaryFact label="实际发送" value={sentMessages.length ? `${sentMessages.length} 条结构消息` : "尚未发送"} />
      <SummaryFact label="第二步结论" value={secondTask ? `${taskLabel(String(secondTask.status || ""))}${secondTask.error_message ? `：${secondTask.error_message}` : ""}` : detail.final_decision || "未创建"} />
    </section>
    <section>
      <SectionTitle icon={<Clock3 className="h-4 w-4" />} title="运行指标" />
      <dl className="mt-3 grid gap-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
        <Metric label="状态" value={STATUS_LABELS[detail.status || ""] || detail.status || "-"} />
        <Metric label="原因码" value={detail.reason_code || "-"} mono />
        <Metric label="模型尝试" value={`${detail.model_attempt_count || 0} 次`} />
        <Metric label="重试" value={`${detail.retry_count || 0} 次`} />
        <Metric label="总耗时" value={formatDuration(detail.duration_ms)} />
        <Metric label="计划 ID" value={detail.plan_id || "未创建"} mono />
        <Metric label="开始时间" value={formatTime(detail.started_at)} />
        <Metric label="结束时间" value={formatTime(detail.finished_at)} />
      </dl>
    </section>
    {detail.error_message ? <section className="border-l-2 border-red-500 bg-red-50 p-4 text-sm text-red-800"><div className="font-semibold">{detail.error_node || "运行失败"} · {detail.error_type}</div><pre className="mt-2 whitespace-pre-wrap break-words font-mono text-xs">{detail.error_message}</pre></section> : null}
    <section><SectionTitle icon={<Send className="h-4 w-4" />} title="最终两步任务" /><TaskList tasks={detail.tasks || []} /></section>
  </div>;
}

function ChatTab({ detail }: { detail: RunDetail }) {
  const messages = Array.isArray(detail.input_snapshot?.recent_messages) ? detail.input_snapshot.recent_messages as JsonRecord[] : [];
  if (!messages.length) return <EmptyState icon={<MessageSquareText className="h-5 w-5" />} text={detail.raw_redacted_at ? `原始聊天已于 ${formatTime(detail.raw_redacted_at)} 按保留策略清除` : "没有聊天上下文"} />;
  return <div className="mx-auto max-w-4xl space-y-3">{messages.map((message, index) => {
    const role = String(message.role || message.sender_type || "unknown");
    const customer = ["customer", "user", "external"].includes(role);
    return <article key={`${String(message.msgid || "message")}-${index}`} className={`flex ${customer ? "justify-start" : "justify-end"}`}><div className={`max-w-[88%] rounded-md border p-3 ${customer ? "border-zinc-200 bg-white" : "border-emerald-200 bg-emerald-50"}`}><div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-zinc-500"><span className="font-medium text-zinc-700">{customer ? "客户" : role === "assistant" ? "AI" : "客服"}</span><span>{formatTime(String(message.created_at || message.msgtime || ""))}</span><span>{String(message.msgtype || "text")}</span></div><div className="whitespace-pre-wrap break-words text-sm">{messageText(message)}</div></div></article>;
  })}</div>;
}

function ModelTab({ detail }: { detail: RunDetail }) {
  const workflow = detail.workflow || {};
  const nodes = Object.entries(workflow).filter(([name, value]) => name !== "summary" && value && typeof value === "object");
  if (!nodes.length) return <EmptyState icon={<Bot className="h-5 w-5" />} text="没有模型节点记录" />;
  return <div className="space-y-4">{nodes.map(([name, value]) => <details key={name} open className="rounded-md border border-zinc-200"><summary className="cursor-pointer px-4 py-3 text-sm font-semibold">{nodeLabel(name)}</summary><div className="border-t border-zinc-200 p-4"><JsonSection value={value} /></div></details>)}</div>;
}

function TimelineTab({ detail }: { detail: RunDetail }) {
  const entries = [
    { at: detail.started_at, type: "workflow_started", summary: "首日千人千面工作流开始", payload: {} },
    ...(detail.events || []).map((event) => ({ at: String(event.created_at || ""), type: String(event.event_type || "event"), summary: String(event.event_summary || ""), payload: event.payload || {} })),
    ...((detail.tasks || []).map((task) => ({ at: String(task.sent_at || task.updated_at || task.scheduled_at || ""), type: `task_${String(task.status || "unknown")}`, summary: `第 ${String(task.step_index || "-")} 步：${taskLabel(String(task.status || ""))}`, payload: task }))),
  ].filter((entry) => entry.at).sort((a, b) => String(a.at).localeCompare(String(b.at)));
  return <div className="relative ml-2 border-l border-zinc-300 pl-6">{entries.map((entry, index) => <article key={`${entry.type}-${entry.at}-${index}`} className="relative pb-6"><span className="absolute -left-[29px] top-1 h-2.5 w-2.5 rounded-full border-2 border-white bg-zinc-700" /><div className="text-xs text-zinc-500">{formatTime(entry.at)}</div><div className="mt-1 text-sm font-semibold">{eventLabel(entry.type)}</div><div className="mt-1 text-sm text-zinc-600">{entry.summary}</div><details className="mt-2"><summary className="cursor-pointer text-xs text-zinc-500">查看数据</summary><pre className="mt-2 max-h-80 overflow-auto rounded-md bg-zinc-950 p-3 text-xs text-zinc-100">{pretty(entry.payload)}</pre></details></article>)}</div>;
}

function TaskList({ tasks }: { tasks: JsonRecord[] }) {
  if (!tasks.length) return <p className="mt-3 text-sm text-zinc-500">本次运行未创建计划任务。</p>;
  return <div className="mt-3 divide-y divide-zinc-200 border-y border-zinc-200">{tasks.map((task, index) => <article key={String(task.id || index)} className="py-4"><div className="flex flex-wrap items-center justify-between gap-2"><div className="font-semibold">第 {String(task.step_index || index + 1)} 步 · {sceneLabel(taskScene(task))}</div><span className="text-xs text-zinc-500">{taskLabel(String(task.status || ""))} · {formatTime(String(task.scheduled_at || ""))}</span></div><div className="mt-2 space-y-1">{(Array.isArray(task.reply_messages) ? task.reply_messages : []).map((message, messageIndex) => <div key={messageIndex} className="rounded-md bg-zinc-50 p-2 text-sm"><span className="mr-2 font-mono text-xs text-zinc-500">{String((message as JsonRecord).type || "text")}</span>{messageText(message as JsonRecord)}</div>)}</div></article>)}</div>;
}

function JsonSection({ value, empty = "" }: { value: unknown; empty?: string }) {
  if (value == null || (typeof value === "object" && Object.keys(value as object).length === 0)) return empty ? <EmptyState icon={<ShieldCheck className="h-5 w-5" />} text={empty} /> : null;
  return <pre className="max-h-[70vh] overflow-auto rounded-md bg-zinc-950 p-4 font-mono text-xs leading-5 text-zinc-100">{pretty(value)}</pre>;
}

function FilterInput({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (value: string) => void; type?: string }) {
  return <label className="min-w-0 text-xs text-zinc-600"><span>{label}</span><input type={type} value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 h-8 w-full min-w-0 rounded-md border border-zinc-200 px-2 text-xs outline-none focus:border-zinc-500" /></label>;
}

function FilterSelect({ label, value, onChange, options }: { label: string; value: string; onChange: (value: string) => void; options: string[][] }) {
  return <label className="text-xs text-zinc-600"><span>{label}</span><select value={value} onChange={(event) => onChange(event.target.value)} className="mt-1 h-8 w-full rounded-md border border-zinc-200 px-2 text-xs"><option value="">全部</option>{options.map(([key, text]) => <option key={key} value={key}>{text}</option>)}</select></label>;
}

function StatusBadge({ status }: { status: string }) {
  const tone = status === "failed" ? "bg-red-100 text-red-700" : status === "blocked" || status === "cancelled" ? "bg-amber-100 text-amber-800" : status === "sent" || status === "completed" ? "bg-emerald-100 text-emerald-700" : "bg-zinc-200 text-zinc-700";
  return <span className={`shrink-0 rounded px-2 py-0.5 text-xs font-medium ${tone}`}>{STATUS_LABELS[status] || status}</span>;
}

function SummaryFact({ label, value }: { label: string; value: string }) { return <div className="min-h-24 bg-white p-4"><div className="text-xs font-medium text-zinc-500">{label}</div><div className="mt-2 break-words text-sm font-semibold leading-6">{value}</div></div>; }
function Metric({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) { return <div><dt className="text-xs text-zinc-500">{label}</dt><dd className={`mt-1 break-words ${mono ? "font-mono text-xs" : ""}`}>{value}</dd></div>; }
function SectionTitle({ icon, title }: { icon: ReactNode; title: string }) { return <h3 className="flex items-center gap-2 text-sm font-semibold">{icon}{title}</h3>; }
function EmptyState({ icon, text }: { icon: ReactNode; text: string }) { return <div className="flex h-full min-h-20 flex-col items-center justify-center gap-1 p-3 text-center text-sm text-zinc-500">{icon}<span>{text}</span></div>; }

function sceneAnalysis(detail: RunDetail): unknown { const workflow = detail.workflow || {}; return workflow.scene_analysis || (workflow.scene_analyst as JsonRecord | undefined)?.output || {}; }
function sceneLabel(value: unknown): string { const key = String(value || ""); return SCENE_LABELS[key] || key || "未选择"; }
function taskLabel(value?: string): string { return ({ pending: "待执行", checking: "检查中", sending: "发送中", sent: "已发送", skipped: "已取消", failed: "失败", check_failed: "检查失败" } as Record<string, string>)[value || ""] || value || "未创建"; }
function nodeLabel(value: string): string { return ({ scene_analyst: "场景分析", scene_analyst_schema_repair: "场景 Schema 修复", plan_writer: "计划写作", contract_verifier: "合同审核", contract_verifier_schema_repair: "审核 Schema 修复", plan_writer_repair: "受限写作修复" } as Record<string, string>)[value] || value; }
function eventLabel(value: string): string { return ({ workflow_started: "工作流启动", plan_created: "计划创建", plan_auto_approved: "计划进入发送队列", task_sent: "任务发送", task_skipped_customer_replied: "客户回复，取消任务", task_failed: "任务失败", plan_cycle_completed: "两步计划完成" } as Record<string, string>)[value] || value; }
function taskScene(task: JsonRecord): unknown { const metadata = Array.isArray(task.content_source_metadata) ? task.content_source_metadata : []; return (metadata.find((item) => item && typeof item === "object" && "scene" in item) as JsonRecord | undefined)?.scene || ""; }
function messageText(message: JsonRecord): string { const value = message.content ?? message.text ?? message.reply_messages ?? ""; return typeof value === "string" ? value : pretty(value); }
function formatDuration(value?: number): string { if (!value) return "-"; return value < 1000 ? `${value} ms` : `${(value / 1000).toFixed(1)} s`; }
function formatTime(value?: string): string { if (!value) return "-"; const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", { hour12: false }); }
function pretty(value: unknown): string { try { return JSON.stringify(value, null, 2); } catch { return String(value); } }
