import { NextRequest } from "next/server";
import { jsonResponse, listAiPathsSopPlatformTasks } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const search = request.nextUrl.searchParams;
  try {
    const response = await listAiPathsSopPlatformTasks({
      limit: search.get("limit") || "100",
      bucket: search.get("bucket") || "",
      decision: search.get("decision") || "",
      task_id: search.get("task_id") || "",
      customer_id: search.get("customer_id") || "",
      refresh_platform: search.get("refresh_platform") || "true",
    });
    const text = await response.text();
    if (!response.ok) {
      return jsonResponse({ error: `AI Paths API returned ${response.status}`, detail: text }, response.status);
    }
    return new Response(text, {
      status: response.status,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (error) {
    console.error("Failed to load third-party SOP task logs:", error);
    return jsonResponse({ error: "Failed to load third-party SOP task logs" }, 500);
  }
}
