import { NextRequest } from "next/server";
import { proxyAiPathsSopEventRaw, requireExternalApiKey } from "../../../_lib/ai-paths";

export async function POST(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  const body = await request.text();
  return proxyAiPathsSopEventRaw(body);
}
