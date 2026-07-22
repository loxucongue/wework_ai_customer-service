"use client";

import { useState } from "react";
import { BookOpenText, MessagesSquare } from "lucide-react";

import { PrecisionQaPlaybookWorkbench } from "@/components/sop/precision-qa-playbook-workbench";
import { SopReplyPackWorkbench } from "@/components/sop/sop-reply-pack-workbench";
import { Button } from "@/components/ui/button";

export type SopConfigSection = "packs" | "precision";

export function SopConfigWorkbench() {
  const [section, setSection] = useState<SopConfigSection>("packs");

  return (
    <div>
      <div className="sticky top-0 z-50 border-b bg-background px-5 py-2">
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant={section === "packs" ? "default" : "ghost"}
            onClick={() => setSection("packs")}
          >
            <MessagesSquare />
            话术包
          </Button>
          <Button
            type="button"
            size="sm"
            variant={section === "precision" ? "default" : "ghost"}
            onClick={() => setSection("precision")}
          >
            <BookOpenText />
            精准回复
          </Button>
        </div>
      </div>
      <div className={section === "packs" ? "block" : "hidden"}>
        <SopReplyPackWorkbench />
      </div>
      <div className={section === "precision" ? "block" : "hidden"}>
        <PrecisionQaPlaybookWorkbench />
      </div>
    </div>
  );
}
