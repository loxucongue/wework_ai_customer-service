import { NextRequest } from "next/server";
import { getAiPathsSopEvent, jsonResponse, listAiPathsSopEvents } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const eventId = searchParams.get("event_id") || "";

  try {
    const response = eventId
      ? await getAiPathsSopEvent(eventId)
      : await listAiPathsSopEvents({
          limit: searchParams.get("limit") || "50",
          event_type: searchParams.get("event_type") || "",
          status: searchParams.get("status") || "",
          customer_id: searchParams.get("customer_id") || "",
          external_userid: searchParams.get("external_userid") || "",
          has_error: searchParams.get("has_error") || "",
        });
    const text = await response.text();

    if (!response.ok) {
      return jsonResponse(
        {
          error: `AI Paths API returned ${response.status}`,
          detail: text,
        },
        response.status
      );
    }

    return new Response(text, {
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  } catch (error) {
    console.error("Failed to load AI Paths SOP logs:", error);
    return jsonResponse({ error: "Failed to load AI Paths SOP logs" }, 500);
  }
}
