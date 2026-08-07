import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../../_lib/ai-paths";

export async function GET(
  request: NextRequest,
  context: { params: Promise<{ workflowRunId: string }> },
) {
  const authError = requireExternalApiKey(request);
  if (authError) return authError;
  const { workflowRunId } = await context.params;
  return proxyAiPathsAdmin(`/admin/outreach/first-day-runs/${encodeURIComponent(workflowRunId)}`);
}
