import { NextRequest } from "next/server";
import { proxyAiPathsAdmin } from "../../../../_lib/ai-paths";

const ALLOWED = new Set(["activate", "pause", "resume", "cancel"]);

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ planId: string; action: string }> }
) {
  const { planId, action } = await params;
  if (!ALLOWED.has(action)) {
    return new Response(JSON.stringify({ error: "unsupported action" }), {
      status: 400,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  }
  return proxyAiPathsAdmin(`/admin/outreach/plans/${encodeURIComponent(planId)}/${action}`, {
    method: "POST",
  });
}
