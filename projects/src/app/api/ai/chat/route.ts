import { NextRequest } from "next/server";
import { parseChatRequest, proxyAiPathsChatRaw, requireExternalApiKey } from "../../_lib/ai-paths";

export async function POST(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  const { body, error } = await parseChatRequest(request);
  if (error) {
    return error;
  }
  if (!body) {
    return new Response(JSON.stringify({ error: "invalid chat request" }), {
      status: 400,
      headers: { "Content-Type": "application/json; charset=utf-8" },
    });
  }

  return proxyAiPathsChatRaw(body);
}
