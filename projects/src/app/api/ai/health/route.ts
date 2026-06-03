import { callAiPathsHealth, jsonResponse } from "../../_lib/ai-paths";

export async function GET() {
  const now = new Date().toISOString();
  try {
    const response = await callAiPathsHealth();
    if (!response.ok) {
      return jsonResponse(
        {
          status: "error",
          service: "ai-paths",
          backend: "error",
          backend_status: response.status,
          time: now,
        },
        503
      );
    }

    return jsonResponse({
      status: "ok",
      service: "ai-paths",
      backend: "ok",
      auth_enabled: Boolean(process.env.AI_EXTERNAL_API_KEY),
      time: now,
    });
  } catch (error) {
    console.error("AI Paths health check failed:", error);
    return jsonResponse(
      {
        status: "error",
        service: "ai-paths",
        backend: "unreachable",
        time: now,
      },
      503
    );
  }
}
