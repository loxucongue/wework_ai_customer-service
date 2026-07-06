import { NextRequest } from "next/server";
import { getAiPathsRun, jsonResponse, listAiPathsRuns } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const searchParams = request.nextUrl.searchParams;
  const requestId = searchParams.get("request_id") || "";

  try {
    const response = requestId
      ? await getAiPathsRun(requestId)
      : await listAiPathsRuns({
          limit: searchParams.get("limit") || "50",
          customer_id: searchParams.get("customer_id") || "",
          conversation_id: searchParams.get("conversation_id") || "",
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

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      return jsonResponse(
        {
          error: "AI Paths API returned non-json response",
          detail: compactResponseText(text),
        },
        502
      );
    }

    return new Response(text, {
      status: response.status,
      headers: { "Content-Type": contentType || "application/json; charset=utf-8" },
    });
  } catch (error) {
    console.error("Failed to load AI Paths logs:", error);
    return jsonResponse({ error: "Failed to load AI Paths logs" }, 500);
  }
}

function compactResponseText(text: string) {
  return text.replace(/\s+/g, " ").slice(0, 1000);
}
