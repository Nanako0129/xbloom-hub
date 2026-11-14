// Everything the page does to the machine, and the rules the current
// dashboard keeps (docs/PLAN.md, safety invariants 1 and 3; S3c step 6):
//
// - one request at a time, shown in a pill while it runs;
// - Stop is not held behind that lock: a start can still be waiting for
//   its answer when the brew has begun (the hub serialises stops itself);
// - the start ticket stays in this module's memory, never in the address,
//   storage or a log; Start is offered only while the machine holds this
//   page's own load, told by the hub's public load_id;
// - a no_cup refusal keeps the ticket; any other outcome of a start spends it.
import { api, hub } from "./hub.svelte";
import { t } from "./i18n.svelte";
import type { RecipeInfo } from "./recipes";

export const work = $state({
  busy: false,
  stopping: false,
  text: "",
  flash: null as { text: string; bad: boolean } | null,
});

// Not reactive state: nothing renders it, so it cannot leak into the page.
let ticket: { slug: string; value: string; loadId: string } | null = null;
// Reactive mirror of "this page holds a ticket for load X", for ownLoad().
const held = $state({ loadId: null as string | null });

export function ownLoad(): boolean {
  const s = hub.state;
  return Boolean(held.loadId && s && s.state === "armed" && s.load_id === held.loadId);
}

export async function act(fn: () => Promise<unknown>, what?: string): Promise<void> {
  if (work.busy) return;
  work.busy = true; work.text = what ?? t("working"); work.flash = null;
  try { await fn(); } catch (error) { work.flash = { text: (error as Error).message, bad: true }; }
  work.busy = false;
}

export async function load(slug: string): Promise<void> {
  const loaded = await api<{ ticket: string; load_id: string }>("POST", "/api/machine/load", { slug });
  ticket = { slug, value: loaded.ticket, loadId: loaded.load_id };
  held.loadId = loaded.load_id;
  location.hash = "#/";
}

export async function start(): Promise<void> {
  try {
    await api("POST", "/api/machine/start", { ticket: ticket?.value });
    ticket = null; held.loadId = null;
  } catch (error) {
    // The hub keeps the ticket when it refuses for a missing cup; in every
    // other case it is spent or void, and so is ours.
    if ((error as { code?: string }).code !== "no_cup") { ticket = null; held.loadId = null; }
    throw error;
  }
}

export async function stop(): Promise<void> {
  if (work.stopping) return;
  work.stopping = true; work.flash = null;
  try { await api("POST", "/api/machine/stop"); } catch (error) { work.flash = { text: (error as Error).message, bad: true }; }
  work.stopping = false;
}

export const cancel = () => api("POST", "/api/machine/cancel");
export const release = () => api("POST", "/api/link/release");
export const acquire = () => api("POST", "/api/link/acquire");

// ---- the confirmation dialog --------------------------------------------------
// info: a recipe to show as the hub returned it (the start confirmation).
export interface Question { title: string; body?: string; yes: string; danger?: boolean; info?: RecipeInfo }
export const asking = $state({ question: null as Question | null });
let answer: ((yes: boolean) => void) | null = null;

export function ask(question: Question): Promise<boolean> {
  asking.question = question;
  return new Promise((resolve) => { answer = resolve; });
}
export function answered(yes: boolean): void {
  asking.question = null;
  answer?.(yes); answer = null;
}
