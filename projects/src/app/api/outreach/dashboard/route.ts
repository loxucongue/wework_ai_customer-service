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

  const [dashboardResponse, workerRuntime] = await Promise.all([
    proxyAiPathsAdmin(`/admin/outreach/dashboard?${request.nextUrl.searchParams.toString()}`),
    loadWorkerRuntime(),
  ]);
  if (!dashboardResponse.ok) return dashboardResponse;

  const dashboard = await dashboardResponse.json() as Record<string, unknown>;
  return jsonResponse({
    ...dashboard,
    ...workerRuntime,
  });
}

async function loadWorkerRuntime() {
  try {
    const response = await getAiPathsWorkerHealth();
    if (!response.ok) {
      return {
        runtime: {},
        runtime_source: "unavailable",
        runtime_error: `Worker API returned ${response.status}`,
      };
    }
    const health = await response.json() as Record<string, unknown>;
    return {
      runtime: isRecord(health.silence_outreach_worker) ? health.silence_outreach_worker : {},
      runtime_source: "worker_service",
      runtime_error: "",
    };
  } catch {
    return {
      runtime: {},
      runtime_source: "unavailable",
      runtime_error: "Worker API unavailable",
    };
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}
