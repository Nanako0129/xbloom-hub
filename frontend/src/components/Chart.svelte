<script lang="ts" module>
  import * as echarts from "echarts/core";
  import { LineChart } from "echarts/charts";
  import { AxisPointerComponent, DataZoomComponent, GridComponent, MarkLineComponent, MarkPointComponent,
    ToolboxComponent } from "echarts/components";
  import { CanvasRenderer } from "echarts/renderers";

  // Only what these charts use, so the page carries no more of ECharts than that.
  // The toolbox is only the home of ECharts' drag-to-select zoom; it is never drawn.
  echarts.use([LineChart, GridComponent, DataZoomComponent, MarkLineComponent, MarkPointComponent, ToolboxComponent,
    AxisPointerComponent, CanvasRenderer]);

  export interface Line {
    key: string;
    kind: "water" | "coffee" | "plan";
    label: string;
    points: [number, number][];
    dashed?: boolean;
    gaps?: [number, number][];   // spans with no readings: the line breaks there, never a straight jump
  }
  export type { Mark } from "../lib/records";
</script>

<script lang="ts">
  import { onMount, type Snippet } from "svelte";
  import { t } from "../lib/i18n.svelte";
  import { clock, whole } from "../lib/format";
  import { valueAt, type Mark } from "../lib/records";

  // live: a brew still running, redrawn several times a second; no drawing animation (step 5 uses it).
  // upto: a replay; the measured curves are drawn only as far as this time and
  // the reading follows it. children: controls drawn between the chart and its reading.
  let { lines, marks = [], live = false, upto = null, children }:
    { lines: Line[]; marks?: Mark[]; live?: boolean; upto?: number | null; children?: Snippet } = $props();

  // Visual parameters, in one place.
  const GRID = { left: 44, right: 16, top: 34, bottom: 58 };   // room for the y labels, above the flags of the marks and below the slider
  const DRAW_MS = 900;          // the curves drawing in when a page arrives, as on the current dashboard
  const LINE_WIDTH = 2.5;       // the measured curves: the thing being read
  const PLAN_WIDTH = 1.5;       // the plan is a reference behind them
  const MARK_OPACITY = 0.6;     // event lines: visible, never louder than a curve
  const FLAG_SIZE = 14;         // a mark's flag is what is clicked: big enough for a finger
  const FLAG_Y = 14;            // flags sit in the strip above the plot, so no curve runs under one
  const FLAG_FONT = 11;
  // One look for each kind of event, so they read apart at a glance: pour
  // start a dotted line and a disc, pour end a solid line and a square,
  // re-zero a dashed coffee-coloured line and a triangle.
  const MARK_STYLE: Record<Mark["kind"], { symbol: string; line: "dotted" | "solid" | "dashed"; color: "axis" | "water" | "coffee" }> = {
    start: { symbol: "circle", line: "dotted", color: "axis" },
    end: { symbol: "rect", line: "solid", color: "water" },
    rezero: { symbol: "triangle", line: "dashed", color: "coffee" },
    grind: { symbol: "diamond", line: "dotted", color: "coffee" },   // the grinder, before any water
  };
  const POINTER_OPACITY = 0.45; // the line under the pointer
  const SLIDER_HEIGHT = 18;
  const SLIDER_FILL = 0.15;     // the zoom window, tinted with the coffee colour
  const MIN_SECONDS = 30;       // the shortest time axis, so a 10 s test brew is not stretched
  const ZOOMED_MIN_PERCENT = 0.5;   // a window this close to the whole counts as not zoomed

  let host: HTMLDivElement;
  let chart: echarts.ECharts | undefined;
  let hidden = $state<Record<string, boolean>>({});
  let at = $state<number | null>(null);          // the moment under the pointer, in seconds
  let pinned = $state<number | null>(null);      // the moment of a mark that was clicked
  // The zoom window as the slider's percentages: seconds follow a growing axis.
  let zoomPct = $state<[number, number] | null>(null);

  const shown = $derived(lines.filter((line) => !hidden[line.key] && line.points.length));
  // Inside a gap there is no reading: nothing is interpolated across it.
  const inGap = (line: Line, time: number) => (line.gaps ?? []).some(([from, to]) => time > from && time < to);
  const readingOf = (line: Line, time: number) => (inGap(line, time) ? null : valueAt(line.points, time));
  // Replaying: the measured curves end at `upto`, the plan stays whole.
  const drawn = $derived(upto === null ? shown : shown.map((line) => {
    if (line.kind === "plan") return line;
    const before = line.points.filter((p) => p[0] <= upto);
    const value = readingOf(line, upto);
    const last = before[before.length - 1];
    return { ...line, points: value !== null && (!last || last[0] < upto) && line.points[0][0] <= upto
      ? [...before, [upto, value] as [number, number]] : before };
  }));
  // The pointer first, then a mark that was clicked, then the replay.
  const readAt = $derived(at ?? pinned ?? upto);
  const reading = $derived(readAt === null ? [] : drawn.map((line) => ({
    line, value: readingOf(line, readAt),
  })));
  const pinnedMark = $derived(at === null && pinned !== null ? marks.find((m) => m.t === pinned) : undefined);
  // The axis is as long as the whole brew, whatever a replay has drawn so far.
  // Another brew's marks: a mark pinned on the previous one means nothing here.
  $effect(() => { marks; pinned = null; });
  const xMax = $derived.by(() => {
    const top = Math.max(MIN_SECONDS, ...shown.map((l) => l.points[l.points.length - 1][0]), ...marks.map((m) => m.t));
    return Math.ceil(top / MIN_SECONDS) * MIN_SECONDS;
  });
  const zoom = $derived(zoomPct ? [(zoomPct[0] / 100) * xMax, (zoomPct[1] / 100) * xMax] as [number, number] : null);
  const kinds = $derived([...new Set(marks.map((m) => m.kind))]);

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
    const style = (m: Mark) => MARK_STYLE[m.kind];
    const lineData = marks.map((m) => ({ xAxis: m.t, lineStyle: { color: c[style(m).color], type: style(m).line } }));
    if (pinned !== null) lineData.push({ xAxis: pinned, lineStyle: { color: c.text, type: "solid" } });
    const markLine = lineData.length ? {
      silent: true, symbol: "none", label: { show: false },
      lineStyle: { width: 1, opacity: MARK_OPACITY }, data: lineData,
    } : undefined;
    // The flags: where a mark is clicked (the line itself is a pixel wide). `t`
    // rides along in the data and comes back with the click.
    const markPoint = marks.length ? {
      silent: false, cursor: "pointer", symbolSize: FLAG_SIZE,
      label: { show: true, position: "right", formatter: "{b}", color: c.axis, fontSize: FLAG_FONT },
      data: marks.map((m) => ({ name: m.label, t: m.t, coord: [m.t, 0], y: FLAG_Y, symbol: style(m).symbol,
        itemStyle: { color: c[style(m).color] } })),
    } : undefined;
    return {
      animation: !live && upto === null,
      animationDuration: DRAW_MS,
      grid: GRID,
      xAxis: {
        // Marks count too: one after the last reading is not cut off.
        type: "value", min: 0, max: xMax,
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
      // Hidden, and only there to host the drag-to-select zoom (see takeGlobalCursor below).
      toolbox: { show: true, left: -100, top: -100, itemSize: 1, showTitle: false,
        feature: { dataZoom: { show: true, yAxisIndex: "none", filterMode: "none", icon: { zoom: "path://M0,0", back: "path://M0,0" } } } },
      series: drawn.map((line, index) => ({
        id: line.key, type: "line", showSymbol: false, smooth: false, data: withGaps(line.points, line.gaps),
        lineStyle: { color: c[line.kind], width: line.kind === "plan" ? PLAN_WIDTH : LINE_WIDTH,
                     type: line.kind === "plan" || line.dashed ? "dashed" : "solid" },
        itemStyle: { color: c[line.kind] },
        emphasis: { disabled: true },
        markLine: index === 0 ? markLine : undefined,
        markPoint: index === 0 ? markPoint : undefined,
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
    // A drag in progress: the select cursor is not taken again under it.
    host.addEventListener("pointerdown", () => { dragging = true; });
    window.addEventListener("pointerup", () => { dragging = false; });
    // Double click: the whole brew again.
    host.addEventListener("dblclick", reset);
    chart.on("datazoom", () => {
      const z = (chart?.getOption() as any)?.dataZoom?.[0];
      zoomPct = z && (z.start > ZOOMED_MIN_PERCENT || z.end < 100 - ZOOMED_MIN_PERCENT) ? [z.start, z.end] : null;
    });
    chart.on("click", (event: any) => {
      if (event.componentType === "markPoint" && typeof event.data?.t === "number") pinned = event.data.t;
    });
    const resize = new ResizeObserver(() => chart?.resize());
    resize.observe(host);
    return () => { resize.disconnect(); chart?.dispose(); chart = undefined; };
  });

  function reset() {
    pinned = null;
    chart?.dispatchAction({ type: "dataZoom", start: 0, end: 100 });
  }

  // A pointer that can drag precisely drags to select a time range; on a
  // touch screen a drag stays the page's scroll, and the pinch and the slider zoom.
  const SELECTS = typeof matchMedia === "function" && matchMedia("(pointer: fine)").matches;
  let dragging = false;

  // New data replaces the series; the zoom and the pointer stay where they are.
  $effect(() => {
    if (!chart) return;
    chart.setOption(option(), { replaceMerge: ["series"] });
    // setOption drops the select cursor, so it is taken again, but never in
    // the middle of a drag: a live or replaying chart redraws many times a second.
    if (SELECTS && !dragging) chart.dispatchAction({ type: "takeGlobalCursor", key: "dataZoomSelect", dataZoomSelectActive: true });
  });

  // A missing value at the start of each gap: ECharts breaks the line there.
  function withGaps(points: [number, number][], gaps: [number, number][] | undefined): (number | null)[][] {
    if (!gaps?.length) return points;
    const out: (number | null)[][] = [];
    let g = 0;
    const sorted = [...gaps].sort((a, b) => a[0] - b[0]);
    for (const point of points) {
      while (g < sorted.length && point[0] > sorted[g][0]) { out.push([sorted[g][0], null]); g++; }
      out.push(point);
    }
    return out;
  }

  const unit = (line: Line) => (line.kind === "coffee" ? "g" : "ml");
</script>

<div class="chart echart">
  <div class="echart-canvas" bind:this={host} role="img" aria-label={t("chart.label")}></div>
  {@render children?.()}
  <div class="reading num" aria-live="off">
    {#if readAt !== null}
      <span class="time">{clock(readAt)}</span>
      {#if pinnedMark}<strong class="mark-name">{pinnedMark.title}</strong>{/if}
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
    {#each kinds as kind (kind)}
      <span class="key-mark"><i class="mark-{kind}"></i>{t(`mark.key_${kind}`)}</span>
    {/each}
    {#if zoom}
      <button type="button" class="key zoomed" onclick={reset}>{t("chart.reset", { range: `${clock(zoom[0])}–${clock(zoom[1])}` })}</button>
    {/if}
  </div>
</div>
