import { NextRequest } from "next/server";
import { jsonResponse, resendAiPathsSopPlatformTask } from "../../../../_lib/ai-paths";

export async function POST(_request: NextRequest, context: { params: Promise<{ taskId: string }> }) {
  const { taskId } = await context.params;
  try {
    const response = await resendAiPathsSopPlatformTask(taskId);
    const text = await response.text();
    if (!response.ok) {
      return jsonResponse({ error: `AI Paths API returned ${response.status}`, detail: text }, response.status);
    }
    return new Response(text, {
      status: response.status,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (error) {
    console.error("Failed to resend third-party SOP task:", error);
    return jsonResponse({ error: "Failed to resend third-party SOP task" }, 500);
  }
}
