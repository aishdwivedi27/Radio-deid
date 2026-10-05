import { useEffect, useState } from "react";
import { getHealth } from "../api/client";
import { StatusBadge, type ServerState } from "../components/StatusBadge";

// Placeholder home page (Phase 0). Screens arrive in Phase 6.
export function HomePage() {
  const [state, setState] = useState<ServerState>("checking");

  useEffect(() => {
    getHealth()
      .then(() => setState("ok"))
      .catch(() => setState("down"));
  }, []);

  return (
    <main>
      <h1>De-identification Station</h1>
      <p className="banner">Pre-approval mode — synthetic data only.</p>
      <StatusBadge state={state} />
    </main>
  );
}
