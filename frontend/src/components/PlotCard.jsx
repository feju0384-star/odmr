import { Box } from "@mantine/core";
import createPlotlyComponent from "react-plotly.js/factory";
import Plotly from "plotly.js-basic-dist-min";

const Plot = createPlotlyComponent(Plotly);

export function PlotCard({
  x = [],
  y = [],
  lineColor = "#5ad1ff",
  yTitle = "Signal",
  xTitle = "Axis",
}) {
  return (
    <Box className="plot-shell">
      <Plot
        data={[
          {
            x,
            y,
            type: "scatter",
            mode: "lines",
            line: { color: lineColor, width: 3 },
            hovertemplate: "%{x}<br>%{y:.6f}<extra></extra>",
          },
        ]}
        layout={{
          autosize: true,
          margin: { l: 58, r: 18, t: 18, b: 46 },
          paper_bgcolor: "rgba(0,0,0,0)",
          plot_bgcolor: "rgba(7,17,31,0.42)",
          font: { color: "#c8d7ef", family: "IBM Plex Mono, monospace" },
          xaxis: {
            title: xTitle,
            gridcolor: "rgba(122,167,255,0.12)",
            zerolinecolor: "rgba(122,167,255,0.12)",
          },
          yaxis: {
            title: yTitle,
            gridcolor: "rgba(122,167,255,0.12)",
            zerolinecolor: "rgba(122,167,255,0.12)",
          },
        }}
        config={{
          displaylogo: false,
          responsive: true,
          modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"],
        }}
        style={{ width: "100%", height: 340 }}
        useResizeHandler
      />
    </Box>
  );
}
