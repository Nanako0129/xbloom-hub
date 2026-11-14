<script lang="ts" module>
  import * as echarts from "echarts/core";
  import { LineChart } from "echarts/charts";
  import { AxisPointerComponent, DataZoomComponent, GridComponent, MarkLineComponent } from "echarts/components";
  import { CanvasRenderer } from "echarts/renderers";

  // Only what these charts use, so the page carries no more of ECharts than that.
  echarts.use([LineChart, GridComponent, DataZoomComponent, MarkLineComponent, AxisPointerComponent, CanvasRenderer]);

  export interface Line {
    key: string;
    kind: "water" | "coffee" | "plan";
    label: string;
    points: [number, number][];
    dashed?: boolean;
  }
</script>

<script lang="ts">
  import { onMount } from "svelte";
  import { t } from "../lib/i18n.svelte";
  import { clock, whole } from "../lib/format";
  import { valueAt } from "../lib/records";

  // live: a brew still running, redrawn several times a second; no drawing animation (step 5 uses it).
  let { lines, marks = [], live = false }: { lines: Line[]; marks?: number[]; live?: boolean } = $props();

  // Visual parameters, in one place.
  const GRID = { left: 44, right: 16, top: 16, bottom: 58 };   // room for the y labels and, below, the slider
  const DRAW_MS = 900;          // the curves drawing in when a page arrives, as on the current dashboard
  const LINE_WIDTH = 2.5;       // the measured curves: the thing being read
  const PLAN_WIDTH = 1.5;       // the plan is a reference behind them
  const MARK_OPACITY = 0.6;     // pour starts: visible, never louder than a curve
  const POINTER_OPACITY = 0.45; // the line under the pointer
  const SLIDER_HEIGHT = 18;
  const SLIDER_FILL = 0.15;     // the zoom window, tinted with the coffee colour
  const MIN_SECONDS = 30;       // the shortest time axis, so a 10 s test brew is not stretched

  let host: HTMLDivElement;
  let chart: echarts.ECharts | undefined;
  let hidden = $state<Record<string, boolean>>({});
  let at = $state<number | null>(null);          // the moment under the pointer, in seconds

  const shown = $derived(lines.filter((line) => !hidden[line.key] && line.points.length));
  const reading = $derived(at === null ? [] : shown.map((line) => ({
    line, value: valueAt(line.points, at as number),
  })));

  // Colours come from the stylesheet's tokens, so the canvas matches the page.
  function palette() {
    const css = getComputedStyle(document.documentElement);
    const token = (name: string) => css.getPropertyValue(name).trim();
    return { water: token("--water"), coffee: token("--coffee"), plan: token("--faint"),
             grid: token("--hairline"), axis: token("--muted"), text: token("--text") };
  }

  // A token colour (#rrggbb) at an opacity.
  function tint(hex: string, alpha: number): string {
    const n = parseInt(hex.replace("#", ""), 16);
    return Number.isNaN(n) ? hex : `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
  }

  function option(): echarts.EChartsCoreOption {
    const c = palette();
    const markLine = marks.length ? {
      silent: true, symbol: "none", label: { show: false },
      lineStyle: { color: c.axis, type: "dotted", width: 1, opacity: MARK_OPACITY },
      data: marks.map((x) => ({ xAxis: x })),
    } : undefined;
    return {
      animation: !live,
      animationDuration: DRAW_MS,
      grid: GRID,
      xAxis: {
        // Pour starts count too: a mark after the last reading is not cut off.
        type: "value", min: 0,
        max: (v: { max: number }) => { const top = Math.max(MIN_SECONDS, v.max, ...marks); return Math.ceil(top / MIN_SECONDS) * MIN_SECONDS; },
        axisLabel: { color: c.axis, formatter: (v: number) => clock(v) },
        axisLine: { lineStyle: { color: c.grid } }, splitLine: { show: false },
        // The pointer line; no label, no tooltip: the reading is drawn by the page below.
        axisPointer: { show: true, snap: false, label: { show: false }, lineStyle: { color: c.text, opacity: POINTER_OPACITY } },
      },
      yAxis: {
        type: "value", min: 0,
        axisLabel: { color: c.axis }, splitLine: { lineStyle: { color: c.grid } },
      },
      axisPointer: { triggerTooltip: false, triggerOn: "mousemove" },
      dataZoom: [
        // The page keeps its scroll: a plain wheel or swipe over the chart
        // scrolls the page. Zooming takes a pinch or ctrl and the wheel;
        // moving the window is the slider's.
        { type: "inside", xAxisIndex: 0, filterMode: "none",
          zoomOnMouseWheel: "ctrl", moveOnMouseWheel: false, moveOnMouseMove: false, preventDefaultMouseMove: false },
        { type: "slider", xAxisIndex: 0, filterMode: "none", height: SLIDER_HEIGHT, bottom: 8,
          borderColor: c.grid, fillerColor: tint(c.coffee, SLIDER_FILL), textStyle: { color: c.axis },
          labelFormatter: (v: number) => clock(v), handleStyle: { color: c.coffee } },
      ],
      series: shown.map((line, index) => ({
        id: line.key, type: "line", showSymbol: false, smooth: false, data: line.points,
        lineStyle: { color: c[line.kind], width: line.kind === "plan" ? PLAN_WIDTH : LINE_WIDTH,
                     type: line.kind === "plan" || line.dashed ? "dashed" : "solid" },
        itemStyle: { color: c[line.kind] },
        emphasis: { disabled: true },
        markLine: index === 0 ? markLine : undefined,
      })),
    };
  }

  onMount(() => {
    chart = echarts.init(host, null, { renderer: "canvas" });
    chart.on("updateAxisPointer", (event: any) => {
      const info = event.axesInfo?.find((a: { axisDim: string }) => a.axisDim === "x");
      at = info ? info.value : null;
    });
    host.addEventListener("pointerleave", () => { at = null; });
    // Double click: the whole brew again.
    host.addEventListener("dblclick", () => chart?.dispatchAction({ type: "dataZoom", start: 0, end: 100 }));
    const resize = new ResizeObserver(() => chart?.resize());
    resize.observe(host);
    return () => { resize.disconnect(); chart?.dispose(); chart = undefined; };
  });

  // New data replaces the series; the zoom and the pointer stay where they are.
  $effect(() => {
    if (!chart) return;
    chart.setOption(option(), { replaceMerge: ["series"] });
  });

  const unit = (line: Line) => (line.kind === "coffee" ? "g" : "ml");
</script>

<div class="chart echart">
  <div class="echart-canvas" bind:this={host} role="img" aria-label={t("chart.label")}></div>
  <div class="reading num" aria-live="off">
    {#if at !== null}
      <span class="time">{clock(at)}</span>
      {#each reading as { line, value } (line.key)}
        <span><i class={line.kind} class:dashed={line.dashed}></i>{line.label} {value === null ? "-" : `${whole(value)} ${unit(line)}`}</span>
      {/each}
    {:else}
      <span class="muted">{t("chart.hint")}</span>
    {/if}
  </div>
  <div class="legend">
    {#each lines as line (line.key)}
      <button type="button" class="key" class:off={hidden[line.key]} aria-pressed={!hidden[line.key]}
        onclick={() => { hidden = { ...hidden, [line.key]: !hidden[line.key] }; }}>
        <i class={line.kind} class:dashed={line.dashed}></i>{line.label}
      </button>
    {/each}
  </div>
</div>
