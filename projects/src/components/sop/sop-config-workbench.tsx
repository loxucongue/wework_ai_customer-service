import { PrecisionQaPlaybookWorkbench } from "@/components/sop/precision-qa-playbook-workbench";
import { SopReplyPackWorkbench } from "@/components/sop/sop-reply-pack-workbench";

export type SopConfigSection = "packs" | "precision";

export function SopConfigWorkbench({ section }: { section: SopConfigSection }) {
  return section === "packs" ? <SopReplyPackWorkbench /> : <PrecisionQaPlaybookWorkbench />;
}
