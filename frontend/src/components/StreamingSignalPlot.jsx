import { Box } from "@mantine/core";
import { useEffect, useRef, useState } from "react";

const MARGIN = { top: 18, right: 18, bottom: 34, left: 58 };
const GRID_COLOR = "rgba(122,167,255,0.12)";
const TEXT_COLOR = "#c8d7ef";
const AXIS_COLOR = "rgba(122,167,255,0.18)";
const BACKGROUND = "rgba(7,17,31,0.42)";
const EMPTY_UNIT = { divisor: 1, label: "uV" };

function lowerBound(values, target, startIndex = 0) {
  let low = startIndex;
  let high = values.length;
  while (low < high) {
    const mid = Math.floor((low + high) / 2);
    if (values[mid] < target) {
      low = mid + 1;
    } else {
      high = mid;
    }
  }
  return low;
}

function pickEngineeringUnit(maxAbsUv) {
  if (maxAbsUv >= 1e6) {
    return { divisor: 1e6, label: "V" };
  }
  if (maxAbsUv >= 1e3) {
    return { divisor: 1e3, label: "mV" };
  }
  return EMPTY_UNIT;
}

function normalizeRange(min, max) {
  if (!Number.isFinite(min) || !Number.isFinite(max) || min === max) {
    return null;
  }
  return min < max ? [min, max] : [max, min];
}

function computeAutoRange(values) {
  if (!values.length) {
    return null;
  }
  const min = Math.min(...values);
  const max = Math.max(...values);
  if (!Number.isFinite(min) || !Number.isFinite(max)) {
    return null;
  }
  if (min === max) {
    const pad = Math.max(Math.abs(min) * 0.1, 1);
    return [min - pad, max + pad];
  }
  const pad = (max - min) * 0.12;
  return [min - pad, max + pad];
}

function resizeCanvas(canvas, width, height) {
  const dpr = window.devicePixelRatio || 1;
  const nextWidth = Math.max(1, Math.round(width * dpr));
  const nextHeight = Math.max(1, Math.round(height * dpr));
  if (canvas.width !== nextWidth || canvas.height !== nextHeight) {
    canvas.width = nextWidth;
    canvas.height = nextHeight;
  }
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  return dpr;
}

function formatTick(value) {
  const abs = Math.abs(value);
  if (abs >= 100) {
    return value.toFixed(0);
  }
  if (abs >= 10) {
    return value.toFixed(1);
  }
  if (abs >= 1) {
    return value.toFixed(2);
  }
  return value.toFixed(3);
}

export function StreamingSignalPlot({
  batch,
  metric,
  timeWindowSec,
  lineColor,
  yAxisAuto,
  manualYMin,
  manualYMax,
  resetKey,
  onMetaChange,
  yLabel,
  xLabel,
  height = 340,
}) {
  const wrapperRef = useRef(null);
  const canvasRef = useRef(null);
  const historyRef = useRef({ times: [], x_uv: [], y_uv: [], r_uv: [], start: 0 });
  const metaRef = useRef({ unitLabel: "uV", visiblePoints: 0, yRange: null });
  const [width, setWidth] = useState(0);

  useEffect(() => {
    const wrapper = wrapperRef.current;
    if (!wrapper) {
      return undefined;
    }
    const updateWidth = () => {
      const nextWidth = Math.max(0, Math.round(wrapper.getBoundingClientRect().width));
      setWidth((current) => (current === nextWidth ? current : nextWidth));
    };
    updateWidth();
    const timerA = window.setTimeout(updateWidth, 50);
    const timerB = window.setTimeout(updateWidth, 250);
    window.addEventListener("resize", updateWidth);
    let observer;
    if (typeof ResizeObserver !== "undefined") {
      observer = new ResizeObserver(() => updateWidth());
      observer.observe(wrapper);
    }
    return () => {
      window.clearTimeout(timerA);
      window.clearTimeout(timerB);
      window.removeEventListener("resize", updateWidth);
      observer?.disconnect();
    };
  }, []);

  useEffect(() => {
    historyRef.current = { times: [], x_uv: [], y_uv: [], r_uv: [], start: 0 };
  }, [resetKey]);

  useEffect(() => {
    const times = batch?.times_s ?? [];
    if (!times.length) {
      return;
    }
    const history = historyRef.current;
    const lastTime = history.times[history.times.length - 1];
    if (Number.isFinite(lastTime) && times[0] <= lastTime) {
      history.times = [];
      history.x_uv = [];
      history.y_uv = [];
      history.r_uv = [];
      history.start = 0;
    }
    history.times.push(...times.map((value) => Number(value)));
    history.x_uv.push(...(batch.x_uv ?? []).map((value) => Number(value)));
    history.y_uv.push(...(batch.y_uv ?? []).map((value) => Number(value)));
    history.r_uv.push(...(batch.r_uv ?? []).map((value) => Number(value)));

    const latest = history.times[history.times.length - 1];
    const keepAfter = latest - 305;
    while (history.start < history.times.length && history.times[history.start] < keepAfter) {
      history.start += 1;
    }
    if (history.start > 4096 && history.start > history.times.length / 4) {
      history.times = history.times.slice(history.start);
      history.x_uv = history.x_uv.slice(history.start);
      history.y_uv = history.y_uv.slice(history.start);
      history.r_uv = history.r_uv.slice(history.start);
      history.start = 0;
    }
  }, [batch]);

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrapper = wrapperRef.current;
    const measuredWidth = Math.max(width, Math.round(wrapper?.getBoundingClientRect().width || 0));
    if (!canvas || measuredWidth < 120) {
      return;
    }

    const context = canvas.getContext("2d");
    if (!context) {
      return;
    }
    const dpr = resizeCanvas(canvas, measuredWidth, height);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, measuredWidth, height);
    context.fillStyle = BACKGROUND;
    context.fillRect(0, 0, measuredWidth, height);

    const plot = {
      left: MARGIN.left,
      top: MARGIN.top,
      width: Math.max(1, measuredWidth - MARGIN.left - MARGIN.right),
      height: Math.max(1, height - MARGIN.top - MARGIN.bottom),
    };

    const history = historyRef.current;
    const end = history.times.length;
    const latest = end ? history.times[end - 1] : null;
    let unit = EMPTY_UNIT;
    let visiblePoints = 0;
    let yRange = null;

    context.strokeStyle = AXIS_COLOR;
    context.lineWidth = 1;
    context.strokeRect(plot.left, plot.top, plot.width, plot.height);
    context.font = "12px IBM Plex Mono, monospace";
    context.fillStyle = TEXT_COLOR;

    const xTickCount = 5;
    const yTickCount = 5;
    for (let index = 0; index <= xTickCount; index += 1) {
      const ratio = index / xTickCount;
      const x = plot.left + ratio * plot.width;
      context.strokeStyle = GRID_COLOR;
      context.beginPath();
      context.moveTo(x, plot.top);
      context.lineTo(x, plot.top + plot.height);
      context.stroke();
      const tickValue = -timeWindowSec + ratio * timeWindowSec;
      context.fillText(formatTick(tickValue), x - 10, plot.top + plot.height + 20);
    }

    if (latest !== null) {
      const windowStart = latest - timeWindowSec;
      const startIndex = lowerBound(history.times, windowStart, history.start);
      const visibleTimes = history.times.slice(startIndex, end);
      const visibleValuesRaw = history[metric].slice(startIndex, end);
      visiblePoints = visibleTimes.length;
      if (visiblePoints) {
        const maxAbsUv = Math.max(...visibleValuesRaw.map((item) => Math.abs(item)), 0);
        unit = pickEngineeringUnit(maxAbsUv);
        const scaledValues = visibleValuesRaw.map((item) => item / unit.divisor);
        const autoRange = computeAutoRange(scaledValues);
        const manualRange = normalizeRange(Number(manualYMin), Number(manualYMax));
        yRange = yAxisAuto ? autoRange : manualRange ?? autoRange;
        if (yRange) {
          const [yMin, yMax] = yRange;
          for (let index = 0; index <= yTickCount; index += 1) {
            const ratio = index / yTickCount;
            const y = plot.top + ratio * plot.height;
            context.strokeStyle = GRID_COLOR;
            context.beginPath();
            context.moveTo(plot.left, y);
            context.lineTo(plot.left + plot.width, y);
            context.stroke();
            const tickValue = yMax - ratio * (yMax - yMin);
            context.fillText(formatTick(tickValue), 8, y + 4);
          }

          const maxDrawPoints = Math.max(600, plot.width * 2);
          const stride = Math.max(1, Math.ceil(visiblePoints / maxDrawPoints));
          context.strokeStyle = lineColor;
          context.lineWidth = 2;
          context.beginPath();
          let started = false;
          for (let index = 0; index < visiblePoints; index += stride) {
            const relativeTime = visibleTimes[index] - latest;
            const x = plot.left + ((relativeTime + timeWindowSec) / timeWindowSec) * plot.width;
            const scaledValue = scaledValues[index];
            const y = plot.top + (1 - (scaledValue - yMin) / (yMax - yMin || 1)) * plot.height;
            if (!started) {
              context.moveTo(x, y);
              started = true;
            } else {
              context.lineTo(x, y);
            }
          }
          const lastValue = scaledValues[visiblePoints - 1];
          const lastX = plot.left + plot.width;
          const lastY = plot.top + (1 - (lastValue - yMin) / (yMax - yMin || 1)) * plot.height;
          if (!started) {
            context.moveTo(lastX, lastY);
          } else {
            context.lineTo(lastX, lastY);
          }
          context.stroke();

          context.fillStyle = lineColor;
          context.beginPath();
          context.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
          context.fill();
        }
      }
    }

    if (!visiblePoints) {
      context.fillStyle = "rgba(200,215,239,0.55)";
      context.fillText("等待实时数据...", plot.left + plot.width / 2 - 48, plot.top + plot.height / 2);
    }

    context.fillStyle = TEXT_COLOR;
    context.fillText(xLabel, plot.left + plot.width / 2 - 20, height - 8);
    context.save();
    context.translate(16, plot.top + plot.height / 2 + 20);
    context.rotate(-Math.PI / 2);
    context.fillText(`${yLabel} (${unit.label})`, 0, 0);
    context.restore();
    context.fillText(`${unit.label} | ${visiblePoints} pts`, plot.left + plot.width - 120, 14);

    const nextMeta = { unitLabel: unit.label, visiblePoints, yRange };
    if (
      nextMeta.unitLabel !== metaRef.current.unitLabel
      || nextMeta.visiblePoints !== metaRef.current.visiblePoints
      || String(nextMeta.yRange) !== String(metaRef.current.yRange)
    ) {
      metaRef.current = nextMeta;
      onMetaChange?.(nextMeta);
    }
  }, [width, height, metric, timeWindowSec, yAxisAuto, manualYMin, manualYMax, lineColor, yLabel, xLabel, batch, onMetaChange]);

  return (
    <Box ref={wrapperRef} className="plot-shell" style={{ height }}>
      <canvas ref={canvasRef} style={{ width: "100%", height: "100%", display: "block" }} />
    </Box>
  );
}
