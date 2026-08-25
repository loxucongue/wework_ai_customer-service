import { getAiPathsQuietBacklog, jsonResponse } from "../../../../_lib/ai-paths";

export async function GET(_: Request, context: { params: Promise<{ eventId: string }> }) {
  const { eventId } = await context.params;
  try {
    const response = await getAiPathsQuietBacklog(eventId);
    const text = await response.text();
    if (!response.ok) {
      return jsonResponse({ error: `AI Paths API returned ${response.status}`, detail: text }, response.status);
    }
    return new Response(text, { status: response.status, headers: { "Content-Type": "application/json; charset=utf-8" } });
  } catch (error) {
    console.error("Failed to load quiet backlog detail:", error);
    return jsonResponse({ error: "Failed to load quiet backlog detail" }, 500);
  }
}
