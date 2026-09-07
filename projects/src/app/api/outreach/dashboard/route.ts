import { NextRequest } from "next/server";

import {
  getAiPathsWorkerHealth,
  jsonResponse,
  proxyAiPathsAdmin,
  requireExternalApiKey,
} from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) return authError;

  const [dashboardResponse, workerResponse] = await Promise.all([
    proxyAiPathsAdmin(`/admin/outreach/dashboard?${request.nextUrl.searchParams.toString()}`),
    getAiPathsWorkerHealth().catch(() => null),
  ]);
  if (!dashboardResponse.ok) return dashboardResponse;

  const dashboard = await dashboardResponse.json() as Record<string, unknown>;
  let runtime: Record<string, unknown> = {};
  let runtimeSource = "unavailable";
  let runtimeError = "";
  if (workerResponse?.ok) {
    const health = await workerResponse.json() as Record<string, unknown>;
    runtime = isRecord(health.silence_outreach_worker) ? health.silence_outreach_worker : {};
    runtimeSource = "worker_service";
  } else {
    runtimeError = workerResponse ? `Worker API returned ${workerResponse.status}` : "Worker API unavailable";
  }
  return jsonResponse({
    ...dashboard,
    runtime,
    runtime_source: runtimeSource,
    runtime_error: runtimeError,
  });
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
