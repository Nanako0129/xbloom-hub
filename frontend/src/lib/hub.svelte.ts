// The page's one way to the hub: requests and the state stream.
// No token and no pairing (docs/PLAN.md, "No pairing", 2026-11-22): the hub
// answers only its allowed networks, and every request that could change
// something carries the X-Hub-Request header, which a page on another site
// cannot add without a preflight the hub never answers.

import { MESSAGES } from "./messages";
import { t } from "./i18n.svelte";

// A device paired before then still holds the old token: wipe it.
try { localStorage.removeItem("xbloom-hub-token"); } catch { /* private mode */ }

export class HubError extends Error {
  constructor(message: string, readonly code?: string, readonly fields?: Record<string, string>) {
    super(message);
  }
}

// The hub's error as the person reads it: its own words when the dictionary
// has them, else the status.
function describeError(code: string | undefined, status: number): string {
  return code && MESSAGES.en[`error.${code}`] ? t(`error.${code}`) : status ? t("error.http", { status }) : t("error.no_hub");
}

// Paths are absolute and under /api only, whatever address the page has.
export async function api<T = unknown>(method: string, path: string, body?: unknown,
  describe: (code: string | undefined, status: number) => string = describeError): Promise<T> {
  if (!path.startsWith("/api/")) throw new Error(`not a hub path: ${path}`);
  const headers: Record<string, string> = {};
  const options: RequestInit = { method, headers };
  if (method !== "GET" || path.startsWith("/api/community")) headers["X-Hub-Request"] = "1";
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response: Response;
  try { response = await fetch(path, options); } catch { throw new HubError(describe("no_hub", 0), "no_hub"); }
  let data: any = null;
  try { data = await response.json(); } catch { /* no body */ }
  if (!response.ok) {
    const code = data?.error;
    throw new HubError(describe(code, response.status), code, data?.fields);
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
  brew: { id: string; elapsed_s: number; pour: number | null; slug: string | null; recipe_name: string | null; gaps?: number } | null;
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

// Others that follow every state as it arrives (the live brew).
const followers: ((next: Snapshot, before: Snapshot | null) => void)[] = [];
export function follow(fn: (next: Snapshot, before: Snapshot | null) => void): void { followers.push(fn); }

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
    // After the state itself: a follower that fails must not hold it back.
    for (const fn of followers) {
      try { fn(next, before); } catch (error) { console.error("a state follower failed", error); }
    }
  };
  source.onerror = () => { hub.reachable = false; };
}
