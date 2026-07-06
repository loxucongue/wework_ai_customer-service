import { NextRequest } from "next/server";
import { proxyAiPathsAdmin, requireExternalApiKey } from "../../../../_lib/ai-paths";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ taskId: string }> }
) {
  const authError = requireExternalApiKey(request);
  if (authError) {
    return authError;
  }

  const { taskId } = await params;
  return proxyAiPathsAdmin(`/admin/outreach/tasks/${encodeURIComponent(taskId)}/execute`, {
    method: "POST",
  });
}
