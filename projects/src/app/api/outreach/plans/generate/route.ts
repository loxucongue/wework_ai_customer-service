import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../../_lib/ai-paths";

export async function POST(request: NextRequest) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  const body = await request.text();
  return proxyAiPathsAdmin("/admin/outreach/plans/generate", {
    method: "POST",
    body,
  });
}
