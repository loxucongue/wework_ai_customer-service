import { getAiPathsWorkerHealth, jsonResponse } from "../../_lib/ai-paths";

export async function GET() {
  const response = await getAiPathsWorkerHealth().catch(() => null);
  if (!response?.ok) {
    return jsonResponse({
      worker: { running: null },
      worker_source: "unavailable",
      worker_error: response ? `Worker API returned ${response.status}` : "Worker API unavailable",
    });
  }
  const health = await response.json() as Record<string, unknown>;
  return jsonResponse({
    worker: health.platform_sop_worker || {},
    worker_source: "worker_service",
  });
}
