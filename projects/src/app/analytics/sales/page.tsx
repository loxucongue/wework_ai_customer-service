import { SalesStrategyDashboard } from "@/components/admin/sales-strategy-dashboard";
import type { Metadata } from "next";

export const dynamic = "force-dynamic";
export const metadata: Metadata = {
  title: "销售策略 BI | AI Paths",
  description: "查看 V3 意图、情绪、逼单、跟进序列和话术的使用与结果指标。",
};

export default function SalesStrategyAnalyticsPage() {
  return <SalesStrategyDashboard />;
}
