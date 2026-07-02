"use client";

import Link from "next/link";
import { useMemo, useState } from "react";
import { AlertTriangle, ArrowLeft, Database, RefreshCw, Search, Trash2 } from "lucide-react";

type Counts = Record<string, number>;

type SopSummaryItem = {
  sop_pack_id?: string;
  sop_pack_name?: string;
  sop_category?: string;
  trigger_source?: string;
  status?: string;
  count?: number;
  latest_at?: string;
};

type EventItem = {
  id?: string;
  event_type?: string;
  stage?: string;
  summary?: string;
  created_at?: string;
};

type InspectResult = {
  customer_id?: string;
  counts?: Counts;
  sop_summary?: SopSummaryItem[];
  latest_events?: EventItem[];
};

type ClearOptions = {
  clear_memory: boolean;
  clear_sop: boolean;
  clear_conversations: boolean;
  clear_outreach: boolean;
};

const DEFAULT_OPTIONS: ClearOptions = {
  clear_memory: true,
  clear_sop: true,
  clear_conversations: false,
  clear_outreach: false,
};

const COUNT_LABELS: Record<string, string> = {
  customer_memory: "客户画像",
  history_events: "历史事件",
  sop_send_tasks: "SOP 发送记录",
  conversations: "对话",
  messages: "消息",
  runs: "运行日志",
  node_traces: "节点轨迹",
  outreach_plans: "主动计划",
  outreach_tasks: "主动任务",
  outreach_events: "主动事件",
};

export function CustomerCleanupWorkbench() {
  const [customerId, setCustomerId] = useState("");
  const [confirmCustomerId, setConfirmCustomerId] = useState("");
  const [result, setResult] = useState<InspectResult | null>(null);
  const [clearResult, setClearResult] = useState<Record<string, unknown> | null>(null);
  const [options, setOptions] = useState<ClearOptions>(DEFAULT_OPTIONS);
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [error, setError] = useState("");

  const normalizedCustomerId = customerId.trim();
  const canClear = normalizedCustomerId && confirmCustomerId.trim() === normalizedCustomerId && !clearing;
  const totalRecords = useMemo(() => {
    const counts = result?.counts || {};
    return Object.values(counts).reduce((sum, value) => sum + Number(value || 0), 0);
  }, [result]);

  async function inspect() {
    if (!normalizedCustomerId) {
      setError("请输入 customer_id");
      return;
    }
    setLoading(true);
    setError("");
    setClearResult(null);
    try {
      const response = await fetch(`/api/admin/customer-records?customer_id=${encodeURIComponent(normalizedCustomerId)}`, {
        cache: "no-store",
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || data?.error || "查询失败");
      }
      setResult(data);
      setConfirmCustomerId("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "查询失败");
    } finally {
      setLoading(false);
    }
  }

  async function clearRecords() {
    if (!canClear) {
      setError("请在确认框输入完整 customer_id");
      return;
    }
    setClearing(true);
    setError("");
    setClearResult(null);
    try {
      const response = await fetch("/api/admin/customer-records/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ customer_id: normalizedCustomerId, ...options }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || data?.error || "清理失败");
      }
      setClearResult(data);
      await inspect();
    } catch (err) {
      setError(err instanceof Error ? err.message : "清理失败");
    } finally {
      setClearing(false);
    }
  }

  return (
    <main className="min-h-screen bg-slate-50 text-slate-950">
      <header className="border-b bg-white px-6 py-4">
        <div className="flex items-center justify-between gap-4">
          <div>
            <Link href="/" className="mb-3 inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm hover:bg-slate-50">
              <ArrowLeft className="h-4 w-4" />
              返回对话
            </Link>
            <h1 className="flex items-center gap-2 text-xl font-semibold">
              <Database className="h-5 w-5" />
              客户记录清理
            </h1>
            <p className="mt-1 text-sm text-slate-500">按指定 customer_id 清空本地画像、历史事件、SOP 发送记录等测试数据。</p>
          </div>
        </div>
      </header>

      <section className="mx-auto grid max-w-6xl gap-5 p-6 lg:grid-cols-[420px_minmax(0,1fr)]">
        <aside className="space-y-5">
          <div className="rounded-lg border bg-white p-5">
            <label className="text-sm font-medium text-slate-700">
              customer_id
              <input
                value={customerId}
                onChange={(event) => setCustomerId(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void inspect();
                }}
                className="mt-2 h-10 w-full rounded-md border px-3 text-sm"
                placeholder="例如 21325693 或 external_userid"
              />
            </label>
            <button
              type="button"
              onClick={() => void inspect()}
              disabled={loading}
              className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-md bg-slate-950 px-4 py-2 text-sm text-white disabled:opacity-60"
            >
              {loading ? <RefreshCw className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
              查询客户记录
            </button>
            {error ? (
              <div className="mt-4 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>
            ) : null}
          </div>

          <div className="rounded-lg border bg-white p-5">
            <h2 className="text-sm font-semibold">清理范围</h2>
            <div className="mt-4 space-y-3">
              <OptionRow
                checked={options.clear_memory}
                onChange={(checked) => setOptions((prev) => ({ ...prev, clear_memory: checked }))}
                title="清空画像与历史事件"
                description="删除 customer_memory 和 history_events。"
              />
              <OptionRow
                checked={options.clear_sop}
                onChange={(checked) => setOptions((prev) => ({ ...prev, clear_sop: checked }))}
                title="清空 SOP 发送记录"
                description="删除 sop_send_tasks；客户会重新进入未发送 SOP 状态。"
              />
              <OptionRow
                checked={options.clear_conversations}
                onChange={(checked) => setOptions((prev) => ({ ...prev, clear_conversations: checked }))}
                title="清空对话与运行日志"
                description="删除 conversations/messages/runs/node_traces，默认不选。"
              />
              <OptionRow
                checked={options.clear_outreach}
                onChange={(checked) => setOptions((prev) => ({ ...prev, clear_outreach: checked }))}
                title="清空主动触达记录"
                description="删除 outreach_plans/tasks/events，默认不选。"
              />
            </div>
          </div>

          <div className="rounded-lg border border-amber-200 bg-amber-50 p-5">
            <div className="flex gap-2 text-sm text-amber-800">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
              <div>
                <div className="font-semibold">危险操作确认</div>
                <p className="mt-1">请输入完整 customer_id 才能执行清理。该操作只清本地数据库，不会删除企微平台聊天记录。</p>
              </div>
            </div>
            <input
              value={confirmCustomerId}
              onChange={(event) => setConfirmCustomerId(event.target.value)}
              className="mt-4 h-10 w-full rounded-md border bg-white px-3 text-sm"
              placeholder="再次输入 customer_id"
            />
            <button
              type="button"
              onClick={() => void clearRecords()}
              disabled={!canClear}
              className="mt-4 inline-flex w-full items-center justify-center gap-2 rounded-md bg-red-600 px-4 py-2 text-sm text-white disabled:opacity-50"
            >
              <Trash2 className="h-4 w-4" />
              {clearing ? "清理中" : "确认清理"}
            </button>
          </div>
        </aside>

        <section className="space-y-5">
          <div className="rounded-lg border bg-white p-5">
            <div className="flex items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold">查询结果</h2>
                <p className="mt-1 text-sm text-slate-500">
                  {result?.customer_id ? `customer_id: ${result.customer_id}` : "还没有查询客户。"}
                </p>
              </div>
              <div className="text-right">
                <div className="text-2xl font-semibold">{totalRecords}</div>
                <div className="text-xs text-slate-500">本地记录数</div>
              </div>
            </div>
            <div className="mt-5 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {Object.entries(COUNT_LABELS).map(([key, label]) => (
                <Metric key={key} label={label} value={Number(result?.counts?.[key] || 0)} />
              ))}
            </div>
          </div>

          {clearResult ? (
            <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-5">
              <h2 className="text-sm font-semibold text-emerald-800">清理完成</h2>
              <pre className="mt-3 max-h-60 overflow-auto rounded-md bg-white p-3 text-xs text-emerald-900">
                {JSON.stringify(clearResult, null, 2)}
              </pre>
            </div>
          ) : null}

          <div className="rounded-lg border bg-white p-5">
            <h2 className="text-sm font-semibold">SOP 发送摘要</h2>
            <div className="mt-3 overflow-x-auto">
              <table className="w-full min-w-[720px] text-left text-sm">
                <thead className="border-b text-xs text-slate-500">
                  <tr>
                    <th className="py-2">SOP</th>
                    <th className="py-2">类目</th>
                    <th className="py-2">来源</th>
                    <th className="py-2">状态</th>
                    <th className="py-2">数量</th>
                    <th className="py-2">最近时间</th>
                  </tr>
                </thead>
                <tbody>
                  {(result?.sop_summary || []).map((item, index) => (
                    <tr key={`${item.sop_pack_id}-${item.status}-${index}`} className="border-b last:border-0">
                      <td className="max-w-[260px] py-2">
                        <div className="truncate font-medium">{item.sop_pack_name || item.sop_pack_id || "-"}</div>
                        <div className="truncate font-mono text-xs text-slate-500">{item.sop_pack_id || "-"}</div>
                      </td>
                      <td className="py-2">{item.sop_category || "-"}</td>
                      <td className="py-2">{item.trigger_source || "-"}</td>
                      <td className="py-2">{item.status || "-"}</td>
                      <td className="py-2">{item.count || 0}</td>
                      <td className="py-2">{formatTime(item.latest_at)}</td>
                    </tr>
                  ))}
                  {(!result?.sop_summary || result.sop_summary.length === 0) ? (
                    <tr>
                      <td colSpan={6} className="py-6 text-center text-slate-500">暂无 SOP 发送记录。</td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
          </div>

          <div className="rounded-lg border bg-white p-5">
            <h2 className="text-sm font-semibold">最近历史事件</h2>
            <div className="mt-3 space-y-2">
              {(result?.latest_events || []).map((event) => (
                <div key={event.id} className="rounded-md border p-3 text-sm">
                  <div className="flex items-center justify-between gap-3">
                    <span className="font-medium">{event.event_type || "-"}</span>
                    <span className="text-xs text-slate-500">{formatTime(event.created_at)}</span>
                  </div>
                  <div className="mt-1 text-slate-600">{event.summary || "-"}</div>
                </div>
              ))}
              {(!result?.latest_events || result.latest_events.length === 0) ? (
                <div className="py-4 text-sm text-slate-500">暂无历史事件。</div>
              ) : null}
            </div>
          </div>
        </section>
      </section>
    </main>
  );
}

function OptionRow({
  checked,
  onChange,
  title,
  description,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  title: string;
  description: string;
}) {
  return (
    <label className="flex cursor-pointer gap-3 rounded-md border p-3 hover:bg-slate-50">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
        className="mt-1 h-4 w-4"
      />
      <span>
        <span className="block text-sm font-medium">{title}</span>
        <span className="mt-1 block text-xs text-slate-500">{description}</span>
      </span>
    </label>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border bg-slate-50 p-3">
      <div className="text-xs text-slate-500">{label}</div>
      <div className="mt-1 text-xl font-semibold">{value}</div>
    </div>
  );
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
