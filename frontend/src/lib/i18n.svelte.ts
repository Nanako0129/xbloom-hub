import { LANGUAGE_NAMES, MESSAGES } from "./messages";

export { LANGUAGE_NAMES };

const LANG_KEY = "xbloom-hub-lang";

function pick(): string {
  let saved: string | null = null;
  try { saved = localStorage.getItem(LANG_KEY); } catch { /* private mode */ }
  if (saved && MESSAGES[saved]) return saved;
  for (const tag of navigator.languages ?? [navigator.language ?? "en"]) {
    if (/^zh\b/i.test(tag)) return "zh-Hant";
    const base = tag.split("-")[0];
    if (MESSAGES[base]) return base;
  }
  return "en";
}

// Reactive: anything that calls t() redraws when the language changes.
export const language = $state({ code: pick() });

export function setLanguage(code: string): void {
  if (!MESSAGES[code]) return;
  language.code = code;
  document.documentElement.lang = code;
  try { localStorage.setItem(LANG_KEY, code); } catch { /* private mode */ }
}

// A missing key shows as the key itself, on purpose: it gets noticed.
export function t(key: string, values?: Record<string, string | number>): string {
  const text = MESSAGES[language.code][key] ?? MESSAGES.en[key] ?? key;
  return values ? text.replace(/\{(\w+)\}/g, (_, name) => String(values[name] ?? "")) : text;
}

export const locale = (): string => (language.code === "zh-Hant" ? "zh-TW" : language.code);
