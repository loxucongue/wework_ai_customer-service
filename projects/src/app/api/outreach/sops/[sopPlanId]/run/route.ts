import { NextRequest } from "next/server";
import { proxyAiPathsAdmin } from "../../../../_lib/ai-paths";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ sopPlanId: string }> }
) {
  const { sopPlanId } = await params;
  const body = await request.text();
  return proxyAiPathsAdmin(`/admin/outreach/sops/${encodeURIComponent(sopPlanId)}/run`, {
    method: "POST",
    body,
  });
}
