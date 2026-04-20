import { Box } from "@mantine/core";
import createPlotlyComponent from "react-plotly.js/factory";
import Plotly from "plotly.js-basic-dist-min";

const Plot = createPlotlyComponent(Plotly);

export function PlotCard({
  x = [],
  y = [],
  lineColor = "#5ad1ff",
  traces = null,
  yTitle = "Signal",
  xTitle = "Axis",
  yRange,
  xRange,
  yScale = "linear",
  xScale = "linear",
  shapes = [],
  annotations = [],
  uirevision = "plot",
  onRelayout,
  onClick,
}) {
  const hasEnoughPoints = Math.max(x.length, y.length) > 1;
  const plotData = Array.isArray(traces) && traces.length
    ? traces.map((trace) => ({
        type: "scatter",
        mode: Math.max((trace.x || []).length, (trace.y || []).length) > 1 ? "lines" : "lines+markers",
        line: { width: 3, color: trace.lineColor || lineColor, dash: trace.lineDash || "solid" },
        marker: { color: trace.lineColor || lineColor, size: 7 },
        connectgaps: true,
        hovertemplate: trace.hovertemplate || "%{x}<br>%{y:.6f}<extra></extra>",
        ...trace,
      }))
    : [
        {
          x,
          y,
          type: "scatter",
          mode: hasEnoughPoints ? "lines" : "lines+markers",
          line: { color: lineColor, width: 3 },
          marker: { color: lineColor, size: 7 },
          connectgaps: true,
          hovertemplate: "%{x}<br>%{y:.6f}<extra></extra>",
        },
      ];

  return (
    <Box className="plot-shell">
      <Plot
        data={plotData}
        layout={{
          uirevision,
          autosize: true,
          margin: { l: 58, r: 18, t: 18, b: 46 },
          paper_bgcolor: "rgba(0,0,0,0)",
          plot_bgcolor: "rgba(7,17,31,0.42)",
          font: { color: "#c8d7ef", family: "IBM Plex Mono, monospace" },
          shapes,
          annotations,
          showlegend: plotData.length > 1,
          clickmode: "event+select",
          legend: {
            orientation: "h",
            x: 0,
            y: 1.12,
            bgcolor: "rgba(0,0,0,0)",
          },
          xaxis: {
            title: xTitle,
            type: xScale,
            autorange: !xRange,
            range: xRange,
            gridcolor: "rgba(122,167,255,0.12)",
            zerolinecolor: "rgba(122,167,255,0.12)",
          },
          yaxis: {
            title: yTitle,
            type: yScale,
            autorange: !yRange,
            range: yRange,
            gridcolor: "rgba(122,167,255,0.12)",
            zerolinecolor: "rgba(122,167,255,0.12)",
          },
        }}
        config={{
          displaylogo: false,
          responsive: true,
          scrollZoom: true,
          modeBarButtonsToRemove: ["select2d", "lasso2d"],
        }}
        onRelayout={onRelayout}
        onClick={onClick}
        style={{ width: "100%", height: 340 }}
        useResizeHandler
      />
    </Box>
  );
}
