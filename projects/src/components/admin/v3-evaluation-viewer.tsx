"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ArrowLeft, RefreshCw } from "lucide-react";

type Manifest = { run_id: string; git_commit?: string; created_at?: string; case_count?: number };
type Critic = { status?: string; failure_owner?: string; reason?: string; scores?: Record<string, number> };
type CaseResult = {
  case_id: string;
  partition?: string;
  category?: string;
  hard_pass?: boolean;
  hard_errors?: string[];
  infrastructure_errors?: string[];
  reply_messages?: Array<{ type?: string; content?: unknown }>;
  critic?: Critic;
  human_review?: { status?: string; verdict?: string };
};
type RunDetail = {
  manifest?: Manifest;
  evaluation?: Record<string, unknown>;
  results?: { human_calibration_status?: string; results?: CaseResult[] };
};

export function V3EvaluationViewer() {
  const [runs, setRuns] = useState<Manifest[]>([]);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  const loadRuns = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await fetch("/api/v3-evaluations", { cache: "no-store" });
      const data = await response.json();
      if (!response.ok) throw new Error(data.detail || data.error || "加载失败");
      const items = (data.items || []) as Manifest[];
      setRuns(items);
      setSelected((current) => current || items[0]?.run_id || "");
    } catch (err) {
      setError(err instanceof Error ? err.message : "加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void loadRuns(); }, [loadRuns]);
  useEffect(() => {
    if (!selected) return;
    void fetch(`/api/v3-evaluations?run_id=${encodeURIComponent(selected)}`, { cache: "no-store" })
      .then(async (response) => {
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || data.error || "加载详情失败");
        setDetail(data);
      })
      .catch((err) => setError(err instanceof Error ? err.message : "加载详情失败"));
  }, [selected]);

  const cases = detail?.results?.results || [];
  const stats = useMemo(() => ({
    total: cases.length,
    hardPass: cases.filter((item) => item.hard_pass).length,
    criticPass: cases.filter((item) => item.critic?.status === "pass").length,
    pending: cases.filter((item) => item.human_review?.status !== "reviewed").length,
  }), [cases]);

  return (
    <main className="min-h-screen bg-[#f5f7f8] text-[#17211d]">
      <header className="flex items-center justify-between border-b border-[#dbe2de] bg-white px-6 py-4">
        <div className="flex items-center gap-3">
          <Link href="/logs" className="rounded border border-[#dbe2de] p-2" title="返回日志"><ArrowLeft size={18} /></Link>
          <div><h1 className="text-xl font-semibold">V3 离线评测</h1><p className="text-sm text-[#607069]">黄金集与 Critic 只用于离线诊断，不参与线上回复。</p></div>
        </div>
        <button onClick={() => void loadRuns()} className="rounded bg-[#0d7a55] p-2 text-white" title="刷新"><RefreshCw size={18} className={loading ? "animate-spin" : ""} /></button>
      </header>
      {error && <div className="m-6 border border-red-200 bg-red-50 p-3 text-red-700">{error}</div>}
      <section className="grid gap-4 p-6 lg:grid-cols-[280px_1fr]">
        <aside className="border border-[#dbe2de] bg-white p-3">
          <div className="mb-2 text-sm font-medium">评测运行</div>
          <div className="space-y-2">
            {runs.map((run) => <button key={run.run_id} onClick={() => setSelected(run.run_id)} className={`w-full border p-3 text-left text-sm ${selected === run.run_id ? "border-[#0d7a55] bg-[#edf8f3]" : "border-[#e4e9e6]"}`}><div className="font-medium">{run.run_id}</div><div className="mt-1 text-xs text-[#66736d]">{run.case_count || 0} 条 · {run.git_commit?.slice(0, 10)}</div></button>)}
          </div>
        </aside>
        <div className="space-y-4">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[["案例", stats.total], ["硬校验通过", stats.hardPass], ["Critic 通过", stats.criticPass], ["待人工校准", stats.pending]].map(([label, value]) => <div key={String(label)} className="border border-[#dbe2de] bg-white p-4"><div className="text-sm text-[#66736d]">{label}</div><div className="mt-1 text-2xl font-semibold">{value}</div></div>)}
          </div>
          <div className="border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">当前 Critic 结论是模型预测。15 条校准集未完成人工判定前，不得称为“已校准”或作为上线通过证据。</div>
          {cases.map((item) => <article key={item.case_id} className="border border-[#dbe2de] bg-white p-4">
            <div className="flex flex-wrap items-center gap-2"><strong>{item.case_id}</strong><span className="text-sm text-[#66736d]">{item.partition} · {item.category}</span><span className={`text-xs ${item.hard_pass ? "text-emerald-700" : "text-red-700"}`}>硬校验 {item.hard_pass ? "通过" : "失败"}</span><span className="text-xs">Critic {item.critic?.status || "未运行"}</span></div>
            <div className="mt-3 space-y-2">{(item.reply_messages || []).map((message, index) => <div key={index} className="border-l-2 border-[#8bb8a5] pl-3 text-sm"><span className="mr-2 text-[#66736d]">{message.type}</span>{typeof message.content === "string" ? message.content : JSON.stringify(message.content)}</div>)}</div>
            {(item.critic?.reason || item.critic?.failure_owner) && <div className="mt-3 bg-[#f7f9f8] p-3 text-sm">归因：{item.critic?.failure_owner || "none"}。{item.critic?.reason}</div>}
            {!!item.infrastructure_errors?.length && <div className="mt-3 text-sm text-red-700">{item.infrastructure_errors.join("；")}</div>}
          </article>)}
        </div>
      </section>
    </main>
  );
}
