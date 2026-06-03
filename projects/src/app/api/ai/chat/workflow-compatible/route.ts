import { NextRequest } from "next/server";
import {
  parseWorkflowCompatibleRequest,
  proxyAiPathsChatWorkflowCompatible,
  requireExternalApiKey,
} from "../../../_lib/ai-paths";

export async function POST(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  const { body, error } = await parseWorkflowCompatibleRequest(request);
  if (error) {
    return error;
  }
  if (!body) {
    return new Response(
      JSON.stringify({
        code: 400,
        msg: "invalid workflow-compatible chat request",
        data: { error: "invalid workflow-compatible chat request" },
      }),
      {
        status: 400,
        headers: { "Content-Type": "application/json; charset=utf-8" },
      }
    );
  }

  return proxyAiPathsChatWorkflowCompatible(body);
}
