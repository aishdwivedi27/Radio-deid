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
