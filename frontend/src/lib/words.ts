// Small wordings used on more than one page.
import { MESSAGES } from "./messages";
import { t } from "./i18n.svelte";

export const stateName = (name: string) => (MESSAGES.en[`state.${name}`] ? t(`state.${name}`) : name);

// The hub reads "busy" for a machine it is connected to but has not heard
// from yet: one asleep when the hub started says nothing until it wakes.
// That is not "in use", and says so.
export const unheard = (s: { state: string; activity: number | null }) => s.state === "busy" && s.activity === null;
export const stateLabel = (s: { state: string; activity: number | null }) => (unheard(s) ? t("state.unheard") : stateName(s.state));
export const reasonText = (code: string | null) => (code && MESSAGES.en[`reason.${code}`] ? t(`reason.${code}`) : code ?? "");
export const stateDetail = (s: { state: string; error: string | null; brew: { recipe_name: string | null } | null; recipe: { name: string } | null }) =>
  s.state === "offline" ? reasonText(s.error) : (s.brew?.recipe_name ?? s.recipe?.name ?? "");
export const tankText = (enough: number | undefined) => (enough === 1 ? t("tank.ok") : enough === 0 ? t("tank.low") : "-");
