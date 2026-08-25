import { NextRequest } from "next/server";
import { jsonResponse, listAiPathsQuietBacklog } from "../../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const search = request.nextUrl.searchParams;
  try {
    const response = await listAiPathsQuietBacklog({
      local_date: search.get("local_date") || "",
      status: search.get("status") || "",
      customer_id: search.get("customer_id") || "",
      wechat: search.get("wechat") || "",
      limit: search.get("limit") || "200",
    });
    const text = await response.text();
    if (!response.ok) {
      return jsonResponse({ error: `AI Paths API returned ${response.status}`, detail: text }, response.status);
    }
    return new Response(text, { status: response.status, headers: { "Content-Type": "application/json; charset=utf-8" } });
  } catch (error) {
    console.error("Failed to load quiet backlog logs:", error);
    return jsonResponse({ error: "Failed to load quiet backlog logs" }, 500);
  }
}
