"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Clock,
  FileText,
  ListChecks,
  MessageSquareText,
  Pause,
  Play,
  RefreshCw,
  Search,
  Send,
  UserRound,
  XCircle,
} from "lucide-react";

type JsonObject = Record<string, unknown>;

type Candidate = {
  customer_id: string;
  external_userid?: string;
  corp_id?: string;
  user_id?: string;
  wechat?: string;
  title?: string;
  lifecycle_stage?: string;
  last_customer_message_at?: string;
  last_staff_message_at?: string;
  last_ai_reply_at?: string;
  last_outreach_at?: string;
  outreach_status?: string;
  outreach_plan_id?: string;
  silent_minutes?: number;
  last_customer_message?: string;
  latest_event_summary?: string;
  portrait?: JsonObject;
  basic_info?: JsonObject;
};

type OutreachPlan = {
  id: string;
  customer_id: string;
  status: string;
  customer_stage?: string;
  stall_reason?: string;
  customer_psychology?: string;
  plan_goal?: string;
  source_snapshot?: JsonObject;
  created_at?: string;
  updated_at?: string;
};

type OutreachTask = {
  id: string;
  plan_id: string;
  customer_id: string;
  step_index: number;
  scheduled_at?: string;
  status: string;
  intent?: string;
  message_goal?: string;
  reply_messages?: Array<JsonObject>;
  before_send_check?: number | boolean;
  sent_at?: string;
  send_status?: string;
  system_msgid?: string;
  error_message?: string;
};

type OutreachEvent = {
  id: string;
  plan_id?: string;
  task_id?: string;
  customer_id: string;
  event_type: string;
  event_summary?: string;
  payload?: JsonObject;
  created_at?: string;
};

type ConversationMessage = {
  content?: unknown;
  text?: unknown;
  msg_content?: unknown;
  direction?: unknown;
  from?: unknown;
  sender_type?: unknown;
  sender_name?: unknown;
  msgtime?: unknown;
  created_at?: unknown;
  send_time?: unknown;
  msgtype?: unknown;
};

type PlanDetail = {
  plan?: OutreachPlan;
  tasks?: OutreachTask[];
  events?: OutreachEvent[];
};

type Filters = {
  silentMinutesMin: string;
  lifecycleStage: string;
  outreachStatus: string;
  noPlanOnly: boolean;
  limit: string;
};

const DEFAULT_FILTERS: Filters = {
  silentMinutesMin: "60",
  lifecycleStage: "",
  outreachStatus: "",
  noPlanOnly: true,
  limit: "50",
};

const STATUS_LABELS: Record<string, string> = {
  none: "无计划",
  draft: "草稿",
  active: "执行中",
  waiting: "等待下一步",
  paused: "暂停",
  completed: "已完成",
  cancelled: "已取消",
  failed: "失败",
  check_failed: "复查失败",
  handoff: "专业协助",
  pending: "待执行",
  checking: "检查中",
  sent: "已发送",
  skipped: "已跳过",
};

function statusLabel(value?: string) {
  return STATUS_LABELS[String(value || "none")] || String(value || "无");
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

function formatSilent(minutes?: number) {
  if (minutes == null || Number.isNaN(minutes)) return "-";
  if (minutes < 60) return `${Math.max(0, Math.round(minutes))}分钟`;
  if (minutes < 1440) return `${(minutes / 60).toFixed(1)}小时`;
  return `${(minutes / 1440).toFixed(1)}天`;
}

function jsonPreview(value: unknown) {
  if (value == null || value === "") return "-";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

async function readJsonResponse(response: Response) {
  const text = await response.text();
  if (!text) return {};
  try {
    return JSON.parse(text);
  } catch {
    return { error: text };
  }
}

function outreachErrorMessage(data: JsonObject, fallback: string) {
  if (data.error === "conversation_refresh_failed") {
    return "历史聊天查询超时，请稍后重试或降低条数";
  }
  if (data.error === "outreach_plan_generation_failed") {
    return "生成计划失败，请稍后重试";
  }
  if (data.error === "preview_required") {
    return "请先生成预览，人工确认后再执行";
  }
  if (data.status === "check_failed") {
    return "发送前复查历史失败，已阻止发送。请刷新历史后重试";
  }
  return String(data.detail || data.error || fallback);
}

function taskHasPreview(task: OutreachTask) {
  return Array.isArray(task.reply_messages) && task.reply_messages.length > 0;
}

function sendStatusLabel(value?: string) {
  if (!value) return "-";
  if (value === "accepted_no_response") return "平台已接收请求/待回查";
  if (value === "accepted") return "平台请求已发出/待回查";
  return value;
}

function messagePreview(messages?: Array<JsonObject>) {
  if (!messages?.length) return "发送前由模型生成";
  return messages
    .map((item) => {
      const content = item.content as JsonObject | undefined;
      if (item.type === "image") return "[图片]";
      if (item.type === "store_address") return `[门店卡片:${String(content?.store_id || "")}]`;
      if (item.type === "payment_collection") return `[收款入口:${String(content?.amount || 10)}元]`;
      return String(content?.text || item.type || "");
    })
    .filter(Boolean)
    .join(" / ");
}

function messageText(message: ConversationMessage) {
  const content = message.content ?? message.text ?? message.msg_content;
  if (content == null) return "";
  if (typeof content === "string") return content;
  if (typeof content === "object") {
    const value = content as JsonObject;
    return String(value.text || value.content || value.url || JSON.stringify(value));
  }
  return String(content);
}

function messageSender(message: ConversationMessage) {
  const direction = String(message.direction || message.from || message.sender_type || "").toLowerCase();
  if (["customer", "user", "external"].includes(direction)) return "客户";
  if (["staff", "assistant", "service", "ai"].includes(direction)) return "员工";
  return String(message.sender_name || direction || "消息");
}

function messageTime(message: ConversationMessage) {
  return formatTime(String(message.msgtime || message.created_at || message.send_time || ""));
}

export function OutreachWorkbench() {
  const [filters, setFilters] = useState<Filters>(DEFAULT_FILTERS);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [selectedCustomer, setSelectedCustomer] = useState<Candidate | null>(null);
  const [selectedPlanId, setSelectedPlanId] = useState("");
  const [planDetail, setPlanDetail] = useState<PlanDetail | null>(null);
  const [events, setEvents] = useState<OutreachEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCustomer, setHistoryCustomer] = useState<Candidate | null>(null);
  const [historyMessages, setHistoryMessages] = useState<ConversationMessage[]>([]);

  const selectedPlan = planDetail?.plan || null;
  const tasks = useMemo(() => planDetail?.tasks || [], [planDetail]);
  const planEvents = useMemo(() => planDetail?.events || [], [planDetail]);

  const loadCandidates = useCallback(async () => {
    setLoading(true);
    setError("");
    const search = new URLSearchParams();
    search.set("silent_minutes_min", filters.silentMinutesMin || "0");
    search.set("limit", filters.limit || "50");
    if (filters.lifecycleStage) search.set("lifecycle_stage", filters.lifecycleStage);
    if (filters.outreachStatus) search.set("outreach_status", filters.outreachStatus);
    if (filters.noPlanOnly) search.set("no_plan_only", "true");
    try {
      const response = await fetch(`/api/outreach/candidates?${search.toString()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "加载候选客户失败");
      const items = Array.isArray(data.items) ? data.items : [];
      setCandidates(items);
      if (!selectedCustomer && items.length) setSelectedCustomer(items[0]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [filters, selectedCustomer]);

  const loadEvents = useCallback(async () => {
    const response = await fetch("/api/outreach/events?limit=80", { cache: "no-store" });
    const data = await response.json();
    if (response.ok) setEvents(Array.isArray(data.items) ? data.items : []);
  }, []);

  const loadPlan = useCallback(async (planId: string) => {
    if (!planId) return;
    setBusy("load-plan");
    setError("");
    try {
      const response = await fetch(`/api/outreach/plans/${encodeURIComponent(planId)}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "加载计划失败");
      setPlanDetail(data);
      setSelectedPlanId(planId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }, []);

  const generatePlan = useCallback(
    async (candidate: Candidate, activate = false) => {
      setBusy(activate ? "generate-activate" : "generate");
      setError("");
      setNotice("");
      try {
        const refreshResponse = await fetch(`/api/outreach/customers/${encodeURIComponent(candidate.customer_id)}/refresh-conversation`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            corp_id: candidate.corp_id || "",
            user_id: candidate.user_id || "",
            wechat: candidate.wechat || "",
            external_userid: candidate.external_userid || candidate.customer_id,
            limit: 30,
          }),
        });
        const refreshData = await readJsonResponse(refreshResponse);
        if (!refreshResponse.ok) throw new Error(outreachErrorMessage(refreshData, "生成计划前刷新历史失败"));
        const messages = Array.isArray(refreshData.messages) ? refreshData.messages : [];
        if (messages.length === 0) {
          throw new Error("生成计划前未获取到历史聊天，请先确认客户信息或稍后重试");
        }
        if (refreshData.warning) setNotice(String(refreshData.warning));
        const response = await fetch("/api/outreach/plans/generate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            customer_id: candidate.customer_id,
            corp_id: candidate.corp_id || "",
            user_id: candidate.user_id || "",
            wechat: candidate.wechat || "",
            external_userid: candidate.external_userid || candidate.customer_id,
            current_stage: candidate.lifecycle_stage || "",
            business_goal: "推进客户支付10元预约金并到店",
          }),
        });
        const data = await readJsonResponse(response);
        if (!response.ok) throw new Error(outreachErrorMessage(data, "生成计划失败"));
        const planId = data?.plan?.id || data?.id;
        if (planId) {
          if (activate) {
            await fetch(`/api/outreach/plans/${encodeURIComponent(planId)}/activate`, { method: "POST" });
          }
          await loadPlan(planId);
          await loadCandidates();
          await loadEvents();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadCandidates, loadEvents, loadPlan]
  );

  const planAction = useCallback(
    async (action: "activate" | "pause" | "resume" | "cancel") => {
      if (!selectedPlan?.id) return;
      setBusy(action);
      setError("");
      setNotice("");
      try {
        const response = await fetch(`/api/outreach/plans/${encodeURIComponent(selectedPlan.id)}/${action}`, {
          method: "POST",
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data?.error || "更新计划失败");
        setPlanDetail(data);
        await loadCandidates();
        await loadEvents();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadCandidates, loadEvents, selectedPlan]
  );

  const executeTask = useCallback(
    async (taskId: string) => {
      setBusy(`task-${taskId}`);
      setError("");
      setNotice("");
      try {
        const response = await fetch(`/api/outreach/tasks/${encodeURIComponent(taskId)}/execute`, { method: "POST" });
        const data = await readJsonResponse(response);
        if (!response.ok || data.ok === false) {
          if (selectedPlanId) await loadPlan(selectedPlanId);
          await loadEvents();
          throw new Error(outreachErrorMessage(data, "执行任务失败"));
        }
        if (selectedPlanId) await loadPlan(selectedPlanId);
        await loadEvents();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadEvents, loadPlan, selectedPlanId]
  );

  const previewTask = useCallback(
    async (taskId: string) => {
      setBusy(`preview-${taskId}`);
      setError("");
      setNotice("");
      try {
        const response = await fetch(`/api/outreach/tasks/${encodeURIComponent(taskId)}/preview`, { method: "POST" });
        const data = await readJsonResponse(response);
        if (!response.ok || data.ok === false) throw new Error(outreachErrorMessage(data, "生成预览失败"));
        if (selectedPlanId) await loadPlan(selectedPlanId);
        await loadEvents();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadEvents, loadPlan, selectedPlanId]
  );

  const refreshConversation = useCallback(
    async (candidate: Candidate) => {
      setBusy("refresh-conversation");
      setError("");
      setNotice("");
      try {
        const response = await fetch(`/api/outreach/customers/${encodeURIComponent(candidate.customer_id)}/refresh-conversation`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            corp_id: candidate.corp_id || "",
            user_id: candidate.user_id || "",
            wechat: candidate.wechat || "",
            external_userid: candidate.external_userid || candidate.customer_id,
          }),
        });
        const data = await readJsonResponse(response);
        if (!response.ok) throw new Error(outreachErrorMessage(data, "刷新历史失败"));
        if (data.warning) setNotice(String(data.warning));
        await loadCandidates();
        await loadEvents();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadCandidates, loadEvents]
  );

  const openConversationHistory = useCallback(
    async (candidate: Candidate) => {
      setHistoryOpen(true);
      setHistoryCustomer(candidate);
      setHistoryMessages([]);
      setBusy(`history-${candidate.customer_id}`);
      setError("");
      setNotice("");
      try {
        const response = await fetch(`/api/outreach/customers/${encodeURIComponent(candidate.customer_id)}/refresh-conversation`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            corp_id: candidate.corp_id || "",
            user_id: candidate.user_id || "",
            wechat: candidate.wechat || "",
            external_userid: candidate.external_userid || candidate.customer_id,
            limit: 30,
          }),
        });
        const data = await readJsonResponse(response);
        if (!response.ok) throw new Error(outreachErrorMessage(data, "加载历史聊天失败"));
        setHistoryMessages(Array.isArray(data.messages) ? data.messages : []);
        if (data.warning) setNotice(String(data.warning));
        await loadCandidates();
        await loadEvents();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadCandidates, loadEvents]
  );

  const runDue = useCallback(async () => {
    setBusy("run-due");
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/outreach/run-due?limit=20", { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "执行到期任务失败");
      if (selectedPlanId) await loadPlan(selectedPlanId);
      await loadEvents();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }, [loadEvents, loadPlan, selectedPlanId]);

  useEffect(() => {
    loadCandidates();
    loadEvents();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (selectedCustomer?.outreach_plan_id) loadPlan(selectedCustomer.outreach_plan_id);
  }, [loadPlan, selectedCustomer?.outreach_plan_id]);

  return (
    <main className="h-screen overflow-hidden bg-[#f7f8fb] text-[#171717]">
      <header className="flex h-14 items-center justify-between border-b border-zinc-200 bg-white px-5">
        <div className="flex items-center gap-3">
          <Link href="/" className="rounded-md border border-zinc-200 p-2 text-zinc-600 hover:bg-zinc-50" title="返回对话">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <h1 className="text-base font-semibold">主动唤醒</h1>
            <p className="text-xs text-zinc-500">筛选沉默客户，生成计划，到点前复查并触达</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {notice ? <span className="max-w-xl truncate text-xs text-amber-600">{notice}</span> : null}
          {error ? <span className="max-w-xl truncate text-xs text-red-600">{error}</span> : null}
          <button onClick={runDue} className="inline-flex items-center gap-2 rounded-md bg-zinc-900 px-3 py-2 text-sm text-white hover:bg-zinc-800">
            <Activity className="h-4 w-4" />
            执行到期任务
          </button>
          <button onClick={loadCandidates} className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm hover:bg-zinc-50">
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            刷新
          </button>
        </div>
      </header>

      <section className="grid h-[calc(100vh-56px)] grid-cols-[340px_minmax(520px,1fr)_360px]">
        <aside className="flex min-h-0 flex-col border-r border-zinc-200 bg-white">
          <div className="border-b border-zinc-200 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium">
              <Search className="h-4 w-4" />
              客户筛选
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="text-xs text-zinc-500">
                沉默超过
                <select
                  className="mt-1 w-full rounded-md border border-zinc-200 bg-white px-2 py-2 text-sm text-zinc-900"
                  value={filters.silentMinutesMin}
                  onChange={(event) => setFilters((prev) => ({ ...prev, silentMinutesMin: event.target.value }))}
                >
                  <option value="60">1小时</option>
                  <option value="180">3小时</option>
                  <option value="720">12小时</option>
                  <option value="1440">24小时</option>
                </select>
              </label>
              <label className="text-xs text-zinc-500">
                计划状态
                <select
                  className="mt-1 w-full rounded-md border border-zinc-200 bg-white px-2 py-2 text-sm text-zinc-900"
                  value={filters.outreachStatus}
                  onChange={(event) => setFilters((prev) => ({ ...prev, outreachStatus: event.target.value }))}
                >
                  <option value="">全部</option>
                  <option value="none">无计划</option>
                  <option value="draft">草稿</option>
                  <option value="active">执行中</option>
                  <option value="waiting">等待</option>
                  <option value="paused">暂停</option>
                </select>
              </label>
              <label className="col-span-2 text-xs text-zinc-500">
                客户阶段
                <input
                  className="mt-1 w-full rounded-md border border-zinc-200 px-2 py-2 text-sm"
                  placeholder="例如 已问价 / 已看案例 / 已给门店"
                  value={filters.lifecycleStage}
                  onChange={(event) => setFilters((prev) => ({ ...prev, lifecycleStage: event.target.value }))}
                />
              </label>
              <label className="flex items-center gap-2 text-xs text-zinc-600">
                <input
                  type="checkbox"
                  checked={filters.noPlanOnly}
                  onChange={(event) => setFilters((prev) => ({ ...prev, noPlanOnly: event.target.checked }))}
                />
                只看无计划客户
              </label>
              <label className="text-xs text-zinc-500">
                数量
                <input
                  className="mt-1 w-full rounded-md border border-zinc-200 px-2 py-2 text-sm"
                  value={filters.limit}
                  onChange={(event) => setFilters((prev) => ({ ...prev, limit: event.target.value }))}
                />
              </label>
            </div>
            <button onClick={loadCandidates} className="mt-3 w-full rounded-md bg-zinc-900 px-3 py-2 text-sm text-white hover:bg-zinc-800">
              筛选客户
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {candidates.length === 0 ? (
              <div className="rounded-lg border border-dashed border-zinc-200 p-6 text-center text-sm text-zinc-500">暂无候选客户</div>
            ) : (
              <div className="space-y-2">
                {candidates.map((item) => {
                  const active = selectedCustomer?.customer_id === item.customer_id;
                  return (
                    <div
                      key={item.customer_id}
                      onClick={() => setSelectedCustomer(item)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" || event.key === " ") setSelectedCustomer(item);
                      }}
                      role="button"
                      tabIndex={0}
                      className={`w-full rounded-lg border p-3 text-left transition ${
                        active ? "border-zinc-900 bg-zinc-50" : "border-zinc-200 bg-white hover:bg-zinc-50"
                      }`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium">{item.title || item.customer_id}</div>
                          <div className="truncate text-xs text-zinc-500">ID {item.customer_id}</div>
                        </div>
                        <span className="rounded-full bg-zinc-100 px-2 py-1 text-xs text-zinc-600">{statusLabel(item.outreach_status)}</span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-2 text-xs text-zinc-500">
                        <span>沉默 {formatSilent(item.silent_minutes)}</span>
                        <span>{formatTime(item.last_customer_message_at)}</span>
                      </div>
                      <div className="mt-2 flex items-end gap-2">
                        <p className="min-w-0 flex-1 line-clamp-2 text-xs text-zinc-600">{item.last_customer_message || item.latest_event_summary || "暂无最近消息摘要"}</p>
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            openConversationHistory(item);
                          }}
                          className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-zinc-200 bg-white text-zinc-600 hover:bg-zinc-100"
                          title="查看历史聊天记录"
                          disabled={busy === `history-${item.customer_id}`}
                        >
                          <MessageSquareText className="h-4 w-4" />
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </aside>

        <section className="min-h-0 overflow-y-auto p-5">
          <div className="mb-4 grid grid-cols-3 gap-3">
            <MetricCard icon={<UserRound className="h-4 w-4" />} label="候选客户" value={String(candidates.length)} />
            <MetricCard icon={<Clock className="h-4 w-4" />} label="当前客户沉默" value={formatSilent(selectedCustomer?.silent_minutes)} />
            <MetricCard icon={<ListChecks className="h-4 w-4" />} label="计划状态" value={statusLabel(selectedPlan?.status || selectedCustomer?.outreach_status)} />
          </div>

          <div className="rounded-xl border border-zinc-200 bg-white">
            <div className="flex items-center justify-between border-b border-zinc-200 p-4">
              <div>
                <h2 className="text-base font-semibold">唤醒计划详情</h2>
                <p className="text-sm text-zinc-500">{selectedCustomer ? `${selectedCustomer.customer_id} · ${selectedCustomer.lifecycle_stage || "未分阶段"}` : "请选择客户"}</p>
              </div>
              <div className="flex items-center gap-2">
                {selectedCustomer ? (
                  <>
                    <button
                      onClick={() => refreshConversation(selectedCustomer)}
                      className="rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50"
                    >
                      刷新对话
                    </button>
                    <button
                      onClick={() => generatePlan(selectedCustomer)}
                      className="rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50"
                    >
                      生成计划
                    </button>
                    <button
                      onClick={() => generatePlan(selectedCustomer, true)}
                      className="rounded-md bg-zinc-900 px-3 py-2 text-sm text-white hover:bg-zinc-800"
                    >
                      生成并启用
                    </button>
                  </>
                ) : null}
              </div>
            </div>

            {selectedPlan ? (
              <div className="p-4">
                <div className="grid grid-cols-3 gap-3">
                  <InfoBlock label="沉默原因" value={selectedPlan.stall_reason || "-"} />
                  <InfoBlock label="客户心理" value={selectedPlan.customer_psychology || "-"} />
                  <InfoBlock label="计划目标" value={selectedPlan.plan_goal || "-"} />
                </div>
                <div className="mt-4 flex items-center gap-2">
                  <button onClick={() => planAction("activate")} className="inline-flex items-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50">
                    <Play className="h-4 w-4" />
                    启用
                  </button>
                  <button onClick={() => planAction("pause")} className="inline-flex items-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50">
                    <Pause className="h-4 w-4" />
                    暂停
                  </button>
                  <button onClick={() => planAction("resume")} className="inline-flex items-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50">
                    <RefreshCw className="h-4 w-4" />
                    恢复
                  </button>
                  <button onClick={() => planAction("cancel")} className="inline-flex items-center gap-2 rounded-md border border-red-200 px-3 py-2 text-sm text-red-600 hover:bg-red-50">
                    <XCircle className="h-4 w-4" />
                    取消
                  </button>
                </div>

                <div className="mt-5 space-y-3">
                  <h3 className="text-sm font-semibold">计划步骤</h3>
                  {tasks.length === 0 ? (
                    <div className="rounded-lg border border-dashed border-zinc-200 p-6 text-sm text-zinc-500">暂无任务步骤</div>
                  ) : (
                    tasks.map((task) => (
                      <div key={task.id} className="rounded-lg border border-zinc-200 p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="rounded-full bg-zinc-900 px-2 py-1 text-xs text-white">第 {task.step_index} 步</span>
                              <span className="text-sm font-medium">{task.intent || "outreach"}</span>
                              <span className="rounded-full bg-zinc-100 px-2 py-1 text-xs text-zinc-600">{statusLabel(task.status)}</span>
                            </div>
                            <p className="mt-2 text-sm text-zinc-700">{task.message_goal || "-"}</p>
                            <p className="mt-1 text-xs text-zinc-500">计划发送：{formatTime(task.scheduled_at)} · 发送结果：{sendStatusLabel(task.send_status)}</p>
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => previewTask(task.id)}
                              className="inline-flex items-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50"
                              disabled={busy === `preview-${task.id}`}
                            >
                              <FileText className="h-4 w-4" />
                              生成预览
                            </button>
                            <button
                              onClick={() => executeTask(task.id)}
                              className="inline-flex items-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50 disabled:cursor-not-allowed disabled:bg-zinc-50 disabled:text-zinc-400"
                              disabled={busy === `task-${task.id}` || !taskHasPreview(task)}
                              title={taskHasPreview(task) ? "发送前会复查客户是否已回复" : "请先生成预览，人工确认后再执行"}
                            >
                              <Send className="h-4 w-4" />
                              立即执行
                            </button>
                          </div>
                        </div>
                        <div className="mt-3 rounded-md bg-zinc-50 p-3 text-sm text-zinc-700">{messagePreview(task.reply_messages)}</div>
                        {!taskHasPreview(task) ? <p className="mt-2 text-xs text-amber-600">请先生成预览，人工确认后再执行。</p> : null}
                        {task.error_message ? <p className="mt-2 text-xs text-red-600">{task.error_message}</p> : null}
                      </div>
                    ))
                  )}
                </div>
              </div>
            ) : (
              <div className="p-12 text-center text-sm text-zinc-500">
                {selectedCustomer ? "该客户暂无计划，点击生成计划开始。" : "左侧选择一个客户后查看或生成计划。"}
              </div>
            )}
          </div>
        </section>

        <aside className="min-h-0 overflow-y-auto border-l border-zinc-200 bg-white p-4">
          <section className="rounded-xl border border-zinc-200 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <FileText className="h-4 w-4" />
              客户画像与最近记录
            </div>
            {selectedCustomer ? (
              <div className="space-y-3 text-sm">
                <InfoLine label="客户" value={selectedCustomer.title || selectedCustomer.customer_id} />
                <InfoLine label="员工" value={selectedCustomer.wechat || selectedCustomer.user_id || "-"} />
                <InfoLine label="最近客户消息" value={formatTime(selectedCustomer.last_customer_message_at)} />
                <InfoLine label="最近触达" value={formatTime(selectedCustomer.last_outreach_at)} />
                <div>
                  <div className="mb-1 text-xs text-zinc-500">画像摘要</div>
                  <pre className="max-h-40 overflow-auto rounded-md bg-zinc-50 p-3 text-xs leading-relaxed text-zinc-700">
                    {jsonPreview(selectedCustomer.portrait || selectedCustomer.basic_info)}
                  </pre>
                </div>
              </div>
            ) : (
              <p className="text-sm text-zinc-500">暂无选中客户</p>
            )}
          </section>

          <section className="mt-4 rounded-xl border border-zinc-200 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <Activity className="h-4 w-4" />
              执行日志
            </div>
            <div className="space-y-3">
              {(planEvents.length ? planEvents : events).slice(0, 30).map((event) => (
                <div key={event.id} className="rounded-lg border border-zinc-100 bg-zinc-50 p-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-medium text-zinc-800">{event.event_type}</span>
                    <span className="text-xs text-zinc-500">{formatTime(event.created_at)}</span>
                  </div>
                  <p className="mt-1 text-xs text-zinc-600">{event.event_summary || "-"}</p>
                </div>
              ))}
              {(planEvents.length ? planEvents : events).length === 0 ? <p className="text-sm text-zinc-500">暂无日志</p> : null}
            </div>
          </section>
        </aside>
      </section>
      {historyOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
          <section className="flex max-h-[82vh] w-full max-w-2xl flex-col rounded-xl border border-zinc-200 bg-white shadow-xl">
            <div className="flex items-start justify-between gap-3 border-b border-zinc-200 p-4">
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold">历史聊天记录</h2>
                <p className="truncate text-xs text-zinc-500">{historyCustomer ? `${historyCustomer.title || historyCustomer.customer_id} · ID ${historyCustomer.customer_id}` : ""}</p>
              </div>
              <button
                type="button"
                onClick={() => setHistoryOpen(false)}
                className="rounded-md border border-zinc-200 p-2 text-zinc-500 hover:bg-zinc-50"
                title="关闭"
              >
                <XCircle className="h-4 w-4" />
              </button>
            </div>
            <div className="min-h-[260px] flex-1 overflow-y-auto p-4">
              {busy.startsWith("history-") ? (
                <div className="py-10 text-center text-sm text-zinc-500">正在加载聊天记录...</div>
              ) : historyMessages.length ? (
                <div className="space-y-3">
                  {historyMessages.map((message, index) => {
                    const sender = messageSender(message);
                    const fromCustomer = sender === "客户";
                    return (
                      <div key={`${String(message.msgtime || message.created_at || index)}-${index}`} className={`flex ${fromCustomer ? "justify-start" : "justify-end"}`}>
                        <div className={`max-w-[78%] rounded-lg px-3 py-2 ${fromCustomer ? "bg-zinc-100 text-zinc-900" : "bg-zinc-900 text-white"}`}>
                          <div className={`mb-1 flex items-center gap-2 text-[11px] ${fromCustomer ? "text-zinc-500" : "text-zinc-300"}`}>
                            <span>{sender}</span>
                            <span>{messageTime(message)}</span>
                          </div>
                          <div className="whitespace-pre-wrap break-words text-sm leading-relaxed">{messageText(message) || "[非文本消息]"}</div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <div className="py-10 text-center text-sm text-zinc-500">暂无可展示的历史聊天记录</div>
              )}
            </div>
          </section>
        </div>
      ) : null}
    </main>
  );
}

function MetricCard({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="rounded-xl border border-zinc-200 bg-white p-4">
      <div className="flex items-center gap-2 text-xs text-zinc-500">
        {icon}
        {label}
      </div>
      <div className="mt-2 truncate text-lg font-semibold">{value}</div>
    </div>
  );
}

function InfoBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-zinc-50 p-3">
      <div className="text-xs text-zinc-500">{label}</div>
      <div className="mt-1 line-clamp-3 text-sm text-zinc-800">{value}</div>
    </div>
  );
}

function InfoLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start justify-between gap-3 border-b border-zinc-100 pb-2">
      <span className="text-xs text-zinc-500">{label}</span>
      <span className="max-w-[210px] text-right text-sm text-zinc-800">{value}</span>
    </div>
  );
}
