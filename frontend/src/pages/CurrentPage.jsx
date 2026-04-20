import { useEffect, useRef, useState } from "react";
import {
  Badge,
  Button,
  Grid,
  Group,
  NumberInput,
  Progress,
  Select,
  SimpleGrid,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { MetricCard } from "../components/MetricCard";
import { PlotCard } from "../components/PlotCard";
import { SectionCard } from "../components/SectionCard";
import { useDashboard } from "../hooks/useDashboard";
import { api, formatGHz, wsUrl } from "../lib/api";

const CURRENT_STORAGE_KEY = "nv-current-measurement-state-v2";

function toFiniteNumber(value, fallback = 0) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function axisLabel(axis) {
  if (axis === "x_v") {
    return "X";
  }
  if (axis === "y_v") {
    return "Y";
  }
  return "R";
}

function measurementModeLabel(mode) {
  if (mode === "current") {
    return "电流测量";
  }
  if (mode === "odmr") {
    return "ODMR 扫描";
  }
  if (mode === "sensitivity") {
    return "灵敏度测量";
  }
  return "空闲";
}

function statusLabel(state) {
  if (state === "open") {
    return "已连接";
  }
  if (state === "error") {
    return "异常";
  }
  return "连接中";
}

function createDefaultCurrentForm(lastRequest = {}, activeChannel = 0) {
  const fallbackStartHz = Number.isFinite(Number(lastRequest?.start_hz))
    ? Number(lastRequest.start_hz)
    : Number.isFinite(Number(lastRequest?.search_center_hz)) && Number.isFinite(Number(lastRequest?.search_span_hz))
      ? Number(lastRequest.search_center_hz) - Number(lastRequest.search_span_hz) / 2
      : 2.83e9;
  const fallbackStopHz = Number.isFinite(Number(lastRequest?.stop_hz))
    ? Number(lastRequest.stop_hz)
    : Number.isFinite(Number(lastRequest?.search_center_hz)) && Number.isFinite(Number(lastRequest?.search_span_hz))
      ? Number(lastRequest.search_center_hz) + Number(lastRequest.search_span_hz) / 2
      : 2.91e9;
  return {
    channel_index: activeChannel,
    start_hz: fallbackStartHz,
    stop_hz: fallbackStopHz,
    search_points: 121,
    settle_ms: 30,
    slope_fit_points: 9,
    phase_target: "auto",
    ...lastRequest,
  };
}

function normalizeCurrentResult(result) {
  if (!result || typeof result !== "object" || !Object.keys(result).length) {
    return null;
  }
  return result;
}

function buildExportTimestamp(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
    "_",
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
  ].join("");
}

function toCsvCell(value) {
  if (value === null || value === undefined) {
    return "";
  }
  const serialized = String(value);
  if (/[",\n]/.test(serialized)) {
    return `"${serialized.replace(/"/g, '""')}"`;
  }
  return serialized;
}

function downloadTextFile(filename, content, mimeType) {
  const blob = new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function downloadCsvFile(filename, header, rows) {
  const csv = [header, ...rows].map((row) => row.map(toCsvCell).join(",")).join("\n");
  downloadTextFile(filename, `\uFEFF${csv}`, "text/csv;charset=utf-8;");
}

function downloadJsonFile(filename, payload) {
  downloadTextFile(filename, JSON.stringify(payload, null, 2), "application/json;charset=utf-8;");
}

function longestArrayLength(...arrays) {
  return Math.max(
    0,
    ...arrays.map((array) => (Array.isArray(array) ? array.length : 0))
  );
}

function buildLinearRange(values) {
  const numericValues = (Array.isArray(values) ? values : [])
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value));
  if (!numericValues.length) {
    return undefined;
  }
  const minValue = Math.min(...numericValues);
  const maxValue = Math.max(...numericValues);
  if (minValue === maxValue) {
    const padding = Math.abs(minValue || 1) * 0.1;
    return [minValue - padding, maxValue + padding];
  }
  const padding = (maxValue - minValue) * 0.06;
  return [minValue - padding, maxValue + padding];
}

function buildInitialTraceRows(result) {
  const initialTrace = result?.initial_trace || {};
  const rowCount = longestArrayLength(
    initialTrace.frequency_hz,
    initialTrace.x_v,
    initialTrace.y_v,
    initialTrace.r_v
  );
  const rows = [];
  for (let index = 0; index < rowCount; index += 1) {
    rows.push([
      initialTrace.frequency_hz?.[index],
      initialTrace.x_v?.[index],
      initialTrace.y_v?.[index],
      initialTrace.r_v?.[index],
    ]);
  }
  return rows;
}

function buildPhaseTraceRows(result) {
  const phaseTrace = result?.phase_trace || {};
  const rowCount = longestArrayLength(
    phaseTrace.frequency_hz,
    phaseTrace.x_v,
    phaseTrace.y_v,
    phaseTrace.selected_v,
    phaseTrace.orthogonal_v
  );
  const rows = [];
  for (let index = 0; index < rowCount; index += 1) {
    rows.push([
      phaseTrace.frequency_hz?.[index],
      phaseTrace.x_v?.[index],
      phaseTrace.y_v?.[index],
      phaseTrace.selected_v?.[index],
      phaseTrace.orthogonal_v?.[index],
    ]);
  }
  return rows;
}

function buildPhaseCandidateRows(result) {
  const candidates = Array.isArray(result?.phase_candidates) ? result.phase_candidates : [];
  return candidates.map((candidate, index) => [
    index + 1,
    candidate?.phase_delta_deg,
    candidate?.phase_deg,
    candidate?.selected_axis,
    candidate?.score,
    candidate?.left_resonance_hz,
    candidate?.right_resonance_hz,
    candidate?.resonance_splitting_hz,
    candidate?.left_zero_crossing_hz,
    candidate?.right_zero_crossing_hz,
    candidate?.zero_crossing_splitting_hz,
    candidate?.left_slope_v_per_hz,
    candidate?.right_slope_v_per_hz,
    candidate?.left_orthogonal_at_zero_v,
    candidate?.right_orthogonal_at_zero_v,
    candidate?.left_has_bracketed_zero,
    candidate?.right_has_bracketed_zero,
    candidate?.gradient_window_points,
  ]);
}

function buildCalibrationRows(points) {
  return points.map((point, index) => [
    index + 1,
    point.current_a,
    point.current_a * 1e3,
    point.left_zero_crossing_hz,
    point.right_zero_crossing_hz,
    point.splitting_hz,
    point.auto_left_zero_crossing_hz,
    point.auto_right_zero_crossing_hz,
    point.auto_splitting_hz,
    point.resonance_splitting_hz,
    point.selected_axis,
    point.source,
    point.created_at,
  ]);
}

function buildHistoryRows(history) {
  return history.map((item, index) => [
    index + 1,
    item.timestamp,
    item.estimated_current_a,
    item.estimated_current_a * 1e3,
    item.left_zero_crossing_hz,
    item.right_zero_crossing_hz,
    item.splitting_hz,
    item.resonance_splitting_hz,
    item.selected_axis,
    item.calibration_r_squared,
  ]);
}

function loadPersistedPageState() {
  if (typeof window === "undefined") {
    return { calibrationPoints: [], measurementHistory: [] };
  }
  try {
    const raw = window.localStorage.getItem(CURRENT_STORAGE_KEY);
    if (!raw) {
      return { calibrationPoints: [], measurementHistory: [] };
    }
    const parsed = JSON.parse(raw);
    return {
      calibrationPoints: Array.isArray(parsed?.calibrationPoints) ? parsed.calibrationPoints : [],
      measurementHistory: Array.isArray(parsed?.measurementHistory) ? parsed.measurementHistory : [],
    };
  } catch {
    return { calibrationPoints: [], measurementHistory: [] };
  }
}

function persistPageState(calibrationPoints, measurementHistory) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(
    CURRENT_STORAGE_KEY,
    JSON.stringify({ calibrationPoints, measurementHistory })
  );
}

function fitCalibration(points) {
  const validPoints = (Array.isArray(points) ? points : []).filter(
    (point) =>
      Number.isFinite(Number(point?.current_a)) &&
      Number.isFinite(Number(point?.splitting_hz))
  );
  if (validPoints.length < 2) {
    return null;
  }

  const n = validPoints.length;
  let sumX = 0;
  let sumY = 0;
  let sumXX = 0;
  let sumXY = 0;
  for (const point of validPoints) {
    const x = Number(point.splitting_hz);
    const y = Number(point.current_a);
    sumX += x;
    sumY += y;
    sumXX += x * x;
    sumXY += x * y;
  }

  const denominator = n * sumXX - sumX * sumX;
  if (Math.abs(denominator) < 1e-30) {
    return null;
  }

  const slopeAperHz = (n * sumXY - sumX * sumY) / denominator;
  const interceptA = (sumY - slopeAperHz * sumX) / n;
  const meanY = sumY / n;
  let totalVariance = 0;
  let residualVariance = 0;
  for (const point of validPoints) {
    const x = Number(point.splitting_hz);
    const y = Number(point.current_a);
    const predicted = slopeAperHz * x + interceptA;
    totalVariance += (y - meanY) ** 2;
    residualVariance += (y - predicted) ** 2;
  }

  return {
    slope_a_per_hz: slopeAperHz,
    intercept_a: interceptA,
    r_squared: totalVariance > 0 ? 1 - residualVariance / totalVariance : 1,
    point_count: n,
  };
}

function estimateCurrentFromCalibration(model, splittingHz) {
  if (!model || !Number.isFinite(Number(splittingHz))) {
    return NaN;
  }
  return model.slope_a_per_hz * Number(splittingHz) + model.intercept_a;
}

function formatCurrent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  if (Math.abs(numeric) >= 1) {
    return `${numeric.toFixed(6)} A`;
  }
  return `${(numeric * 1e3).toFixed(3)} mA`;
}

function formatScientific(value, digits = 3) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  return numeric.toExponential(digits);
}

function formatSplittingMHz(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  return `${(numeric / 1e6).toFixed(6)} MHz`;
}

export default function CurrentPage() {
  const persistedState = loadPersistedPageState();
  const { data, refresh, error, loading } = useDashboard(1500);
  const [currentForm, setCurrentForm] = useState(null);
  const [currentResult, setCurrentResult] = useState(null);
  const [selectionTarget, setSelectionTarget] = useState("left");
  const [selectedLeftZeroCrossingHz, setSelectedLeftZeroCrossingHz] = useState(null);
  const [selectedRightZeroCrossingHz, setSelectedRightZeroCrossingHz] = useState(null);
  const [calibrationCurrentA, setCalibrationCurrentA] = useState(null);
  const [calibrationPoints, setCalibrationPoints] = useState(persistedState.calibrationPoints);
  const [measurementHistory, setMeasurementHistory] = useState(persistedState.measurementHistory);
  const [isCurrentRunning, setIsCurrentRunning] = useState(false);
  const [currentSocketState, setCurrentSocketState] = useState("connecting");
  const [currentEstimatedDuration, setCurrentEstimatedDuration] = useState(0);
  const currentSocketRef = useRef(null);
  const pendingCurrentRef = useRef(null);
  const pendingCurrentActionRef = useRef("preview");
  const activeCurrentActionRef = useRef("preview");
  const calibrationPointsRef = useRef(calibrationPoints);
  const hasHydratedRef = useRef(false);

  useEffect(() => {
    calibrationPointsRef.current = calibrationPoints;
  }, [calibrationPoints]);

  useEffect(() => {
    persistPageState(calibrationPoints, measurementHistory);
  }, [calibrationPoints, measurementHistory]);

  useEffect(() => {
    if (!data?.measurement || hasHydratedRef.current) {
      return;
    }
    const measurement = data.measurement;
    const activeChannel = data.lockin?.active_channel ?? 0;
    setCurrentForm(createDefaultCurrentForm(measurement.last_current_request, activeChannel));
    const hydratedResult = normalizeCurrentResult(measurement.last_current_result);
    setCurrentResult(hydratedResult);
    const hydratedLeft = toFiniteNumber(hydratedResult?.left_zero_crossing_hz, NaN);
    const hydratedRight = toFiniteNumber(hydratedResult?.right_zero_crossing_hz, NaN);
    if (Number.isFinite(hydratedLeft) && hydratedLeft > 0) {
      setSelectedLeftZeroCrossingHz(hydratedLeft);
    }
    if (Number.isFinite(hydratedRight) && hydratedRight > 0) {
      setSelectedRightZeroCrossingHz(hydratedRight);
    }
    setCurrentEstimatedDuration(toFiniteNumber(measurement.estimated_duration_s, 0));
    hasHydratedRef.current = true;
  }, [data]);

  useEffect(() => {
    if (!data?.measurement || isCurrentRunning) {
      return;
    }
    setCurrentResult(normalizeCurrentResult(data.measurement.last_current_result));
  }, [data, isCurrentRunning]);

  useEffect(() => {
    let active = true;
    let reconnectTimer = 0;
    let socket;

    const connect = () => {
      setCurrentSocketState("connecting");
      socket = new WebSocket(wsUrl("/measurement/current/ws"));
      currentSocketRef.current = socket;

      socket.onopen = () => {
        if (!active) {
          return;
        }
        setCurrentSocketState("open");
        if (pendingCurrentRef.current) {
          activeCurrentActionRef.current = pendingCurrentActionRef.current || "preview";
          socket.send(JSON.stringify(pendingCurrentRef.current));
          pendingCurrentRef.current = null;
          pendingCurrentActionRef.current = "preview";
        }
      };

      socket.onmessage = async (event) => {
        if (!active) {
          return;
        }
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "current_started") {
            setIsCurrentRunning(true);
            setCurrentEstimatedDuration(toFiniteNumber(payload.estimated_duration_s, 0));
            notifications.show({
              color: "cyan",
              title: activeCurrentActionRef.current === "measure" ? "开始自动测量" : "开始标定扫描",
              message: `测量通道 ${toFiniteNumber(payload.channel_index, 0) + 1}`,
            });
          } else if (payload.type === "current_complete") {
            setIsCurrentRunning(false);
            const result = normalizeCurrentResult(payload.result);
            setCurrentResult(result);
            const autoLeft = toFiniteNumber(result?.left_zero_crossing_hz, NaN);
            const autoRight = toFiniteNumber(result?.right_zero_crossing_hz, NaN);
            if (Number.isFinite(autoLeft) && autoLeft > 0) {
              setSelectedLeftZeroCrossingHz(autoLeft);
            }
            if (Number.isFinite(autoRight) && autoRight > 0) {
              setSelectedRightZeroCrossingHz(autoRight);
            }

            if (activeCurrentActionRef.current === "measure") {
              const calibrationModel = fitCalibration(calibrationPointsRef.current);
              if (!calibrationModel) {
                notifications.show({
                  color: "yellow",
                  title: "扫描完成",
                  message: "已完成自动扫描，但标定点不足 2 个，暂时无法换算电流。",
                });
              } else {
                const splittingHz = toFiniteNumber(result?.zero_crossing_splitting_hz, NaN);
                const estimatedCurrentA = estimateCurrentFromCalibration(calibrationModel, splittingHz);
                setMeasurementHistory((prev) => [
                  {
                    id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
                    timestamp: new Date().toISOString(),
                    estimated_current_a: estimatedCurrentA,
                    left_zero_crossing_hz: toFiniteNumber(result?.left_zero_crossing_hz, NaN),
                    right_zero_crossing_hz: toFiniteNumber(result?.right_zero_crossing_hz, NaN),
                    splitting_hz: splittingHz,
                    resonance_splitting_hz: toFiniteNumber(result?.resonance_splitting_hz, NaN),
                    selected_axis: result?.selected_axis || "x_v",
                    calibration_r_squared: calibrationModel.r_squared,
                  },
                  ...prev,
                ]);
                notifications.show({
                  color: "teal",
                  title: "电流测量完成",
                  message: `估算电流 ${formatCurrent(estimatedCurrentA)}`,
                });
              }
            } else {
              notifications.show({
                color: "teal",
                title: "扫描完成",
                message: "已得到左右过零点和劈裂量，可直接加入标定。",
              });
            }
            activeCurrentActionRef.current = "preview";
            await refresh();
          } else if (payload.type === "current_cancelled") {
            setIsCurrentRunning(false);
            activeCurrentActionRef.current = "preview";
            notifications.show({
              color: "yellow",
              title: "电流扫描已停止",
              message: payload.message || "当前任务已取消",
            });
            await refresh();
          } else if (payload.type === "current_error") {
            setIsCurrentRunning(false);
            activeCurrentActionRef.current = "preview";
            notifications.show({
              color: "red",
              title: "电流扫描失败",
              message: payload.message || "Current WebSocket 返回错误",
            });
            await refresh();
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      socket.onerror = () => {
        if (active) {
          setCurrentSocketState("error");
        }
      };

      socket.onclose = () => {
        if (!active) {
          return;
        }
        setCurrentSocketState("connecting");
        reconnectTimer = window.setTimeout(connect, 500);
      };
    };

    connect();
    return () => {
      active = false;
      window.clearTimeout(reconnectTimer);
      socket?.close();
      currentSocketRef.current = null;
    };
  }, [refresh]);

  if (!data || !currentForm) {
    return (
      <Stack gap="md">
        <Text className="page-title">电流测量</Text>
        <Text c="dimmed">{error || (loading ? "正在加载电流测量页面..." : "测量数据为空")}</Text>
      </Stack>
    );
  }

  const measurement = data.measurement || {};
  const isMeasurementRunning = Boolean(measurement.running) || isCurrentRunning;
  const measurementProgress = toFiniteNumber(measurement.progress, 0);
  const measurementStatus = measurement.status || "idle";
  const currentMode = measurement.mode || "idle";
  const phaseTrace = currentResult?.phase_trace || {};
  const phaseCandidates = Array.isArray(currentResult?.phase_candidates)
    ? currentResult.phase_candidates
    : [];
  const selectedAxis = currentResult?.selected_axis || "x_v";
  const orthogonalAxis = currentResult?.orthogonal_axis || "y_v";
  const phaseTraceFrequencyGHz = (phaseTrace.frequency_hz || []).map(
    (value) => toFiniteNumber(value, 0) / 1e9
  );
  const autoLeftZeroCrossingHz = toFiniteNumber(currentResult?.left_zero_crossing_hz, NaN);
  const autoRightZeroCrossingHz = toFiniteNumber(currentResult?.right_zero_crossing_hz, NaN);
  const autoSplittingHz = toFiniteNumber(currentResult?.zero_crossing_splitting_hz, NaN);
  const manualLeftZeroCrossingHz = toFiniteNumber(selectedLeftZeroCrossingHz, NaN);
  const manualRightZeroCrossingHz = toFiniteNumber(selectedRightZeroCrossingHz, NaN);
  const manualSplittingHz =
    Number.isFinite(manualLeftZeroCrossingHz) &&
    Number.isFinite(manualRightZeroCrossingHz) &&
    manualRightZeroCrossingHz > manualLeftZeroCrossingHz
      ? manualRightZeroCrossingHz - manualLeftZeroCrossingHz
      : NaN;
  const leftResonanceHz = toFiniteNumber(currentResult?.left_resonance_hz, NaN);
  const rightResonanceHz = toFiniteNumber(currentResult?.right_resonance_hz, NaN);
  const resonanceSplittingHz = toFiniteNumber(currentResult?.resonance_splitting_hz, NaN);
  const phaseYRange = buildLinearRange([
    ...(phaseTrace.selected_v || []),
    ...(phaseTrace.orthogonal_v || []),
  ]);
  const calibrationModel = fitCalibration(calibrationPoints);
  const predictedCurrentA = estimateCurrentFromCalibration(calibrationModel, autoSplittingHz);
  const latestMeasurement = measurementHistory[0] || null;
  const phaseRevision = `current-split-${phaseTraceFrequencyGHz.length}-${toFiniteNumber(autoSplittingHz, 0).toFixed(3)}-${toFiniteNumber(manualSplittingHz, 0).toFixed(3)}`;
  const phaseShapes = [
    ...(Number.isFinite(autoLeftZeroCrossingHz)
      ? [
          {
            type: "line",
            x0: autoLeftZeroCrossingHz / 1e9,
            x1: autoLeftZeroCrossingHz / 1e9,
            y0: 0,
            y1: 1,
            yref: "paper",
            line: { color: "rgba(100, 228, 194, 0.95)", width: 2, dash: "dot" },
          },
        ]
      : []),
    ...(Number.isFinite(autoRightZeroCrossingHz)
      ? [
          {
            type: "line",
            x0: autoRightZeroCrossingHz / 1e9,
            x1: autoRightZeroCrossingHz / 1e9,
            y0: 0,
            y1: 1,
            yref: "paper",
            line: { color: "rgba(100, 228, 194, 0.95)", width: 2, dash: "dot" },
          },
        ]
      : []),
    ...(Number.isFinite(manualLeftZeroCrossingHz)
      ? [
          {
            type: "line",
            x0: manualLeftZeroCrossingHz / 1e9,
            x1: manualLeftZeroCrossingHz / 1e9,
            y0: 0,
            y1: 1,
            yref: "paper",
            line: { color: "rgba(255, 184, 108, 0.95)", width: 2 },
          },
        ]
      : []),
    ...(Number.isFinite(manualRightZeroCrossingHz)
      ? [
          {
            type: "line",
            x0: manualRightZeroCrossingHz / 1e9,
            x1: manualRightZeroCrossingHz / 1e9,
            y0: 0,
            y1: 1,
            yref: "paper",
            line: { color: "rgba(255, 184, 108, 0.95)", width: 2 },
          },
        ]
      : []),
    ...(Number.isFinite(leftResonanceHz)
      ? [
          {
            type: "line",
            x0: leftResonanceHz / 1e9,
            x1: leftResonanceHz / 1e9,
            y0: 0,
            y1: 1,
            yref: "paper",
            line: { color: "rgba(138, 180, 255, 0.8)", width: 1.5, dash: "dash" },
          },
        ]
      : []),
    ...(Number.isFinite(rightResonanceHz)
      ? [
          {
            type: "line",
            x0: rightResonanceHz / 1e9,
            x1: rightResonanceHz / 1e9,
            y0: 0,
            y1: 1,
            yref: "paper",
            line: { color: "rgba(138, 180, 255, 0.8)", width: 1.5, dash: "dash" },
          },
        ]
      : []),
  ];
  const phaseAnnotations = [
    ...(Number.isFinite(autoSplittingHz)
      ? [
          {
            x: ((autoLeftZeroCrossingHz + autoRightZeroCrossingHz) / 2) / 1e9,
            y: 1.06,
            yref: "paper",
            text: `自动劈裂 ${formatSplittingMHz(autoSplittingHz)}`,
            showarrow: false,
            font: { color: "#64e4c2", size: 11 },
          },
        ]
      : []),
    ...(Number.isFinite(manualSplittingHz)
      ? [
          {
            x: ((manualLeftZeroCrossingHz + manualRightZeroCrossingHz) / 2) / 1e9,
            y: 1.12,
            yref: "paper",
            text: `标定劈裂 ${formatSplittingMHz(manualSplittingHz)}`,
            showarrow: false,
            font: { color: "#ffb86c", size: 11 },
          },
        ]
      : []),
  ];

  const syncFromOdmr = () => {
    const odmrRequest = data.measurement?.last_request || {};
    const startHz = toFiniteNumber(odmrRequest.start_hz, 2.83e9);
    const stopHz = toFiniteNumber(odmrRequest.stop_hz, 2.91e9);
    setCurrentForm((prev) => ({
      ...prev,
      start_hz: startHz,
      stop_hz: stopHz,
    }));
    notifications.show({ color: "teal", title: "已同步", message: "已继承当前 ODMR 的扫描窗口。" });
  };

  const useDefaultResonance = () => {
    setCurrentForm((prev) => ({ ...prev, start_hz: 2.83e9, stop_hz: 2.91e9 }));
  };

  const runCurrentScan = (action) => {
    if (isMeasurementRunning) {
      notifications.show({ color: "yellow", title: "已有任务在运行", message: "请先停止当前测量任务。" });
      return;
    }
    if (!data.lockin.connected || !data.microwave.connected) {
      notifications.show({
        color: "red",
        title: "设备未连接",
        message: "电流测量需要同时连接锁相和微波源。",
      });
      return;
    }
    if (toFiniteNumber(currentForm.start_hz) >= toFiniteNumber(currentForm.stop_hz)) {
      notifications.show({ color: "red", title: "参数错误", message: "终止频率必须大于起始频率。" });
      return;
    }
    if (toFiniteNumber(currentForm.search_points) < 11) {
      notifications.show({ color: "red", title: "参数错误", message: "搜索点数至少为 11。" });
      return;
    }
    if (action === "measure" && !calibrationModel) {
      notifications.show({
        color: "yellow",
        title: "标定不足",
        message: "至少需要 2 个有效标定点才能换算电流。",
      });
      return;
    }

    const socket = currentSocketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      pendingCurrentRef.current = { ...currentForm };
      pendingCurrentActionRef.current = action;
      notifications.show({
        color: "yellow",
        title: "连接中",
        message: "电流测量 WebSocket 尚未打开，连接完成后会自动开始任务。",
      });
      return;
    }

    activeCurrentActionRef.current = action;
    socket.send(JSON.stringify(currentForm));
  };

  const stopCurrentScan = async () => {
    try {
      await api.stopCurrent();
    } catch (err) {
      notifications.show({
        color: "red",
        title: "停止失败",
        message: err instanceof Error ? err.message : "未知错误",
      });
    }
  };

  const useAutoZeroPair = () => {
    if (!Number.isFinite(autoLeftZeroCrossingHz) || !Number.isFinite(autoRightZeroCrossingHz)) {
      notifications.show({ color: "yellow", title: "暂无自动结果", message: "请先执行一次扫描。" });
      return;
    }
    setSelectedLeftZeroCrossingHz(autoLeftZeroCrossingHz);
    setSelectedRightZeroCrossingHz(autoRightZeroCrossingHz);
  };

  const applyCandidatePair = (candidate) => {
    const leftHz = toFiniteNumber(candidate?.left_zero_crossing_hz, NaN);
    const rightHz = toFiniteNumber(candidate?.right_zero_crossing_hz, NaN);
    if (!Number.isFinite(leftHz) || !Number.isFinite(rightHz)) {
      return;
    }
    setSelectedLeftZeroCrossingHz(leftHz);
    setSelectedRightZeroCrossingHz(rightHz);
  };

  const handlePhasePlotClick = (event) => {
    const xValueGHz = toFiniteNumber(event?.points?.[0]?.x, NaN);
    if (!Number.isFinite(xValueGHz)) {
      return;
    }
    const frequencyHz = xValueGHz * 1e9;
    if (selectionTarget === "right") {
      setSelectedRightZeroCrossingHz(frequencyHz);
      return;
    }
    setSelectedLeftZeroCrossingHz(frequencyHz);
  };

  const addCalibrationPoint = () => {
    const currentA = toFiniteNumber(calibrationCurrentA, NaN);
    if (!Number.isFinite(currentA)) {
      notifications.show({ color: "red", title: "输入不完整", message: "请先输入标定电流值。" });
      return;
    }
    if (!Number.isFinite(manualLeftZeroCrossingHz) || !Number.isFinite(manualRightZeroCrossingHz)) {
      notifications.show({
        color: "red",
        title: "输入不完整",
        message: "请先选择左右两个过零点。",
      });
      return;
    }
    if (!(manualRightZeroCrossingHz > manualLeftZeroCrossingHz)) {
      notifications.show({
        color: "red",
        title: "左右顺序错误",
        message: "右过零点必须大于左过零点。",
      });
      return;
    }

    setCalibrationPoints((prev) =>
      [...prev, {
        id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
        created_at: new Date().toISOString(),
        current_a: currentA,
        left_zero_crossing_hz: manualLeftZeroCrossingHz,
        right_zero_crossing_hz: manualRightZeroCrossingHz,
        splitting_hz: manualRightZeroCrossingHz - manualLeftZeroCrossingHz,
        auto_left_zero_crossing_hz: autoLeftZeroCrossingHz,
        auto_right_zero_crossing_hz: autoRightZeroCrossingHz,
        auto_splitting_hz: autoSplittingHz,
        resonance_splitting_hz: resonanceSplittingHz,
        selected_axis: currentResult?.selected_axis || "x_v",
        source:
          Number.isFinite(autoLeftZeroCrossingHz) &&
          Number.isFinite(autoRightZeroCrossingHz) &&
          Math.abs(autoLeftZeroCrossingHz - manualLeftZeroCrossingHz) < 1e-6 &&
          Math.abs(autoRightZeroCrossingHz - manualRightZeroCrossingHz) < 1e-6
            ? "auto"
            : "manual",
      }].sort((left, right) => Number(left.current_a) - Number(right.current_a))
    );
    setCalibrationCurrentA(null);
    notifications.show({ color: "teal", title: "已加入标定点", message: "当前劈裂标定点已写入拟合表。" });
  };

  const clearCalibration = () => {
    setCalibrationPoints([]);
    notifications.show({ color: "yellow", title: "已清空", message: "标定表已清空。" });
  };

  const removeCalibrationPoint = (id) => {
    setCalibrationPoints((prev) => prev.filter((point) => point.id !== id));
  };

  const clearHistory = () => {
    setMeasurementHistory([]);
    notifications.show({ color: "yellow", title: "已清空", message: "测量历史已清空。" });
  };

  const exportCurrentJson = () => {
    if (!calibrationPoints.length && !measurementHistory.length && !currentResult) {
      notifications.show({ color: "yellow", title: "暂无数据", message: "当前没有可导出的电流测量数据。" });
      return;
    }
    const timestamp = buildExportTimestamp();
    downloadJsonFile(`current_measurement_${timestamp}.json`, {
      exported_at: new Date().toISOString(),
      request: currentForm,
      calibration_model: calibrationModel,
      calibration_points: calibrationPoints,
      last_scan_result: currentResult,
      measurement_history: measurementHistory,
    });
    notifications.show({ color: "teal", title: "导出完成", message: "电流测量 JSON 已开始下载。" });
  };

  const exportCurrentCsvBundle = () => {
    const timestamp = buildExportTimestamp();
    const baseName = `current_measurement_${timestamp}`;
    let exportedCount = 0;

    const calibrationRows = buildCalibrationRows(calibrationPoints);
    if (calibrationRows.length) {
      downloadCsvFile(
        `${baseName}_calibration.csv`,
        [
          "point_index",
          "current_a",
          "current_ma",
          "selected_left_zero_crossing_hz",
          "selected_right_zero_crossing_hz",
          "selected_splitting_hz",
          "auto_left_zero_crossing_hz",
          "auto_right_zero_crossing_hz",
          "auto_splitting_hz",
          "resonance_splitting_hz",
          "selected_axis",
          "source",
          "created_at",
        ],
        calibrationRows
      );
      exportedCount += 1;
    }

    const historyRows = buildHistoryRows(measurementHistory);
    if (historyRows.length) {
      downloadCsvFile(
        `${baseName}_history.csv`,
        [
          "measurement_index",
          "timestamp",
          "estimated_current_a",
          "estimated_current_ma",
          "left_zero_crossing_hz",
          "right_zero_crossing_hz",
          "splitting_hz",
          "resonance_splitting_hz",
          "selected_axis",
          "calibration_r_squared",
        ],
        historyRows
      );
      exportedCount += 1;
    }

    const phaseTraceRows = buildPhaseTraceRows(currentResult);
    if (phaseTraceRows.length) {
      downloadCsvFile(
        `${baseName}_phase_trace.csv`,
        ["frequency_hz", "x_v", "y_v", "selected_v", "orthogonal_v"],
        phaseTraceRows
      );
      exportedCount += 1;
    }

    const initialTraceRows = buildInitialTraceRows(currentResult);
    if (initialTraceRows.length) {
      downloadCsvFile(
        `${baseName}_initial_trace.csv`,
        ["frequency_hz", "x_v", "y_v", "r_v"],
        initialTraceRows
      );
      exportedCount += 1;
    }

    const candidateRows = buildPhaseCandidateRows(currentResult);
    if (candidateRows.length) {
      downloadCsvFile(
        `${baseName}_phase_candidates.csv`,
        [
          "candidate_index",
          "phase_delta_deg",
          "phase_deg",
          "selected_axis",
          "score",
          "left_resonance_hz",
          "right_resonance_hz",
          "resonance_splitting_hz",
          "left_zero_crossing_hz",
          "right_zero_crossing_hz",
          "zero_crossing_splitting_hz",
          "left_slope_v_per_hz",
          "right_slope_v_per_hz",
          "left_orthogonal_at_zero_v",
          "right_orthogonal_at_zero_v",
          "left_has_bracketed_zero",
          "right_has_bracketed_zero",
          "gradient_window_points",
        ],
        candidateRows
      );
      exportedCount += 1;
    }

    if (!exportedCount) {
      notifications.show({ color: "yellow", title: "暂无数据", message: "当前没有可导出的 CSV 数据。" });
      return;
    }

    notifications.show({
      color: "teal",
      title: "导出完成",
      message: `电流测量 CSV 已开始下载，共 ${exportedCount} 个文件。`,
    });
  };

  return (
    <Stack gap="lg">
      <div>
        <Text className="eyebrow">Step 5</Text>
        <Text className="page-title">电流测量</Text>
        <Text c="dimmed" maw={960}>
          这一页按 ODMR 双峰劈裂来测电流。先设置起始频率和终止频率，扫出一条完整的 ODMR；系统再自动找左右两个共振峰、自动调相并给出左右过零点。你用已知电流值对劈裂频率做标定，之后再由劈裂量直接换算电流。
        </Text>
      </div>

      <SimpleGrid cols={{ base: 1, md: 2, xl: 6 }}>
        <MetricCard label="当前任务" value={measurementModeLabel(currentMode)} hint={measurementStatus} />
        <MetricCard label="WebSocket" value={statusLabel(currentSocketState)} hint={isCurrentRunning ? "正在扫描" : "等待任务"} />
        <MetricCard label="自动劈裂" value={formatSplittingMHz(autoSplittingHz)} hint={Number.isFinite(resonanceSplittingHz) ? `峰间距 ${formatSplittingMHz(resonanceSplittingHz)}` : "尚未扫描"} />
        <MetricCard label="手动劈裂" value={formatSplittingMHz(manualSplittingHz)} hint="用于标定拟合" />
        <MetricCard label="标定点数" value={String(calibrationPoints.length)} hint={calibrationModel ? `R² ${toFiniteNumber(calibrationModel.r_squared, 0).toFixed(6)}` : "至少需要 2 点"} />
        <MetricCard label="最新电流" value={latestMeasurement ? formatCurrent(latestMeasurement.estimated_current_a) : formatCurrent(predictedCurrentA)} hint={latestMeasurement ? latestMeasurement.timestamp.replace("T", " ").slice(0, 19) : "尚未执行自动测量"} />
      </SimpleGrid>

      <Progress value={measurementProgress * 100} color="cyan" radius="xl" size="lg" />

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="扫描参数"
            description="先按起始频率到终止频率扫完整 ODMR，再自动调相并计算左右过零点和劈裂。"
            badge={isCurrentRunning ? "Running" : "Ready"}
          >
            <SimpleGrid cols={{ base: 1, md: 2 }}>
              <NumberInput
                label="锁相通道"
                value={currentForm.channel_index}
                min={0}
                onChange={(value) =>
                  setCurrentForm((prev) => ({
                    ...prev,
                    channel_index: Math.max(0, Math.round(toFiniteNumber(value, prev.channel_index))),
                  }))
                }
              />
              <Select
                label="优先通道"
                value={currentForm.phase_target}
                onChange={(value) => setCurrentForm((prev) => ({ ...prev, phase_target: value || "auto" }))}
                data={[
                  { value: "auto", label: "自动选择" },
                  { value: "x_v", label: "优先 X" },
                  { value: "y_v", label: "优先 Y" },
                ]}
              />
              <NumberInput
                label="起始频率 (Hz)"
                value={currentForm.start_hz}
                onChange={(value) =>
                  setCurrentForm((prev) => ({
                    ...prev,
                    start_hz: toFiniteNumber(value, prev.start_hz),
                  }))
                }
              />
              <NumberInput
                label="终止频率 (Hz)"
                value={currentForm.stop_hz}
                onChange={(value) =>
                  setCurrentForm((prev) => ({
                    ...prev,
                    stop_hz: toFiniteNumber(value, prev.stop_hz),
                  }))
                }
              />
              <NumberInput
                label="搜索点数"
                value={currentForm.search_points}
                onChange={(value) =>
                  setCurrentForm((prev) => ({
                    ...prev,
                    search_points: Math.max(11, Math.round(toFiniteNumber(value, prev.search_points))),
                  }))
                }
              />
              <NumberInput
                label="稳定等待 (ms)"
                value={currentForm.settle_ms}
                onChange={(value) =>
                  setCurrentForm((prev) => ({
                    ...prev,
                    settle_ms: Math.max(1, toFiniteNumber(value, prev.settle_ms)),
                  }))
                }
              />
              <NumberInput
                label="斜率拟合点数"
                value={currentForm.slope_fit_points}
                onChange={(value) =>
                  setCurrentForm((prev) => ({
                    ...prev,
                    slope_fit_points: Math.max(3, Math.round(toFiniteNumber(value, prev.slope_fit_points))),
                  }))
                }
              />
            </SimpleGrid>

            <Group mt="lg">
              <Button variant="light" color="gray" onClick={syncFromOdmr}>继承 ODMR 窗口</Button>
              <Button variant="light" color="gray" onClick={useDefaultResonance}>回到 2.87 GHz</Button>
            </Group>

            <Group mt="md">
              <Button
                variant="light"
                onClick={() => runCurrentScan("preview")}
                loading={isCurrentRunning && activeCurrentActionRef.current !== "measure"}
                disabled={isMeasurementRunning && !isCurrentRunning}
              >
                扫描用于标定
              </Button>
              <Button
                color="cyan"
                onClick={() => runCurrentScan("measure")}
                loading={isCurrentRunning && activeCurrentActionRef.current === "measure"}
                disabled={isMeasurementRunning && !isCurrentRunning}
              >
                自动测量电流
              </Button>
              <Button color="red" variant="light" onClick={stopCurrentScan} disabled={!isCurrentRunning}>
                停止
              </Button>
            </Group>

            <SimpleGrid cols={{ base: 1, md: 3 }} mt="lg">
              <MetricCard label="预计耗时" value={`${currentEstimatedDuration.toFixed(2)} s`} hint={`进度 ${(measurementProgress * 100).toFixed(1)}%`} />
              <MetricCard label="左过零点" value={Number.isFinite(autoLeftZeroCrossingHz) ? formatGHz(autoLeftZeroCrossingHz) : "--"} hint={Number.isFinite(leftResonanceHz) ? `左峰 ${formatGHz(leftResonanceHz)}` : "尚未扫描"} />
              <MetricCard label="右过零点" value={Number.isFinite(autoRightZeroCrossingHz) ? formatGHz(autoRightZeroCrossingHz) : "--"} hint={Number.isFinite(rightResonanceHz) ? `右峰 ${formatGHz(rightResonanceHz)}` : "尚未扫描"} />
            </SimpleGrid>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="左右过零点选择"
            description="先在下面选择“左点”或“右点”，再点击图上的零点位置。绿色虚线是自动结果，橙色实线是你当前用于标定的左右选点。"
            badge={axisLabel(selectedAxis)}
          >
            <PlotCard
              traces={[
                {
                  name: `${axisLabel(selectedAxis)} 选中通道`,
                  x: phaseTraceFrequencyGHz,
                  y: phaseTrace.selected_v || [],
                  lineColor: "#64e4c2",
                  hovertemplate: "%{x:.6f} GHz<br>Selected=%{y:.6e} V<extra></extra>",
                },
                {
                  name: `${axisLabel(orthogonalAxis)} 正交通道`,
                  x: phaseTraceFrequencyGHz,
                  y: phaseTrace.orthogonal_v || [],
                  lineColor: "#ffb86c",
                  hovertemplate: "%{x:.6f} GHz<br>Orthogonal=%{y:.6e} V<extra></extra>",
                },
              ]}
              xTitle="Microwave Frequency (GHz)"
              yTitle="Lock-in Voltage (V)"
              yRange={phaseYRange}
              shapes={phaseShapes}
              annotations={phaseAnnotations}
              uirevision={phaseRevision}
              onClick={handlePhasePlotClick}
            />

            <Group mt="md">
              <Select
                label="当前点击设置"
                value={selectionTarget}
                onChange={(value) => setSelectionTarget(value || "left")}
                data={[
                  { value: "left", label: "左过零点" },
                  { value: "right", label: "右过零点" },
                ]}
                w={180}
              />
              <Button variant="light" color="gray" onClick={useAutoZeroPair}>
                使用自动双点
              </Button>
              {phaseCandidates.map((candidate, index) => (
                <Button
                  key={`${candidate.left_zero_crossing_hz}-${candidate.right_zero_crossing_hz}-${index}`}
                  variant="light"
                  color="gray"
                  onClick={() => applyCandidatePair(candidate)}
                >
                  候选 {index + 1} {formatSplittingMHz(candidate.zero_crossing_splitting_hz)}
                </Button>
              ))}
            </Group>

            <SimpleGrid cols={{ base: 1, md: 3 }} mt="lg">
              <MetricCard label="手动左点" value={Number.isFinite(manualLeftZeroCrossingHz) ? formatGHz(manualLeftZeroCrossingHz) : "--"} hint="点图或直接输入" />
              <MetricCard label="手动右点" value={Number.isFinite(manualRightZeroCrossingHz) ? formatGHz(manualRightZeroCrossingHz) : "--"} hint="点图或直接输入" />
              <MetricCard label="手动劈裂" value={formatSplittingMHz(manualSplittingHz)} hint={calibrationModel ? `估算 ${formatCurrent(estimateCurrentFromCalibration(calibrationModel, manualSplittingHz))}` : "等待标定"} />
            </SimpleGrid>
          </SectionCard>
        </Grid.Col>
      </Grid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="标定"
            description="输入已知电流值，并把当前左右过零点对应的劈裂量加入标定表。"
            badge={calibrationModel ? "Calibrated" : "Need Points"}
          >
            <SimpleGrid cols={{ base: 1, md: 2 }}>
              <NumberInput label="标定电流值 (A)" value={calibrationCurrentA} onChange={setCalibrationCurrentA} placeholder="例如 0.01" />
              <NumberInput label="左过零点 (Hz)" value={selectedLeftZeroCrossingHz} onChange={setSelectedLeftZeroCrossingHz} />
              <NumberInput label="右过零点 (Hz)" value={selectedRightZeroCrossingHz} onChange={setSelectedRightZeroCrossingHz} />
              <NumberInput label="当前劈裂 (Hz)" value={Number.isFinite(manualSplittingHz) ? manualSplittingHz : undefined} readOnly />
            </SimpleGrid>

            <Group mt="lg">
              <Button color="cyan" onClick={addCalibrationPoint}>加入标定点</Button>
              <Button variant="light" color="gray" onClick={clearCalibration} disabled={!calibrationPoints.length}>
                清空标定
              </Button>
            </Group>

            <Group mt="md">
              <Button variant="light" color="gray" onClick={exportCurrentCsvBundle}>
                导出 CSV
              </Button>
              <Button variant="light" color="gray" onClick={exportCurrentJson}>
                导出 JSON
              </Button>
            </Group>

            <SimpleGrid cols={{ base: 1, md: 2 }} mt="lg">
              <MetricCard label="拟合斜率" value={calibrationModel ? formatScientific(calibrationModel.slope_a_per_hz, 4) : "--"} hint="I = a * Δf + b" />
              <MetricCard label="拟合截距" value={calibrationModel ? formatScientific(calibrationModel.intercept_a, 4) : "--"} hint={calibrationModel ? `R² ${calibrationModel.r_squared.toFixed(6)}` : "等待标定"} />
            </SimpleGrid>

            <Text c="dimmed" size="sm" mt="md">
              自动测量时，系统会重新扫描左右共振峰并计算当前劈裂量，再按照标定模型把 Δf 直接换算成电流。
            </Text>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="标定表"
            description="每个标定点都保存左右过零点、劈裂量和自动扫描结果。"
            badge={String(calibrationPoints.length)}
          >
            {calibrationPoints.length ? (
              <div style={{ overflowX: "auto" }}>
                <Table striped highlightOnHover withTableBorder>
                  <Table.Thead>
                    <Table.Tr>
                      <Table.Th>#</Table.Th>
                      <Table.Th>电流</Table.Th>
                      <Table.Th>左点</Table.Th>
                      <Table.Th>右点</Table.Th>
                      <Table.Th>劈裂</Table.Th>
                      <Table.Th>自动劈裂</Table.Th>
                      <Table.Th>来源</Table.Th>
                      <Table.Th>操作</Table.Th>
                    </Table.Tr>
                  </Table.Thead>
                  <Table.Tbody>
                    {calibrationPoints.map((point, index) => (
                      <Table.Tr key={point.id}>
                        <Table.Td>{index + 1}</Table.Td>
                        <Table.Td>{formatCurrent(point.current_a)}</Table.Td>
                        <Table.Td>{formatGHz(point.left_zero_crossing_hz)}</Table.Td>
                        <Table.Td>{formatGHz(point.right_zero_crossing_hz)}</Table.Td>
                        <Table.Td>{formatSplittingMHz(point.splitting_hz)}</Table.Td>
                        <Table.Td>{formatSplittingMHz(point.auto_splitting_hz)}</Table.Td>
                        <Table.Td>
                          <Badge variant="light" color={point.source === "manual" ? "orange" : "teal"}>
                            {point.source === "manual" ? "手动选点" : "自动双点"}
                          </Badge>
                        </Table.Td>
                        <Table.Td>
                          <Button variant="subtle" color="red" onClick={() => removeCalibrationPoint(point.id)}>
                            删除
                          </Button>
                        </Table.Td>
                      </Table.Tr>
                    ))}
                  </Table.Tbody>
                </Table>
              </div>
            ) : (
              <Text c="dimmed">暂无标定点。先扫描出左右过零点，再输入已知电流值加入标定。</Text>
            )}
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard
        title="测量历史"
        description="自动测量完成后，会记录每次得到的左右过零点和对应劈裂量。"
        badge={String(measurementHistory.length)}
      >
        <Group mb="md">
          <Button variant="light" color="gray" onClick={clearHistory} disabled={!measurementHistory.length}>
            清空历史
          </Button>
        </Group>

        {measurementHistory.length ? (
          <div style={{ overflowX: "auto" }}>
            <Table striped highlightOnHover withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>#</Table.Th>
                  <Table.Th>时间</Table.Th>
                  <Table.Th>电流</Table.Th>
                  <Table.Th>左点</Table.Th>
                  <Table.Th>右点</Table.Th>
                  <Table.Th>劈裂</Table.Th>
                  <Table.Th>R²</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {measurementHistory.map((item, index) => (
                  <Table.Tr key={item.id}>
                    <Table.Td>{index + 1}</Table.Td>
                    <Table.Td>{String(item.timestamp).replace("T", " ").slice(0, 19)}</Table.Td>
                    <Table.Td>{formatCurrent(item.estimated_current_a)}</Table.Td>
                    <Table.Td>{formatGHz(item.left_zero_crossing_hz)}</Table.Td>
                    <Table.Td>{formatGHz(item.right_zero_crossing_hz)}</Table.Td>
                    <Table.Td>{formatSplittingMHz(item.splitting_hz)}</Table.Td>
                    <Table.Td>{Number.isFinite(Number(item.calibration_r_squared)) ? Number(item.calibration_r_squared).toFixed(6) : "--"}</Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </div>
        ) : (
          <Text c="dimmed">暂无自动测量记录。</Text>
        )}
      </SectionCard>
    </Stack>
  );
}
