import { NextRequest } from "next/server";
import { jsonResponse, proxyAiPathsAdmin } from "../../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const days = request.nextUrl.searchParams.get("days") || "2";
  const response = await proxyAiPathsAdmin(`/admin/sop-platform-wechat-scope?days=${encodeURIComponent(days)}`);
  const text = await response.text();
  if (!response.ok) {
    return jsonResponse({ error: `AI Paths API returned ${response.status}`, detail: text }, response.status);
  }
  return new Response(text, {
    status: response.status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

export async function PUT(request: NextRequest) {
  const payload = await request.json().catch(() => null);
  if (!payload) return jsonResponse({ error: "Invalid JSON payload" }, 400);
  const response = await proxyAiPathsAdmin("/admin/sop-platform-wechat-scope?days=2", {
    method: "PUT",
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  if (!response.ok) {
    return jsonResponse({ error: `AI Paths API returned ${response.status}`, detail: text }, response.status);
  }
  return new Response(text, {
    status: response.status,
    headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}
