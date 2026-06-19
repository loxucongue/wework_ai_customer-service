import { NextRequest } from "next/server";
import { proxyAiPathsAdmin } from "../../../../_lib/ai-paths";

export async function POST(
  _request: NextRequest,
  { params }: { params: Promise<{ taskId: string }> }
) {
  const { taskId } = await params;
  return proxyAiPathsAdmin(`/admin/outreach/tasks/${encodeURIComponent(taskId)}/execute`, {
    method: "POST",
  });
}
