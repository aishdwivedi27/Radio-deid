export type ServerState = "checking" | "ok" | "down";

const LABELS: Record<ServerState, string> = {
  checking: "Checking the local server…",
  ok: "Local server is running",
  down: "Local server is not responding",
};

export function StatusBadge({ state }: { state: ServerState }) {
  return (
    <p role="status" data-state={state}>
      {LABELS[state]}
    </p>
  );
}
