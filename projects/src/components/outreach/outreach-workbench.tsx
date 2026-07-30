"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity,
  ArrowLeft,
  Bot,
  CircleCheck,
  CircleX,
  Clock,
  GitBranch,
  History,
  Images,
  ListChecks,
  LoaderCircle,
  MapPin,
  MessageSquareText,
  Pause,
  Phone,
  Play,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Tags,
  TimerReset,
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
  platform_customer_name?: string;
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
  external_userid?: string;
  corp_id?: string;
  wechat?: string;
  status: string;
  customer_stage?: string;
  conversion_stage?: string;
  customer_type?: string;
  stall_reason?: string;
  last_explicit_intent?: string;
  last_interaction_summary?: string;
  next_best_action?: string;
  suppress_reason?: string;
  customer_psychology?: string;
  plan_goal?: string;
  core_barrier?: string;
  emotional_need?: string;
  plan_arc?: string;
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
  content_sources?: Array<unknown>;
  content_source_metadata?: Array<JsonObject>;
  should_send_payment_collection?: boolean;
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

type CustomerHistoryEvent = {
  event_id?: string;
  event_type?: string;
  stage?: string;
  summary?: string;
  facts?: JsonObject;
  impact?: string;
  confidence?: number;
  event_time?: string;
};

type CustomerDetail = {
  customer_id?: string;
  external_userid?: string;
  corp_id?: string;
  wechat?: string;
  portrait?: JsonObject;
  basic_info?: JsonObject;
  lifecycle_stage?: string;
  profile_updated_at?: string;
  history_events?: CustomerHistoryEvent[];
  outreach_events?: OutreachEvent[];
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
  keyword: string;
  silentMinutesMin: string;
  lifecycleStage: string;
  outreachStatus: string;
  noPlanOnly: boolean;
  limit: string;
};

type DashboardStats = {
  generated_at?: string;
  timezone?: string;
  worker?: {
    enabled?: boolean;
    mode?: string;
    poll_seconds?: number;
    batch_size?: number;
    before_send_retry_seconds?: number;
  };
  plan_monitor?: {
    enabled?: boolean;
    poll_seconds?: number;
    silent_minutes?: number;
    batch_size?: number;
    auto_activate?: boolean;
    last_scan_started_at?: string;
    last_scan_finished_at?: string;
    candidate_count?: number;
    evaluated_count?: number;
    created_count?: number;
    rejected_count?: number;
    skipped_count?: number;
    error_count?: number;
    last_error?: string;
  };
  metrics?: {
    platform_tasks_today?: number;
    personalized_plans_today?: number;
    active_plans?: number;
    pending_tasks?: number;
    due_tasks?: number;
    sent_today?: number;
    stopped_today?: number;
    retry_today?: number;
    failed_today?: number;
  };
  next_due?: {
    scheduled_at?: string;
    customer_id?: string;
    task_id?: string;
  };
  last_sent?: {
    sent_at?: string;
    customer_id?: string;
    task_id?: string;
  };
  outcomes?: {
    window_days?: number;
    sent_tasks?: number;
    contact_count?: number;
    reopened_24h_rate?: number;
    reopened_72h_rate?: number;
    ai_resumed_72h_rate?: number;
    deposit_7d_rate?: number;
    average_touches_per_customer?: number;
    value_only_rate?: number;
    repeated_angle_rate?: number;
    asset_send_count?: number;
    asset_repeat_rate?: number;
    asset_reply_72h_rate?: number;
    safety_stop_count?: number;
    failure_count?: number;
    complaint_rate?: number | null;
  };
};

const DEFAULT_FILTERS: Filters = {
  keyword: "",
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

function candidateKey(candidate?: Candidate | null) {
  if (!candidate) return "";
  return [candidate.corp_id, candidate.wechat, candidate.external_userid, candidate.customer_id]
    .map((value) => String(value || "").toLowerCase())
    .join(":");
}

function planMatchesCandidate(plan: OutreachPlan, candidate: Candidate) {
  if (String(plan.customer_id || "") !== String(candidate.customer_id || "")) return false;
  const identityFields: Array<keyof Pick<OutreachPlan, "corp_id" | "wechat" | "external_userid">> = [
    "corp_id",
    "wechat",
    "external_userid",
  ];
  return identityFields.every((field) => {
    const planValue = String(plan[field] || "").trim().toLowerCase();
    const candidateValue = String(candidate[field] || "").trim().toLowerCase();
    return !planValue || !candidateValue || planValue === candidateValue;
  });
}

function objectValue(value: unknown): JsonObject {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as JsonObject) : {};
}

function textValue(value: unknown): string {
  if (value == null || value === "") return "";
  if (Array.isArray(value)) return value.map(textValue).filter(Boolean).join("、");
  if (typeof value === "object") {
    return Object.entries(value as JsonObject)
      .map(([key, item]) => `${fieldLabel(key)}：${textValue(item)}`)
      .filter((item) => !item.endsWith("："))
      .join("；");
  }
  return String(value);
}

function listValue(value: unknown) {
  if (Array.isArray(value)) return value.map(textValue).filter(Boolean);
  const text = textValue(value);
  return text ? [text] : [];
}

function candidateName(candidate?: Candidate | null) {
  if (!candidate) return "-";
  const basic = objectValue(candidate.basic_info);
  const title = String(candidate.title || "").trim();
  const safeTitle =
    title.length <= 24 &&
    !/[\r\n。！？!?]/.test(title) &&
    !title.includes("我已经添加了你") &&
    !title.includes("现在我们可以开始聊天了")
      ? title
      : "";
  return textValue(basic.customer_name) || candidate.platform_customer_name || safeTitle || candidate.customer_id;
}

function fieldLabel(key: string) {
  const labels: Record<string, string> = {
    summary: "画像摘要",
    customer_type_tags: "客户类型",
    decision_stage: "决策阶段",
    deposit_state: "预约金状态",
    main_objection: "主要顾虑",
    next_sales_strategy: "下一步策略",
    intent_level: "意向程度",
    trust_level: "信任程度",
    concerns: "客户顾虑",
    style_tags: "沟通偏好",
    city: "城市",
    area_or_landmark: "区域/地标",
    preferred_store_id: "意向门店 ID",
    preferred_store_name: "意向门店",
    intent_date: "意向日期",
    intent_time: "意向时间",
    customer_name: "登记姓名",
    phone: "联系电话",
    order_id: "订单 ID",
    store_id: "门店 ID",
    store_name: "门店",
    amount: "金额",
    source: "来源",
    status: "状态",
    reason: "原因",
  };
  return labels[key] || key.replaceAll("_", " ");
}

function levelLabel(value: unknown) {
  const labels: Record<string, string> = {
    low: "低",
    medium: "中",
    high: "高",
    unknown: "未知",
    none: "未进入",
    unpaid: "未支付",
    paid: "已支付",
  };
  const text = textValue(value);
  return labels[text] || text || "-";
}

function eventTypeLabel(value?: string) {
  const labels: Record<string, string> = {
    customer_psychology_update: "客户心理变化",
    customer_need_update: "客户需求更新",
    store_preference_update: "门店偏好更新",
    payment_success: "预约金已支付",
    offer_explained: "活动报价已介绍",
    deposit_explained: "预约金规则已介绍",
    plan_created: "生成唤醒计划",
    plan_activated: "启用唤醒计划",
    task_sent: "触达已发送",
    task_failed: "触达失败",
    task_skipped_customer_replied: "客户回复，停止触达",
    task_skipped_order_state_changed: "订单变化，停止触达",
    plan_cancelled_customer_replied: "客户回复，取消剩余计划",
    task_deferred_daily_limit: "达到当日上限，顺延发送",
    before_send_check_failed: "发送前复查失败",
  };
  return labels[String(value || "")] || String(value || "历史事件");
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
  if (data.reason === "customer_deleted" || data.error === "customer_deleted") {
    return "该客户已删除企业微信，系统已停止生成计划和后续触达。";
  }
  if (
    data.reason === "customer_relation_unavailable" ||
    data.reason === "customer_relation_check_failed"
  ) {
    return "暂时无法确认客户关系状态，系统已停止生成计划，请稍后重试。";
  }
  if (data.error === "conversation_refresh_failed") {
    return "历史聊天查询超时，请稍后重试或降低条数";
  }
  if (data.error === "outreach_plan_generation_failed") {
    return "生成计划失败，请稍后重试";
  }
  if (data.error === "preview_required") {
    return "该旧任务缺少可执行消息，请重新生成计划";
  }
  if (data.status === "check_failed") {
    return "发送前复查历史失败，已阻止发送。请刷新历史后重试";
  }
  return String(data.detail || data.error || fallback);
}

function taskHasMessages(task: OutreachTask) {
  return Array.isArray(task.reply_messages) && task.reply_messages.length > 0;
}

function taskPlanningMetadata(task: OutreachTask) {
  const items = Array.isArray(task.content_source_metadata) ? task.content_source_metadata : [];
  const holder = items.find((item) => item && typeof item.outreach_task_metadata === "object");
  return (holder?.outreach_task_metadata || {}) as JsonObject;
}

function taskResolvedAsset(task: OutreachTask) {
  const items = Array.isArray(task.content_source_metadata) ? task.content_source_metadata : [];
  const holder = items.find((item) => item && typeof item.resolved_asset === "object");
  return (holder?.resolved_asset || {}) as JsonObject;
}

function sendStatusLabel(value?: string) {
  if (!value) return "-";
  if (value === "accepted_no_response") return "平台已接收请求/待回查";
  if (value === "accepted") return "平台请求已发出/待回查";
  return value;
}

function boolLabel(value?: boolean) {
  return value ? "允许收款卡" : "仅文本推进";
}

function rateLabel(value?: number | null) {
  if (value === null || value === undefined) return "--";
  return `${Math.round(value * 1000) / 10}%`;
}

function outreachMessageTypeLabel(type: unknown) {
  const labels: Record<string, string> = {
    text: "文字",
    image: "图片",
    video: "视频",
    payment_collection: "预约金卡",
    store_address: "门店卡",
    human_handoff_notice: "人工协助",
  };
  const key = String(type || "unknown");
  return labels[key] || key;
}

function outreachMessageContent(message: JsonObject) {
  const content = message.content;
  return content && typeof content === "object" ? (content as JsonObject) : {};
}

function OutreachMessageList({ messages }: { messages?: Array<JsonObject> }) {
  if (!messages?.length) {
    return <p className="text-sm text-amber-700">该旧任务没有结构化消息，请重新生成计划后再执行。</p>;
  }
  return (
    <div className="divide-y divide-zinc-200 border-y border-zinc-200">
      {messages
        .slice()
        .sort((left, right) => Number(left.order || 0) - Number(right.order || 0))
        .map((message, index) => {
          const type = String(message.type || "unknown");
          const content = outreachMessageContent(message);
          const url = String(content.url || "");
          return (
            <div key={`${type}-${String(message.order || index + 1)}-${index}`} className="grid gap-2 py-3 sm:grid-cols-[88px_minmax(0,1fr)]">
              <div className="flex items-start gap-2 text-xs text-zinc-500">
                <span className="font-medium text-zinc-900">#{String(message.order || index + 1)}</span>
                <span>{outreachMessageTypeLabel(type)}</span>
              </div>
              <div className="min-w-0 text-sm text-zinc-800">
                {type === "text" ? <p className="whitespace-pre-wrap break-words">{String(content.text || "")}</p> : null}
                {type === "image" && url ? (
                  <div className="space-y-2">
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={url} alt="计划发送图片" loading="lazy" className="max-h-56 max-w-full rounded-md border border-zinc-200 object-contain" />
                    <a href={url} target="_blank" rel="noreferrer" className="block break-all text-xs text-blue-600 hover:underline">
                      {url}
                    </a>
                  </div>
                ) : null}
                {type === "video" && url ? (
                  <div className="space-y-2">
                    <video src={url} controls preload="none" className="max-h-64 max-w-full rounded-md border border-zinc-200" />
                    <a href={url} target="_blank" rel="noreferrer" className="block break-all text-xs text-blue-600 hover:underline">
                      {url}
                    </a>
                  </div>
                ) : null}
                {type === "payment_collection" ? (
                  <p>金额：{String(content.amount || 10)} 元{content.remark ? ` · 备注：${String(content.remark)}` : ""}</p>
                ) : null}
                {type === "store_address" ? (
                  <p>
                    门店 ID：{String(content.store_id || "-")}
                    {content.store_name ? ` · ${String(content.store_name)}` : ""}
                  </p>
                ) : null}
                {!["text", "image", "video", "payment_collection", "store_address"].includes(type) ? (
                  <pre className="overflow-x-auto whitespace-pre-wrap break-words text-xs">{JSON.stringify(content, null, 2)}</pre>
                ) : null}
              </div>
            </div>
          );
        })}
    </div>
  );
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
  const [planLoading, setPlanLoading] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyCustomer, setHistoryCustomer] = useState<Candidate | null>(null);
  const [historyMessages, setHistoryMessages] = useState<ConversationMessage[]>([]);
  const [dashboard, setDashboard] = useState<DashboardStats>({});
  const [customerDetail, setCustomerDetail] = useState<CustomerDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailRevision, setDetailRevision] = useState(0);
  const planLoadSequence = useRef(0);

  const selectedPlan = planDetail?.plan || null;
  const tasks = useMemo(() => planDetail?.tasks || [], [planDetail]);
  const planEvents = useMemo(() => planDetail?.events || [], [planDetail]);

  const loadDashboard = useCallback(async () => {
    try {
      const response = await fetch("/api/outreach/dashboard", { cache: "no-store" });
      const data = await readJsonResponse(response);
      if (!response.ok) throw new Error(outreachErrorMessage(data, "加载主动唤醒统计失败"));
      setDashboard(data as DashboardStats);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadCandidates = useCallback(async () => {
    setLoading(true);
    setError("");
    const search = new URLSearchParams();
    search.set("silent_minutes_min", filters.silentMinutesMin || "0");
    search.set("limit", filters.limit || "50");
    if (filters.keyword) search.set("keyword", filters.keyword);
    if (filters.lifecycleStage) search.set("lifecycle_stage", filters.lifecycleStage);
    if (filters.outreachStatus) search.set("outreach_status", filters.outreachStatus);
    if (filters.noPlanOnly) search.set("no_plan_only", "true");
    try {
      const response = await fetch(`/api/outreach/candidates?${search.toString()}`, { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "加载候选客户失败");
      const items = (Array.isArray(data.items) ? data.items : []) as Candidate[];
      setCandidates(items);
      setSelectedCustomer((current) => {
        if (!items.length) return null;
        if (!current) return items[0];
        return items.find((item) => candidateKey(item) === candidateKey(current)) || items[0];
      });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, [filters]);

  const refreshCustomerDetail = useCallback(async () => {
    setDetailRevision((value) => value + 1);
  }, []);


  const loadPlan = useCallback(async (planId: string, expectedCustomer?: Candidate | null) => {
    if (!planId) return;
    const requestSequence = ++planLoadSequence.current;
    setPlanLoading(true);
    setPlanDetail(null);
    setSelectedPlanId(planId);
    setError("");
    try {
      const response = await fetch(`/api/outreach/plans/${encodeURIComponent(planId)}`, { cache: "no-store" });
      const data = (await response.json()) as PlanDetail & { error?: string };
      if (!response.ok) throw new Error(data?.error || "加载计划失败");
      if (!data.plan || String(data.plan.id || "") !== planId) {
        throw new Error("计划详情与请求不一致，请刷新后重试");
      }
      if (expectedCustomer && !planMatchesCandidate(data.plan, expectedCustomer)) {
        throw new Error("计划不属于当前客户，已停止展示旧计划");
      }
      if (requestSequence !== planLoadSequence.current) return;
      setPlanDetail(data);
    } catch (err) {
      if (requestSequence !== planLoadSequence.current) return;
      setPlanDetail(null);
      setSelectedPlanId("");
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      if (requestSequence === planLoadSequence.current) setPlanLoading(false);
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
            limit: 50,
          }),
        });
        const refreshData = await readJsonResponse(refreshResponse);
        if (!refreshResponse.ok) throw new Error(outreachErrorMessage(refreshData, "生成计划前刷新历史失败"));
        const customerRelation =
          refreshData.customer_relation && typeof refreshData.customer_relation === "object"
            ? (refreshData.customer_relation as JsonObject)
            : {};
        if (
          customerRelation.is_deleted === true ||
          ["deleted", "removed"].includes(String(customerRelation.status || "").toLowerCase())
        ) {
          throw new Error("该客户已删除企业微信，系统已停止生成计划和后续触达。");
        }
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
        if (data.skipped === true) {
          throw new Error(outreachErrorMessage(data, "当前客户不适合生成计划"));
        }
        const planId = data?.plan?.id || data?.id;
        if (planId) {
          if (activate) {
            await fetch(`/api/outreach/plans/${encodeURIComponent(planId)}/activate`, { method: "POST" });
          }
          await loadPlan(planId, candidate);
          await loadCandidates();
          await refreshCustomerDetail();
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadCandidates, loadPlan, refreshCustomerDetail]
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
        await refreshCustomerDetail();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadCandidates, refreshCustomerDetail, selectedPlan]
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
          if (selectedPlanId) await loadPlan(selectedPlanId, selectedCustomer);
          await refreshCustomerDetail();
          throw new Error(outreachErrorMessage(data, "执行任务失败"));
        }
        if (selectedPlanId) await loadPlan(selectedPlanId, selectedCustomer);
        await refreshCustomerDetail();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadPlan, refreshCustomerDetail, selectedCustomer, selectedPlanId]
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
            limit: 50,
          }),
        });
        const data = await readJsonResponse(response);
        if (!response.ok) throw new Error(outreachErrorMessage(data, "刷新历史失败"));
        if (data.warning) setNotice(String(data.warning));
        await loadCandidates();
        await refreshCustomerDetail();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadCandidates, refreshCustomerDetail]
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
            limit: 50,
          }),
        });
        const data = await readJsonResponse(response);
        if (!response.ok) throw new Error(outreachErrorMessage(data, "加载历史聊天失败"));
        setHistoryMessages(Array.isArray(data.messages) ? data.messages : []);
        if (data.warning) setNotice(String(data.warning));
        await loadCandidates();
        await refreshCustomerDetail();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setBusy("");
      }
    },
    [loadCandidates, refreshCustomerDetail]
  );

  const runDue = useCallback(async () => {
    setBusy("run-due");
    setError("");
    setNotice("");
    try {
      const response = await fetch("/api/outreach/run-due?limit=20", { method: "POST" });
      const data = await response.json();
      if (!response.ok) throw new Error(data?.error || "执行到期任务失败");
      if (selectedPlanId) await loadPlan(selectedPlanId, selectedCustomer);
      await Promise.all([refreshCustomerDetail(), loadDashboard()]);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  }, [loadDashboard, loadPlan, refreshCustomerDetail, selectedCustomer, selectedPlanId]);

  useEffect(() => {
    loadCandidates();
    loadDashboard();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    ++planLoadSequence.current;
    setSelectedPlanId("");
    setPlanDetail(null);
    setPlanLoading(false);
    if (selectedCustomer?.outreach_plan_id) {
      void loadPlan(selectedCustomer.outreach_plan_id, selectedCustomer);
    }
  }, [loadPlan, selectedCustomer, selectedCustomer?.outreach_plan_id]);

  useEffect(() => {
    if (!selectedCustomer) {
      setCustomerDetail(null);
      return;
    }
    const controller = new AbortController();
    const search = new URLSearchParams({
      corp_id: selectedCustomer.corp_id || "",
      wechat: selectedCustomer.wechat || "",
      external_userid: selectedCustomer.external_userid || selectedCustomer.customer_id,
    });
    setDetailLoading(true);
    setCustomerDetail(null);
    fetch(
      `/api/outreach/customers/${encodeURIComponent(selectedCustomer.customer_id)}/detail?${search.toString()}`,
      { cache: "no-store", signal: controller.signal }
    )
      .then(async (response) => {
        const data = await readJsonResponse(response);
        if (!response.ok) throw new Error(outreachErrorMessage(data, "加载客户画像失败"));
        return data as CustomerDetail;
      })
      .then((data) => setCustomerDetail(data))
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
    return () => controller.abort();
  }, [detailRevision, selectedCustomer]);

  return (
    <main className="min-h-screen bg-[#f7f8fb] text-[#171717]">
      <header className="sticky top-0 z-30 flex min-h-14 flex-wrap items-center justify-between gap-2 border-b border-zinc-200 bg-white px-3 py-2 sm:px-5">
        <div className="flex items-center gap-3">
          <Link href="/" className="rounded-md border border-zinc-200 p-2 text-zinc-600 hover:bg-zinc-50" title="返回对话">
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <div>
            <h1 className="text-base font-semibold">个性化主动唤醒</h1>
            <p className="text-xs text-zinc-500">监控客户状态，自动生成计划，到点复查后触达</p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {notice ? <span className="hidden max-w-xl truncate text-xs text-amber-600 lg:inline">{notice}</span> : null}
          {error ? <span className="hidden max-w-xl truncate text-xs text-red-600 lg:inline">{error}</span> : null}
          <Link
            href="/outreach/assets"
            className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm hover:bg-zinc-50"
          >
            <Images className="h-4 w-4" />
            素材库
          </Link>
          <button onClick={runDue} className="inline-flex items-center gap-2 rounded-md bg-zinc-900 px-3 py-2 text-sm text-white hover:bg-zinc-800">
            <Activity className="h-4 w-4" />
            执行到期任务
          </button>
          <button
            onClick={() => {
              loadCandidates();
              refreshCustomerDetail();
              loadDashboard();
            }}
            className="inline-flex items-center gap-2 rounded-md border border-zinc-200 bg-white px-3 py-2 text-sm hover:bg-zinc-50"
          >
            <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
            刷新
          </button>
        </div>
      </header>
      {error ? (
        <div className="mx-3 mt-3 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 sm:mx-5" role="alert">
          {error}
        </div>
      ) : null}
      {!error && notice ? (
        <div className="mx-3 mt-3 rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 sm:mx-5" role="status">
          {notice}
        </div>
      ) : null}

      <section className="border-b border-zinc-200 bg-white px-5 py-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-sm font-semibold">自动唤醒运行总览</h2>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-xs ${
                  dashboard.worker?.enabled ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-700"
                }`}
              >
                <span className={`h-1.5 w-1.5 rounded-full ${dashboard.worker?.enabled ? "bg-emerald-500" : "bg-red-500"}`} />
                {dashboard.worker?.enabled ? "自动发送运行中" : "自动发送已关闭"}
              </span>
            </div>
            <p className="mt-1 text-xs text-zinc-500">
              第二天起，已开口但未预约客户由模型生成个性化计划，发送前复查客户回复和订单状态
            </p>
          </div>
          <div className="text-right text-xs text-zinc-500">
            <div>数据时区：北京时间</div>
            <div className="mt-1">更新于 {formatTime(dashboard.generated_at)}</div>
          </div>
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">
          <DashboardMetric icon={<GitBranch className="h-4 w-4" />} label="今日平台任务" value={dashboard.metrics?.platform_tasks_today || 0} />
          <DashboardMetric icon={<Bot className="h-4 w-4" />} label="今日个性化计划" value={dashboard.metrics?.personalized_plans_today || 0} />
          <DashboardMetric icon={<Activity className="h-4 w-4" />} label="执行中计划" value={dashboard.metrics?.active_plans || 0} />
          <DashboardMetric icon={<Clock className="h-4 w-4" />} label="待发送任务" value={dashboard.metrics?.pending_tasks || 0} />
          <DashboardMetric icon={<TimerReset className="h-4 w-4" />} label="当前已到期" value={dashboard.metrics?.due_tasks || 0} tone={dashboard.metrics?.due_tasks ? "warning" : "neutral"} />
          <DashboardMetric icon={<CircleCheck className="h-4 w-4" />} label="今日已发送" value={dashboard.metrics?.sent_today || 0} tone="success" />
          <DashboardMetric icon={<ShieldCheck className="h-4 w-4" />} label="今日安全停止" value={dashboard.metrics?.stopped_today || 0} />
          <DashboardMetric icon={<CircleX className="h-4 w-4" />} label="今日失败" value={dashboard.metrics?.failed_today || 0} tone={dashboard.metrics?.failed_today ? "danger" : "neutral"} />
        </div>

        <div className="mt-4 grid gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(320px,0.8fr)]">
          <div className="border-t border-zinc-200 pt-3">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-xs font-semibold text-zinc-700">今日处理链路</h3>
              <span className="text-xs text-zinc-500">只统计自动审批的个性化唤醒</span>
            </div>
            <div className="grid grid-cols-5 overflow-hidden rounded-lg border border-zinc-200">
              {[
                ["平台任务", dashboard.metrics?.platform_tasks_today || 0],
                ["生成计划", dashboard.metrics?.personalized_plans_today || 0],
                ["等待发送", dashboard.metrics?.pending_tasks || 0],
                ["发送成功", dashboard.metrics?.sent_today || 0],
                ["状态变化停止", dashboard.metrics?.stopped_today || 0],
              ].map(([label, value], index) => (
                <div key={String(label)} className={`min-w-0 px-1.5 py-3 sm:px-3 ${index ? "border-l border-zinc-200" : ""}`}>
                  <div className="min-h-7 text-center text-[10px] leading-tight text-zinc-500 sm:min-h-0 sm:text-left sm:text-xs">{label}</div>
                  <div className="mt-1 text-lg font-semibold">{value}</div>
                </div>
              ))}
            </div>
          </div>

          <div className="border-t border-zinc-200 pt-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <h3 className="text-xs font-semibold text-zinc-700">队列与计划监控</h3>
              <span
                className={`inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] ${
                  dashboard.plan_monitor?.enabled
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-zinc-100 text-zinc-600"
                }`}
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${
                    dashboard.plan_monitor?.enabled ? "bg-emerald-500" : "bg-zinc-400"
                  }`}
                />
                {dashboard.plan_monitor?.enabled ? "10分钟评估运行中" : "10分钟评估已关闭"}
              </span>
            </div>
            <div className="grid grid-cols-2 gap-x-5 gap-y-2 text-xs">
              <StatLine label="下个发送时间" value={formatTime(dashboard.next_due?.scheduled_at)} />
              <StatLine label="最近发送时间" value={formatTime(dashboard.last_sent?.sent_at)} />
              <StatLine label="最近监控扫描" value={formatTime(dashboard.plan_monitor?.last_scan_finished_at)} />
              <StatLine label="沉默阈值" value={`${dashboard.plan_monitor?.silent_minutes || 10} 分钟`} />
              <StatLine label="候选 / 已评估" value={`${dashboard.plan_monitor?.candidate_count || 0} / ${dashboard.plan_monitor?.evaluated_count || 0}`} />
              <StatLine label="创建 / 模型拒绝" value={`${dashboard.plan_monitor?.created_count || 0} / ${dashboard.plan_monitor?.rejected_count || 0}`} />
              <StatLine label="监控跳过" value={String(dashboard.plan_monitor?.skipped_count || 0)} />
              <StatLine label="监控错误" value={String(dashboard.plan_monitor?.error_count || 0)} />
            </div>
            {dashboard.plan_monitor?.last_error ? (
              <p className="mt-2 truncate text-[11px] text-red-600" title={dashboard.plan_monitor.last_error}>
                最近错误：{dashboard.plan_monitor.last_error}
              </p>
            ) : null}
          </div>
        </div>

        <div className="mt-4 border-t border-zinc-200 pt-3">
          <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
            <h3 className="text-xs font-semibold text-zinc-700">近 {dashboard.outcomes?.window_days || 30} 天触达效果</h3>
            <span className="text-xs text-zinc-500">开口与 AI 承接按结构事件统计，投诉率待平台提供结构事件</span>
          </div>
          <div className="grid grid-cols-2 overflow-hidden rounded-lg border border-zinc-200 sm:grid-cols-4 xl:grid-cols-8">
            {[
              ["24h 开口", rateLabel(dashboard.outcomes?.reopened_24h_rate)],
              ["72h 开口", rateLabel(dashboard.outcomes?.reopened_72h_rate)],
              ["AI 重新承接", rateLabel(dashboard.outcomes?.ai_resumed_72h_rate)],
              ["7天预约金", rateLabel(dashboard.outcomes?.deposit_7d_rate)],
              ["人均触达", String(dashboard.outcomes?.average_touches_per_customer ?? 0)],
              ["纯价值占比", rateLabel(dashboard.outcomes?.value_only_rate)],
              ["连续角度重复", rateLabel(dashboard.outcomes?.repeated_angle_rate)],
              ["素材带来开口", rateLabel(dashboard.outcomes?.asset_reply_72h_rate)],
            ].map(([label, value], index) => (
              <div
                key={label}
                className={`min-w-0 px-3 py-3 ${
                  index % 2 ? "border-l border-zinc-200" : ""
                } ${index >= 2 ? "sm:border-l" : ""} ${index >= 4 ? "border-t xl:border-t-0" : ""}`}
              >
                <div className="text-[11px] text-zinc-500">{label}</div>
                <div className="mt-1 text-base font-semibold text-zinc-900">{value}</div>
              </div>
            ))}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-xs text-zinc-500">
            <span>已发 {dashboard.outcomes?.sent_tasks || 0} 次 / {dashboard.outcomes?.contact_count || 0} 位客户</span>
            <span>素材使用 {dashboard.outcomes?.asset_send_count || 0} 次</span>
            <span>素材重复率 {rateLabel(dashboard.outcomes?.asset_repeat_rate)}</span>
            <span>安全停止 {dashboard.outcomes?.safety_stop_count || 0}</span>
            <span>失败 {dashboard.outcomes?.failure_count || 0}</span>
            <span>投诉率 {rateLabel(dashboard.outcomes?.complaint_rate)}</span>
          </div>
        </div>
      </section>
      <section className="grid min-h-[620px] grid-cols-1 lg:h-[calc(100vh-460px)] lg:max-h-[820px] lg:grid-cols-[280px_minmax(380px,1fr)_300px] 2xl:grid-cols-[340px_minmax(520px,1fr)_360px]">
        <aside className="flex h-full min-h-0 flex-col border-b border-zinc-200 bg-white lg:border-b-0 lg:border-r">
          <div className="border-b border-zinc-200 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-medium">
              <Search className="h-4 w-4" />
              客户与计划筛选
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="col-span-2 text-xs text-zinc-500">
                搜索客户
                <input
                  className="mt-1 w-full rounded-md border border-zinc-200 px-2 py-2 text-sm"
                  placeholder="昵称、客户 ID 或最近消息"
                  value={filters.keyword}
                  onChange={(event) => setFilters((prev) => ({ ...prev, keyword: event.target.value }))}
                />
              </label>
              <label className="text-xs text-zinc-500">
                至少未回复
                <select
                  className="mt-1 w-full rounded-md border border-zinc-200 bg-white px-2 py-2 text-sm text-zinc-900"
                  value={filters.silentMinutesMin}
                  onChange={(event) => setFilters((prev) => ({ ...prev, silentMinutesMin: event.target.value }))}
                >
                  <option value="0">不限</option>
                  <option value="60">1小时</option>
                  <option value="180">3小时</option>
                  <option value="720">12小时</option>
                  <option value="1440">24小时</option>
                </select>
              </label>
              <label className="text-xs text-zinc-500">
                唤醒状态
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
                  <option value="completed">已完成</option>
                  <option value="cancelled">已取消</option>
                  <option value="failed">失败</option>
                </select>
              </label>
              <label className="col-span-2 text-xs text-zinc-500">
                当前成交阶段
                <input
                  className="mt-1 w-full rounded-md border border-zinc-200 px-2 py-2 text-sm"
                  placeholder="填写完整阶段值"
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
                仅看未生成计划
              </label>
              <label className="text-xs text-zinc-500">
                展示数量
                <input
                  className="mt-1 w-full rounded-md border border-zinc-200 px-2 py-2 text-sm"
                  value={filters.limit}
                  onChange={(event) => setFilters((prev) => ({ ...prev, limit: event.target.value }))}
                />
              </label>
            </div>
            <button onClick={loadCandidates} className="mt-3 w-full rounded-md bg-zinc-900 px-3 py-2 text-sm text-white hover:bg-zinc-800">
              查询客户
            </button>
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto p-3">
            {candidates.length === 0 ? (
              <div className="rounded-lg border border-dashed border-zinc-200 p-6 text-center text-sm text-zinc-500">暂无候选客户</div>
            ) : (
              <div className="space-y-2">
                {candidates.map((item) => {
                  const active = candidateKey(selectedCustomer) === candidateKey(item);
                  const name = candidateName(item);
                  return (
                    <div
                      key={candidateKey(item)}
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
                        <div className="flex min-w-0 items-center gap-2.5">
                          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-sm font-semibold text-white">
                            {name.slice(0, 1)}
                          </div>
                          <div className="min-w-0">
                            <div className="truncate text-sm font-semibold">{name}</div>
                            <div className="truncate text-xs text-zinc-500">客户 ID：{item.customer_id}</div>
                          </div>
                        </div>
                        <span className="rounded-full bg-zinc-100 px-2 py-1 text-xs text-zinc-600">{statusLabel(item.outreach_status)}</span>
                      </div>
                      <div className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-zinc-500">
                        <span className="truncate">客服：{item.wechat || item.user_id || "-"}</span>
                        <span className="text-right">未回复 {formatSilent(item.silent_minutes)}</span>
                        <span className="col-span-2 truncate">企微 ID：{item.external_userid || "-"}</span>
                      </div>
                      <div className="mt-2 flex items-end gap-2">
                        <p className="min-w-0 flex-1 line-clamp-2 text-xs text-zinc-600">{item.last_customer_message || item.latest_event_summary || "暂无最近消息摘要"}</p>
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            openConversationHistory(item);
                          }}
                          className="inline-flex h-8 shrink-0 items-center justify-center gap-1 rounded-md border border-zinc-200 bg-white px-2 text-xs text-zinc-700 hover:bg-zinc-100"
                          title="查看历史聊天记录"
                          disabled={busy === `history-${item.customer_id}`}
                        >
                          <MessageSquareText className="h-4 w-4" />
                          聊天
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
                      className="inline-flex min-w-[88px] items-center justify-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50 disabled:cursor-wait disabled:bg-zinc-50 disabled:text-zinc-500"
                      disabled={busy === "generate"}
                    >
                      {busy === "generate" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
                      {busy === "generate" ? "生成中" : "生成计划"}
                    </button>
                    <button
                      onClick={() => generatePlan(selectedCustomer, true)}
                      className="inline-flex min-w-[104px] items-center justify-center gap-2 rounded-md bg-zinc-900 px-3 py-2 text-sm text-white hover:bg-zinc-800 disabled:cursor-wait disabled:bg-zinc-700"
                      disabled={busy === "generate-activate"}
                    >
                      {busy === "generate-activate" ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
                      {busy === "generate-activate" ? "生成中" : "生成并启用"}
                    </button>
                  </>
                ) : null}
              </div>
            </div>

            {planLoading ? (
              <div className="flex items-center justify-center gap-2 p-12 text-sm text-zinc-500">
                <LoaderCircle className="h-4 w-4 animate-spin" />
                正在加载当前客户的计划
              </div>
            ) : selectedPlan ? (
              <div className="p-4">
                <div className="grid grid-cols-3 gap-3">
                  <InfoBlock label="成交阶段" value={selectedPlan.conversion_stage || selectedPlan.customer_stage || "-"} />
                  <InfoBlock label="客户类型" value={selectedPlan.customer_type || "-"} />
                  <InfoBlock label="卡点原因" value={selectedPlan.stall_reason || "-"} />
                  <InfoBlock label="最后意向" value={selectedPlan.last_explicit_intent || "-"} />
                  <InfoBlock label="下一步动作" value={selectedPlan.next_best_action || "-"} />
                  <InfoBlock label="计划目标" value={selectedPlan.plan_goal || "-"} />
                  <InfoBlock label="最近互动" value={selectedPlan.last_interaction_summary || "-"} />
                  <InfoBlock label="客户心理" value={selectedPlan.customer_psychology || "-"} />
                  <InfoBlock label="抑制原因" value={selectedPlan.suppress_reason || "-"} />
                  <InfoBlock label="核心阻力" value={selectedPlan.core_barrier || "-"} />
                  <InfoBlock label="心理需求" value={selectedPlan.emotional_need || "-"} />
                </div>
                <div className="mt-3 rounded-md border border-zinc-200 bg-zinc-50 p-3">
                  <p className="text-xs font-medium text-zinc-500">递进逻辑</p>
                  <p className="mt-1 text-sm text-zinc-700">{selectedPlan.plan_arc || "-"}</p>
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
                    tasks.map((task) => {
                      const planning = taskPlanningMetadata(task);
                      const asset = taskResolvedAsset(task);
                      const avoidRepeating = Array.isArray(planning.avoid_repeating)
                        ? planning.avoid_repeating.map(String).filter(Boolean).join("、")
                        : "";
                      return (
                      <div key={task.id} className="rounded-lg border border-zinc-200 p-4">
                        <div className="flex items-start justify-between gap-3">
                          <div>
                            <div className="flex items-center gap-2">
                              <span className="rounded-full bg-zinc-900 px-2 py-1 text-xs text-white">第 {task.step_index} 步</span>
                              <span className="text-sm font-medium">{task.intent || "outreach"}</span>
                              <span className="rounded-full bg-zinc-100 px-2 py-1 text-xs text-zinc-600">{statusLabel(task.status)}</span>
                            </div>
                            <p className="mt-2 text-sm text-zinc-700">{task.message_goal || "-"}</p>
                            <div className="mt-2 flex flex-wrap gap-2 text-xs text-zinc-600">
                              <span className="rounded bg-zinc-100 px-2 py-1 text-zinc-700">
                                内容模式：{String(planning.content_mode || "-")}
                              </span>
                              <span className="rounded bg-emerald-50 px-2 py-1 text-emerald-700">
                                心理角度：{String(planning.persuasion_angle || "-")}
                              </span>
                              <span className="rounded bg-blue-50 px-2 py-1 text-blue-700">
                                新价值：{String(planning.new_value || "-")}
                              </span>
                              {asset.type ? (
                                <span className="rounded bg-amber-50 px-2 py-1 text-amber-700">
                                  素材：{String(asset.type)} / {String(asset.source || "-")}
                                </span>
                              ) : null}
                            </div>
                            <p className="mt-2 text-xs text-zinc-500">
                              时间理由：{String(planning.timing_reason || "-")} · 紧迫度：{String(planning.urgency_level || "-")}
                            </p>
                            {avoidRepeating ? <p className="mt-2 text-xs text-zinc-500">避免复读：{avoidRepeating}</p> : null}
                            <p className="mt-1 text-xs text-zinc-500">
                              计划发送：{formatTime(task.scheduled_at)} · {boolLabel(task.should_send_payment_collection)} · 发送结果：{sendStatusLabel(task.send_status)}
                            </p>
                          </div>
                          <div className="flex items-center gap-2">
                            <button
                              onClick={() => executeTask(task.id)}
                              className="inline-flex items-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50 disabled:cursor-not-allowed disabled:bg-zinc-50 disabled:text-zinc-400"
                              disabled={busy === `task-${task.id}` || !taskHasMessages(task)}
                              title={taskHasMessages(task) ? "发送前会复查客户状态，并结合最新上下文润色文字" : "该旧任务缺少消息，请重新生成计划"}
                            >
                              <Send className="h-4 w-4" />
                              立即执行
                            </button>
                          </div>
                        </div>
                        <div className="mt-4">
                          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
                            <h4 className="text-xs font-semibold text-zinc-700">计划发送消息</h4>
                            <span className="text-xs text-zinc-500">已生成即可执行；发送前会复查状态并只润色文字</span>
                          </div>
                          <OutreachMessageList messages={task.reply_messages} />
                        </div>
                        {task.error_message ? <p className="mt-2 text-xs text-red-600">{task.error_message}</p> : null}
                      </div>
                    );
                    })
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

        <aside className="h-full min-h-0 overflow-y-auto border-t border-zinc-200 bg-white p-4 lg:border-l lg:border-t-0">
          {!selectedCustomer ? (
            <div className="rounded-xl border border-dashed border-zinc-200 p-8 text-center text-sm text-zinc-500">
              从左侧选择客户后查看画像和历史事件
            </div>
          ) : detailLoading ? (
            <div className="flex items-center justify-center gap-2 rounded-xl border border-zinc-200 p-8 text-sm text-zinc-500">
              <LoaderCircle className="h-4 w-4 animate-spin" />
              正在加载客户详情
            </div>
          ) : (
            <>
              <section className="rounded-xl border border-zinc-200">
                <div className="border-b border-zinc-200 p-4">
                  <div className="flex items-center gap-3">
                    <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full bg-zinc-900 text-base font-semibold text-white">
                      {candidateName(selectedCustomer).slice(0, 1)}
                    </div>
                    <div className="min-w-0">
                      <h2 className="truncate text-sm font-semibold">{candidateName(selectedCustomer)}</h2>
                      <p className="truncate text-xs text-zinc-500">客户 ID：{selectedCustomer.customer_id}</p>
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => openConversationHistory(selectedCustomer)}
                    className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-md border border-zinc-200 px-3 py-2 text-sm hover:bg-zinc-50"
                  >
                    <MessageSquareText className="h-4 w-4" />
                    查看聊天记录
                  </button>
                </div>
                <div className="space-y-2 p-4">
                  <InfoLine label="企微 ID" value={selectedCustomer.external_userid || "-"} />
                  <InfoLine label="接待账号" value={selectedCustomer.wechat || selectedCustomer.user_id || "-"} />
                  <InfoLine label="当前阶段" value={customerDetail?.lifecycle_stage || selectedCustomer.lifecycle_stage || "未分阶段"} />
                  <InfoLine label="最近客户消息" value={formatTime(selectedCustomer.last_customer_message_at)} />
                  <InfoLine label="最近主动触达" value={formatTime(selectedCustomer.last_outreach_at)} />
                </div>
              </section>

              <CustomerProfilePanel
                portrait={objectValue(customerDetail?.portrait || selectedCustomer.portrait)}
                basicInfo={objectValue(customerDetail?.basic_info || selectedCustomer.basic_info)}
                updatedAt={customerDetail?.profile_updated_at}
              />

              <CustomerEventTimeline events={customerDetail?.history_events || []} />

              <OutreachEventTimeline events={customerDetail?.outreach_events || planEvents} />
            </>
          )}
        </aside>
      </section>
      {historyOpen ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30 px-4">
          <section className="flex max-h-[82vh] w-full max-w-2xl flex-col rounded-xl border border-zinc-200 bg-white shadow-xl">
            <div className="flex items-start justify-between gap-3 border-b border-zinc-200 p-4">
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold">历史聊天记录</h2>
                <p className="truncate text-xs text-zinc-500">{historyCustomer ? `${candidateName(historyCustomer)} · 客户 ID ${historyCustomer.customer_id}` : ""}</p>
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

function DashboardMetric({
  icon,
  label,
  value,
  tone = "neutral",
}: {
  icon: ReactNode;
  label: string;
  value: number;
  tone?: "neutral" | "success" | "warning" | "danger";
}) {
  const toneClass = {
    neutral: "text-zinc-600",
    success: "text-emerald-700",
    warning: "text-amber-700",
    danger: "text-red-700",
  }[tone];
  return (
    <div className="min-w-0 rounded-lg border border-zinc-200 bg-white px-3 py-3">
      <div className={`flex items-center gap-2 text-xs ${toneClass}`}>
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <div className="mt-2 text-xl font-semibold text-zinc-900">{value}</div>
    </div>
  );
}

function StatLine({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex min-w-0 items-center justify-between gap-3 border-b border-zinc-100 py-1.5">
      <span className="truncate text-zinc-500">{label}</span>
      <span className="shrink-0 font-medium text-zinc-800">{value}</span>
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

function CustomerProfilePanel({
  portrait,
  basicInfo,
  updatedAt,
}: {
  portrait: JsonObject;
  basicInfo: JsonObject;
  updatedAt?: string;
}) {
  const summary = textValue(portrait.summary);
  const concerns = listValue(portrait.concerns);
  const customerTags = listValue(portrait.customer_type_tags);
  const styleTags = listValue(portrait.style_tags);
  const fields = [
    ["登记姓名", basicInfo.customer_name, <UserRound key="name" className="h-3.5 w-3.5" />],
    ["联系电话", basicInfo.phone, <Phone key="phone" className="h-3.5 w-3.5" />],
    ["城市", basicInfo.city, <MapPin key="city" className="h-3.5 w-3.5" />],
    ["区域/地标", basicInfo.area_or_landmark, <MapPin key="area" className="h-3.5 w-3.5" />],
    ["意向门店", basicInfo.preferred_store_name, <MapPin key="store" className="h-3.5 w-3.5" />],
    ["意向到店", [textValue(basicInfo.intent_date), textValue(basicInfo.intent_time)].filter(Boolean).join(" "), <Clock key="time" className="h-3.5 w-3.5" />],
  ].filter(([, value]) => Boolean(textValue(value)));

  return (
    <section className="mt-4 overflow-hidden rounded-xl border border-zinc-200">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <UserRound className="h-4 w-4" />
          客户画像
        </div>
        <span className="text-[11px] text-zinc-400">更新 {formatTime(updatedAt)}</span>
      </div>
      <div className="space-y-4 p-4">
        <div>
          <div className="text-xs text-zinc-500">当前判断</div>
          <p className="mt-1 text-sm leading-relaxed text-zinc-800">{summary || "暂未形成画像摘要"}</p>
        </div>

        <div className="grid grid-cols-2 gap-x-4 gap-y-3 border-y border-zinc-100 py-3 text-xs">
          <ProfileStatus label="意向程度" value={levelLabel(portrait.intent_level)} />
          <ProfileStatus label="信任程度" value={levelLabel(portrait.trust_level)} />
          <ProfileStatus label="决策阶段" value={textValue(portrait.decision_stage) || "-"} />
          <ProfileStatus label="预约金状态" value={levelLabel(portrait.deposit_state || basicInfo.deposit_state)} />
        </div>

        {fields.length ? (
          <div className="grid grid-cols-1 gap-2">
            {fields.map(([label, value, icon]) => (
              <div key={String(label)} className="flex items-start gap-2 text-xs">
                <span className="mt-0.5 text-zinc-400">{icon as ReactNode}</span>
                <span className="w-16 shrink-0 text-zinc-500">{String(label)}</span>
                <span className="min-w-0 break-words text-zinc-800">{textValue(value)}</span>
              </div>
            ))}
          </div>
        ) : null}

        {textValue(portrait.main_objection) ? (
          <ProfileTextBlock label="主要顾虑" value={textValue(portrait.main_objection)} />
        ) : null}
        {textValue(portrait.next_sales_strategy) ? (
          <ProfileTextBlock label="建议承接" value={textValue(portrait.next_sales_strategy)} accent />
        ) : null}
        <TagGroup label="客户类型" items={customerTags} />
        <TagGroup label="当前顾虑" items={concerns} />
        <TagGroup label="沟通偏好" items={styleTags} />
      </div>
    </section>
  );
}

function CustomerEventTimeline({ events }: { events: CustomerHistoryEvent[] }) {
  const sorted = [...events].reverse();
  return (
    <section className="mt-4 rounded-xl border border-zinc-200">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <History className="h-4 w-4" />
          客户历史事件
        </div>
        <span className="text-xs text-zinc-400">{sorted.length} 条</span>
      </div>
      <div className="p-4">
        {sorted.length ? (
          <div className="space-y-0">
            {sorted.slice(0, 40).map((event, index) => (
              <div key={event.event_id || `${event.event_type}-${event.event_time}-${index}`} className="relative border-l border-zinc-200 pb-5 pl-4 last:pb-0">
                <span className="absolute -left-1 top-1 h-2 w-2 rounded-full bg-zinc-700" />
                <div className="flex items-start justify-between gap-2">
                  <span className="text-xs font-medium text-zinc-800">{eventTypeLabel(event.event_type)}</span>
                  <span className="shrink-0 text-[11px] text-zinc-400">{formatTime(event.event_time)}</span>
                </div>
                <p className="mt-1 text-xs leading-relaxed text-zinc-600">{event.summary || "未记录摘要"}</p>
                {event.impact ? <p className="mt-1 text-[11px] text-zinc-500">影响：{event.impact}</p> : null}
                <FactList facts={objectValue(event.facts)} />
              </div>
            ))}
          </div>
        ) : (
          <p className="py-3 text-center text-sm text-zinc-500">暂无客户历史事件</p>
        )}
      </div>
    </section>
  );
}

function OutreachEventTimeline({ events }: { events: OutreachEvent[] }) {
  return (
    <section className="mt-4 rounded-xl border border-zinc-200">
      <div className="flex items-center justify-between border-b border-zinc-200 px-4 py-3">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <Activity className="h-4 w-4" />
          唤醒执行事件
        </div>
        <span className="text-xs text-zinc-400">{events.length} 条</span>
      </div>
      <div className="divide-y divide-zinc-100 px-4">
        {events.length ? (
          events.slice(0, 30).map((event) => (
            <div key={event.id} className="py-3">
              <div className="flex items-start justify-between gap-2">
                <span className="text-xs font-medium text-zinc-800">{eventTypeLabel(event.event_type)}</span>
                <span className="shrink-0 text-[11px] text-zinc-400">{formatTime(event.created_at)}</span>
              </div>
              <p className="mt-1 text-xs leading-relaxed text-zinc-600">{event.event_summary || "未记录摘要"}</p>
            </div>
          ))
        ) : (
          <p className="py-5 text-center text-sm text-zinc-500">暂无唤醒执行事件</p>
        )}
      </div>
    </section>
  );
}

function ProfileStatus({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="text-zinc-500">{label}</div>
      <div className="mt-0.5 font-medium text-zinc-800">{value}</div>
    </div>
  );
}

function ProfileTextBlock({ label, value, accent = false }: { label: string; value: string; accent?: boolean }) {
  return (
    <div className={`border-l-2 pl-3 ${accent ? "border-emerald-500" : "border-amber-400"}`}>
      <div className="text-xs text-zinc-500">{label}</div>
      <p className="mt-1 text-xs leading-relaxed text-zinc-700">{value}</p>
    </div>
  );
}

function TagGroup({ label, items }: { label: string; items: string[] }) {
  if (!items.length) return null;
  return (
    <div>
      <div className="mb-2 flex items-center gap-1 text-xs text-zinc-500">
        <Tags className="h-3.5 w-3.5" />
        {label}
      </div>
      <div className="flex flex-wrap gap-1.5">
        {items.map((item) => (
          <span key={item} className="rounded bg-zinc-100 px-2 py-1 text-[11px] text-zinc-700">
            {item}
          </span>
        ))}
      </div>
    </div>
  );
}

function FactList({ facts }: { facts: JsonObject }) {
  const entries = Object.entries(facts)
    .map(([key, value]) => [
      fieldLabel(key),
      key.endsWith("_state") || key === "status" ? levelLabel(value) : textValue(value),
    ] as const)
    .filter(([, value]) => Boolean(value));
  if (!entries.length) return null;
  return (
    <div className="mt-2 space-y-1">
      {entries.slice(0, 8).map(([label, value]) => (
        <div key={label} className="flex items-start gap-2 text-[11px] leading-relaxed">
          <span className="shrink-0 text-zinc-400">{label}</span>
          <span className="min-w-0 break-words text-zinc-600">{value}</span>
        </div>
      ))}
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
