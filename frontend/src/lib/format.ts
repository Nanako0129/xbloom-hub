// Numbers and times as the current dashboard shows them.
import { MESSAGES } from "./messages";
import { t, locale } from "./i18n.svelte";

export const clock = (seconds: number | null | undefined): string => {
  const s = Math.max(0, Math.round(seconds ?? 0));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
};
export const whole = (value: number | null | undefined): string => (value == null ? "-" : String(Math.round(value)));
export const when = (iso: string | null | undefined): string => {
  const d = new Date(iso ?? "");
  return isNaN(+d) ? "" : d.toLocaleString(locale(), { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" });
};
export const endingText = (code: string | null | undefined): string =>
  !code ? t("ending.running") : MESSAGES.en[`ending.${code}`] ? t(`ending.${code}`) : code;
