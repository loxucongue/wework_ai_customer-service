import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../_lib/ai-paths";

export async function GET(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  return proxyAiPathsAdmin(`/admin/outreach/candidates?${request.nextUrl.searchParams.toString()}`);
}
