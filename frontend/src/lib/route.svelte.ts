// The hash routes of the hand-written page, unchanged.
export interface Route { name: string; id?: string; sub?: string; ids?: string[] }

function parse(hash: string): Route {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] === "recipes") return { name: "recipes", id: parts[1], sub: parts[2] };
  if (parts[0] === "history") return { name: "history", id: parts[1] };
  if (parts[0] === "compare") return { name: "compare", ids: parts.slice(1) };
  if (parts[0] === "console") return { name: "console" };
  return { name: "home" };
}

export const route = $state({ current: parse(location.hash), hash: location.hash });

window.addEventListener("hashchange", () => {
  // Read the address as it is now: hub.svelte.ts may have just taken a token out of it.
  route.current = parse(location.hash);
  route.hash = location.hash;
});
