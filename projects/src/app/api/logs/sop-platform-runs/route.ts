import { NextRequest } from "next/server";
import { getAiPathsWorkerHealth, jsonResponse, listAiPathsSopPlatformRuns } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const search = request.nextUrl.searchParams;
  try {
    const response = await listAiPathsSopPlatformRuns({
      limit: search.get("limit") || "100",
      status: search.get("status") || "",
      log_version: search.get("log_version") || "",
      biz_type: search.get("biz_type") || "",
      task_id: search.get("task_id") || "",
      customer_id: search.get("customer_id") || "",
      external_userid: search.get("external_userid") || "",
      wechat: search.get("wechat") || "",
      query: search.get("query") || "",
      date_from: search.get("date_from") || "",
      date_to: search.get("date_to") || "",
      refresh_platform: search.get("refresh_platform") || "true",
    });
    const text = await response.text();
    if (!response.ok) {
      return jsonResponse({ error: `AI Paths API returned ${response.status}`, detail: text }, response.status);
    }
    const payload = JSON.parse(text) as Record<string, unknown>;
    const workerResponse = await getAiPathsWorkerHealth().catch(() => null);
    if (workerResponse?.ok) {
      const health = await workerResponse.json() as Record<string, unknown>;
      payload.worker = health.platform_sop_worker || {};
      payload.worker_source = "worker_service";
    } else {
      payload.worker = { running: null };
      payload.worker_source = "unavailable";
      payload.worker_error = workerResponse ? `Worker API returned ${workerResponse.status}` : "Worker API unavailable";
    }
    return jsonResponse(payload);
  } catch (error) {
    console.error("Failed to load third-party SOP batch logs:", error);
    return jsonResponse({ error: "Failed to load third-party SOP batch logs" }, 500);
  }
}
