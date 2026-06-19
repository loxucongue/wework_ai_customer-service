import { NextRequest } from "next/server";
import { proxyAiPathsAdmin } from "../../../_lib/ai-paths";

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ planId: string }> }
) {
  const { planId } = await params;
  return proxyAiPathsAdmin(`/admin/outreach/plans/${encodeURIComponent(planId)}`);
}
