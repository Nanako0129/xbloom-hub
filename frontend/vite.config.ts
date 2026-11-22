import { defineConfig, type Plugin } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// The packages whose code may end up in the page (docs/PLAN.md, S3c, C2).
// The page can start a brew, so anything else in the bundle fails the build. Svelte's runtime, ECharts and what they need at
// run time; the rest of the tree only builds.
const ALLOWED = new Set(["svelte", "clsx", "esm-env", "echarts", "zrender", "tslib"]);

function packageOf(id: string): string | null {
  const at = id.lastIndexOf("/node_modules/");
  if (at < 0) return null;
  const parts = id.slice(at + "/node_modules/".length).split("/");
  return parts[0].startsWith("@") ? `${parts[0]}/${parts[1]}` : parts[0];
}

function bundleAllowList(): Plugin {
  return {
    name: "xbloom-bundle-allow-list",
    apply: "build",
    generateBundle(_, bundle) {
      const found = new Set<string>();
      for (const chunk of Object.values(bundle)) {
        if (chunk.type !== "chunk") continue;
        for (const id of Object.keys(chunk.modules)) {
          const name = packageOf(id.replace(/^\0/, ""));
          if (name) found.add(name);
        }
      }
      const outside = [...found].filter((name) => !ALLOWED.has(name));
      if (outside.length) this.error(`packages outside the allow-list in the page: ${outside.join(", ")}`);
    },
  };
}

export default defineConfig({
  plugins: [svelte(), bundleAllowList()],
  // Relative asset paths: the build does not depend on the address it is served at.
  base: "./",
  build: {
    outDir: "../src/xbloom_hub/web",
    emptyOutDir: true,          // no stale chunk survives a rebuild (C12)
    assetsInlineLimit: 0,       // no data: URI the CSP would refuse (C13)
    sourcemap: false,
    target: "es2022",
  },
});
