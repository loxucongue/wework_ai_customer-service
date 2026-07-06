import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../../_lib/ai-paths";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ planId: string }> }
) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  const { planId } = await params;
  return proxyAiPathsAdmin(`/admin/outreach/plans/${encodeURIComponent(planId)}`);
}
