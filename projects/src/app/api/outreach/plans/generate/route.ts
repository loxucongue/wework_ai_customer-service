import { NextRequest } from "next/server";
import { proxyAiPathsAdmin } from "../../../_lib/ai-paths";

export async function POST(request: NextRequest) {
  const body = await request.text();
  return proxyAiPathsAdmin("/admin/outreach/plans/generate", {
    method: "POST",
    body,
  });
}
