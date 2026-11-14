// The page's one way to the hub: the token, requests, the state stream.
// docs/PLAN.md, S3c: the token arrives once in the address, is kept in
// localStorage, and is sent only in the Authorization header (C10).

const TOKEN_KEY = "xbloom-hub-token";

// Before anything reads the address: take the token out of it.
(function takeToken() {
  const match = location.hash.match(/^#t=([A-Za-z0-9_\-.~+/=]+)$/);
  if (!match) return;
  try { localStorage.setItem(TOKEN_KEY, match[1]); } catch { /* private mode */ }
  history.replaceState(null, "", location.pathname + "#/");
})();

export function hasToken(): boolean {
  try { return Boolean(localStorage.getItem(TOKEN_KEY)); } catch { return false; }
}

function token(): string {
  try { return localStorage.getItem(TOKEN_KEY) ?? ""; } catch { return ""; }
}

export class HubError extends Error {
  constructor(message: string, readonly code?: string, readonly fields?: Record<string, string>) {
    super(message);
  }
}

// Paths are absolute and under /api only: the page may be served from /next/.
export async function api<T = unknown>(method: string, path: string, body?: unknown,
  describe?: (code: string | undefined, status: number) => string): Promise<T> {
  if (!path.startsWith("/api/")) throw new Error(`not a hub path: ${path}`);
  const headers: Record<string, string> = {};
  const options: RequestInit = { method, headers };
  if (method !== "GET" || path.startsWith("/api/community")) headers.Authorization = `Bearer ${token()}`;
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response: Response;
  try { response = await fetch(path, options); } catch { throw new HubError(describe?.("no_hub", 0) ?? "no hub", "no_hub"); }
  let data: any = null;
  try { data = await response.json(); } catch { /* no body */ }
  if (!response.ok) {
    const code = data?.error;
    throw new HubError(describe?.(code, response.status) ?? `HTTP ${response.status}`, code, data?.fields);
  }
  return data as T;
}

export interface Snapshot {
  state: string;
  activity: number | null;
  weight_g: number | null;
  water_ml: number | null;
  cup: boolean | null;
  recipe: { slug: string; name: string } | null;
  brew: { id: string; elapsed_s: number; pour: number | null; slug: string | null; recipe_name: string | null } | null;
  error: string | null;
  load_id: string | null;
  water_enough?: number;
  fw_version?: string;
  [other: string]: unknown;
}

// What the hub reports, as it arrives; and the last changes, for the console.
export const hub = $state({
  state: null as Snapshot | null,
  reachable: false,
  log: [] as { at: Date; snap: Snapshot }[],
});

export function listen(): void {
  const source = new EventSource("/api/events");
  source.onmessage = (event) => {
    const next = JSON.parse(event.data) as Snapshot;
    const before = hub.state;
    hub.reachable = true;
    if (!before || before.state !== next.state || before.error !== next.error || before.activity !== next.activity) {
      hub.log.push({ at: new Date(), snap: next });
      if (hub.log.length > 200) hub.log.shift();
    }
    hub.state = next;
  };
  source.onerror = () => { hub.reachable = false; };
}
