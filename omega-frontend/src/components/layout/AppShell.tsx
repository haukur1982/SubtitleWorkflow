"use client";

import Button from "@/components/common/Button";
import { useNavigation } from "@/store/navigation";
import NavigationBar from "./NavigationBar";
import LibraryView from "../views/LibraryView";
import PipelineView from "../views/PipelineView";
import DeliveryView from "../views/DeliveryView";
import ProgramDetailView from "../views/ProgramDetailView";

export default function AppShell() {
  const { activeView, selectedProgramId } = useNavigation();

  return (
    <div className="app-shell">
      <header className="app-header">
        <h1 className="app-title">Omega Pro</h1>
        <div className="app-header-actions">
          <Button variant="ghost">Import Media</Button>
          <Button>New Program</Button>
        </div>
      </header>

      <NavigationBar />

      <main className="app-content">
        {activeView === "library" && <LibraryView />}
        {activeView === "pipeline" && <PipelineView />}
        {activeView === "delivery" && <DeliveryView />}
      </main>

      {selectedProgramId && <ProgramDetailView programId={selectedProgramId} />}
    </div>
  );
}
