// Typed API client. Pages call these functions; they never call fetch directly (CLAUDE.md, Architecture).

export interface Health {
  status: "ok";
}

export async function getHealth(fetchImpl: typeof fetch = fetch): Promise<Health> {
  const response = await fetchImpl("/api/health", { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`health check failed: HTTP ${response.status}`);
  }
  return (await response.json()) as Health;
}

// GET /api/status (public): first-run and pre-approval flags; `banner` is set while in pre-approval mode.
export interface Status {
  setup_required: boolean;
  setup_step: string;
  preapproval: boolean;
  banner: string | null;
}

export async function getStatus(fetchImpl: typeof fetch = fetch): Promise<Status> {
  const response = await fetchImpl("/api/status", { credentials: "same-origin" });
  if (!response.ok) {
    throw new Error(`status check failed: HTTP ${response.status}`);
  }
  return (await response.json()) as Status;
}
