import { NextRequest } from "next/server";
import { proxyAiPathsAdmin } from "../../../../_lib/ai-paths";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ customerId: string }> }
) {
  const { customerId } = await params;
  const body = await request.text();
  return proxyAiPathsAdmin(
    `/admin/outreach/customers/${encodeURIComponent(customerId)}/refresh-conversation`,
    {
      method: "POST",
      body,
    }
  );
}
