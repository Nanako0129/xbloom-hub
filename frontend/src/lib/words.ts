// Small wordings used on more than one page.
import { MESSAGES } from "./messages";
import { t } from "./i18n.svelte";

export const stateName = (name: string) => (MESSAGES.en[`state.${name}`] ? t(`state.${name}`) : name);
export const reasonText = (code: string | null) => (code && MESSAGES.en[`reason.${code}`] ? t(`reason.${code}`) : code ?? "");
export const cupText = (cup: boolean | null) => (cup === true ? t("cup.on") : cup === false ? t("cup.off") : t("cup.unknown"));
export const stateDetail = (s: { state: string; error: string | null; brew: { recipe_name: string | null } | null; recipe: { name: string } | null }) =>
  s.state === "offline" ? reasonText(s.error) : (s.brew?.recipe_name ?? s.recipe?.name ?? "");
export const tankText = (enough: number | undefined) => (enough === 1 ? t("tank.ok") : enough === 0 ? t("tank.low") : "-");
