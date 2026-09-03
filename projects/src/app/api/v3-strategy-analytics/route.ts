import { NextRequest } from "next/server";

import { jsonResponse, proxyAiPathsAdmin } from "../_lib/ai-paths";

const coreViews = {
  summary: "summary",
  checkpoints: "by-checkpoint",
  sequences: "by-sequence",
  scripts: "by-script",
  failures: "failures",
} as const;

const decisionViews = {
  intents: "by-intent",
  emotions: "by-emotion",
  closing: "by-closing",
  transitions: "transitions",
} as const;

export async function GET(request: NextRequest) {
  const query = new URLSearchParams(request.nextUrl.searchParams);
  query.set("limit", "20");
  const errors: Record<string, string> = {};
  const data: Record<string, unknown> = {};

  const load = async (views: Record<string, string>) => Promise.all(
    Object.entries(views).map(async ([key, endpoint]) => {
      const response = await proxyAiPathsAdmin(
        `/admin/v3-strategy-analytics/${endpoint}?${query.toString()}`,
      );
      const payload = (await response.json()) as Record<string, unknown>;
      if (!response.ok) {
        errors[key] = String(payload.detail || payload.error || `HTTP ${response.status}`);
        return;
      }
      data[key] = payload;
    }),
  );

  await load(coreViews);

  if (!data.summary) {
    return jsonResponse(
      {
        error: "销售策略统计暂时不可用",
        detail: errors.summary || "后端尚未提供 V3 策略统计接口",
        errors,
      },
      502,
    );
  }
  const summary = data.summary as Record<string, unknown>;
  const salesDecision = "decision_coverage_rate" in summary || "decision_eligible_count" in summary;
  if (salesDecision) await load(decisionViews);

  return jsonResponse({
    generated_at: new Date().toISOString(),
    capabilities: { sales_decision: salesDecision },
    data,
    errors,
  });
}
