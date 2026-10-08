import { useEffect, useState } from "react";
import { getHealth, getStatus, type Status } from "../api/client";
import { PreApprovalBanner } from "../components/PreApprovalBanner";
import { StatusBadge, type ServerState } from "../components/StatusBadge";

// Placeholder home page (Phase 0). Screens (login, setup, admin, custodian) arrive in Phase 6.
export function HomePage() {
  const [state, setState] = useState<ServerState>("checking");
  const [status, setStatus] = useState<Status | null>(null);

  useEffect(() => {
    getHealth()
      .then(() => setState("ok"))
      .catch(() => setState("down"));
    getStatus()
      .then(setStatus)
      .catch(() => setStatus(null));
  }, []);

  return (
    <main>
      <h1>De-identification Station</h1>
      <PreApprovalBanner preapproval={status ? status.preapproval : null} text={status?.banner} />
      {status?.setup_required ? <p>First-run setup has not been completed.</p> : null}
      <StatusBadge state={state} />
    </main>
  );
}
