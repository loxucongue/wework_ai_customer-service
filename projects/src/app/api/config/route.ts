import { NextRequest } from "next/server";

const CONFIG_WORKFLOW_ID = "7641872030061117450";

export async function POST(request: NextRequest) {
  const body = await request.json();
  const { input } = body as { input: string };

  if (!input) {
    return new Response(
      JSON.stringify({ error: "input is required" }),
      { status: 400, headers: { "Content-Type": "application/json" } }
    );
  }

  const cozeApiToken = process.env.COZE_WORKLOAD_API_TOKEN;
  const cozeApiBase = process.env.COZE_API_BASE_URL || "https://api.coze.cn";

  if (!cozeApiToken) {
    return new Response(
      JSON.stringify({ error: "COZE_WORKLOAD_API_TOKEN is not configured" }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }

  const headers: Record<string, string> = {
    Authorization: `Bearer ${cozeApiToken}`,
    "Content-Type": "application/json",
  };

  const extraHeaders = process.env.COZE_EXTRA_HEADERS || "";
  for (const pair of extraHeaders.split(";")) {
    const idx = pair.indexOf("=");
    if (idx > 0) {
      headers[pair.slice(0, idx).trim()] = pair.slice(idx + 1).trim();
    }
  }

  try {
    const response = await fetch(`${cozeApiBase}/v1/workflow/run`, {
      method: "POST",
      headers,
      body: JSON.stringify({
        workflow_id: CONFIG_WORKFLOW_ID,
        parameters: { input },
      }),
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error("Config workflow API error:", response.status, errorText);
      return new Response(
        JSON.stringify({
          error: `Workflow API returned ${response.status}`,
          detail: errorText,
        }),
        { status: response.status, headers: { "Content-Type": "application/json" } }
      );
    }

    const result = await response.json();

    return new Response(JSON.stringify(result), {
      headers: { "Content-Type": "application/json" },
    });
  } catch (error) {
    console.error("Config workflow call failed:", error);
    return new Response(
      JSON.stringify({ error: "Failed to call config workflow" }),
      { status: 500, headers: { "Content-Type": "application/json" } }
    );
  }
}
