import { NextRequest } from "next/server";

import { getV3Evaluation, jsonResponse, listV3Evaluations } from "../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const runId = request.nextUrl.searchParams.get("run_id") || "";
  try {
    const response = runId ? await getV3Evaluation(runId) : await listV3Evaluations();
    const text = await response.text();
    if (!response.ok) {
      return jsonResponse(
        { error: `V3 evaluation API returned ${response.status}`, detail: text },
        response.status,
      );
    }
    return new Response(text, {
      status: 200,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (error) {
    console.error("Failed to load V3 evaluations:", error);
    return jsonResponse({ error: "Failed to load V3 evaluations" }, 500);
  }
}
