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

  let { lines, marks = [], live = false }: { lines: Line[]; marks?: number[]; live?: boolean } = $props();

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

  function option(): echarts.EChartsCoreOption {
    const c = palette();
    const markLine = marks.length ? {
      silent: true, symbol: "none", label: { show: false },
      lineStyle: { color: c.axis, type: "dotted", width: 1, opacity: 0.6 },
      data: marks.map((x) => ({ xAxis: x })),
    } : undefined;
    return {
      animation: !live,
      animationDuration: 900,
      grid: { left: 44, right: 16, top: 16, bottom: 58 },
      xAxis: {
        type: "value", min: 0, max: (v: { max: number }) => Math.max(30, Math.ceil(v.max / 30) * 30),
        axisLabel: { color: c.axis, formatter: (v: number) => clock(v) },
        axisLine: { lineStyle: { color: c.grid } }, splitLine: { show: false },
        // The pointer line; no label, no tooltip: the reading is drawn by the page below.
        axisPointer: { show: true, snap: false, label: { show: false }, lineStyle: { color: c.text, opacity: 0.45 } },
      },
      yAxis: {
        type: "value", min: 0,
        axisLabel: { color: c.axis }, splitLine: { lineStyle: { color: c.grid } },
      },
      axisPointer: { triggerTooltip: false, triggerOn: "mousemove" },
      tooltip: { show: false },
      dataZoom: [
        { type: "inside", xAxisIndex: 0, filterMode: "none" },
        { type: "slider", xAxisIndex: 0, filterMode: "none", height: 18, bottom: 8,
          borderColor: c.grid, fillerColor: "rgba(224,164,88,0.15)", textStyle: { color: c.axis },
          labelFormatter: (v: number) => clock(v), handleStyle: { color: c.coffee } },
      ],
      series: shown.map((line, index) => ({
        id: line.key, type: "line", showSymbol: false, smooth: false, data: line.points,
        lineStyle: { color: c[line.kind], width: line.kind === "plan" ? 1.5 : 2.5,
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
