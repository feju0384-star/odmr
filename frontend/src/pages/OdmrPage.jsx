import { useEffect, useRef, useState } from "react";
import {
  Button,
  Grid,
  Group,
  List,
  NumberInput,
  Progress,
  Select,
  SimpleGrid,
  Stack,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { MetricCard } from "../components/MetricCard";
import { PlotCard } from "../components/PlotCard";
import { SectionCard } from "../components/SectionCard";
import { useDashboard } from "../hooks/useDashboard";
import { api, formatGHz, shortReadout, wsUrl } from "../lib/api";

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

function createEmptyTrace() {
  return {
    frequency_hz: [],
    intensity: [],
    scan_mode: "software_sync",
    readout_source: "r_v",
  };
}

function createDefaultOdmrForm(lastRequest = {}) {
  return {
    scan_mode: "software_sync",
    readout_source: "r_v",
    start_hz: 2.83e9,
    stop_hz: 2.91e9,
    points: 161,
    dwell_ms: 8,
    averages: 4,
    aux_voltage_min_v: 0,
    aux_voltage_max_v: 10,
    aux_frequency_min_hz: 2.82e9,
    aux_frequency_max_hz: 2.92e9,
    ...lastRequest,
  };
}

function createDefaultSensitivityForm(lastRequest = {}, activeChannel = 0) {
  return {
    channel_index: activeChannel,
    search_center_hz: 2.87e9,
    search_span_hz: 20e6,
    search_points: 121,
    settle_ms: 30,
    slope_fit_points: 9,
    asd_duration_s: 5,
    asd_min_frequency_hz: 1,
    phase_target: "auto",
    cos_alpha: 1,
    gamma_hz_per_t: 28e9,
    ...lastRequest,
  };
}

function normalizeSensitivityResult(result) {
  if (!result || typeof result !== "object" || !Object.keys(result).length) {
    return null;
  }
  return result;
}

function formatScientific(value, digits = 3) {
  if (!Number.isFinite(Number(value))) {
    return "--";
  }
  return Number(value).toExponential(digits);
}

function formatSensitivity(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  return `${(numeric * 1e9).toFixed(3)} nT/√Hz`;
}

function formatSlope(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  return `${numeric.toFixed(3)} µV/MHz`;
}

function lastValue(values) {
  return Array.isArray(values) && values.length ? values[values.length - 1] : undefined;
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

function buildLogRange(values) {
  const numericValues = (Array.isArray(values) ? values : [])
    .map((value) => Number(value))
    .filter((value) => Number.isFinite(value) && value > 0);
  if (!numericValues.length) {
    return undefined;
  }
  const minValue = Math.min(...numericValues);
  const maxValue = Math.max(...numericValues);
  if (!(maxValue > minValue)) {
    return undefined;
  }
  return [Math.log10(minValue), Math.log10(maxValue)];
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

function buildOdmrCsvRows(trace) {
  const frequencies = trace?.frequency_hz || [];
  const intensities = trace?.intensity || [];
  const rowCount = longestArrayLength(frequencies, intensities);
  const rows = [];
  for (let index = 0; index < rowCount; index += 1) {
    rows.push([
      frequencies[index],
      intensities[index],
      trace?.readout_source || "r_v",
      trace?.scan_mode || "software_sync",
    ]);
  }
  return rows;
}

function buildSensitivityPhaseTraceRows(result) {
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

function buildSensitivityTimeTraceRows(result) {
  const timeTrace = result?.time_trace || {};
  const rowCount = longestArrayLength(timeTrace.time_s, timeTrace.signal_v);
  const rows = [];
  for (let index = 0; index < rowCount; index += 1) {
    rows.push([timeTrace.time_s?.[index], timeTrace.signal_v?.[index]]);
  }
  return rows;
}

function buildSensitivitySpectrumRows(result) {
  const spectrum = result?.asd_spectrum || {};
  const rowCount = longestArrayLength(
    spectrum.frequency_hz,
    spectrum.asd_v_per_sqrt_hz,
    spectrum.sensitivity_t_per_sqrt_hz
  );
  const rows = [];
  for (let index = 0; index < rowCount; index += 1) {
    rows.push([
      spectrum.frequency_hz?.[index],
      spectrum.asd_v_per_sqrt_hz?.[index],
      spectrum.sensitivity_t_per_sqrt_hz?.[index],
    ]);
  }
  return rows;
}

function buildSensitivityPhaseCandidateRows(result) {
  const candidates = Array.isArray(result?.phase_candidates) ? result.phase_candidates : [];
  return candidates.map((candidate, index) => [
    index + 1,
    candidate?.phase_delta_deg,
    candidate?.phase_deg,
    candidate?.selected_axis,
    candidate?.score,
    candidate?.zero_crossing_hz,
    candidate?.slope_v_per_hz,
    candidate?.orthogonal_at_zero_v,
    candidate?.has_bracketed_zero,
    candidate?.gradient_window_points,
  ]);
}

export default function OdmrPage() {
  const { data, refresh, error, loading } = useDashboard(1500);
  const [odmrForm, setOdmrForm] = useState(null);
  const [trace, setTrace] = useState(null);
  const [sensitivityForm, setSensitivityForm] = useState(null);
  const [sensitivityResult, setSensitivityResult] = useState(null);
  const [isOdmrRunning, setIsOdmrRunning] = useState(false);
  const [isSensitivityRunning, setIsSensitivityRunning] = useState(false);
  const [odmrSocketState, setOdmrSocketState] = useState("connecting");
  const [sensitivitySocketState, setSensitivitySocketState] = useState("connecting");
  const [odmrStatusText, setOdmrStatusText] = useState("空闲");
  const [odmrProgress, setOdmrProgress] = useState(0);
  const [currentPoint, setCurrentPoint] = useState(0);
  const [currentFrequencyHz, setCurrentFrequencyHz] = useState(0);
  const [currentValue, setCurrentValue] = useState(0);
  const [estimatedDuration, setEstimatedDuration] = useState(0);
  const [sensitivityEstimatedDuration, setSensitivityEstimatedDuration] = useState(0);
  const [liveReadout, setLiveReadout] = useState(false);
  const odmrSocketRef = useRef(null);
  const sensitivitySocketRef = useRef(null);
  const pendingOdmrRef = useRef(null);
  const pendingSensitivityRef = useRef(null);
  const hasHydratedRef = useRef(false);

  useEffect(() => {
    if (!data?.measurement || hasHydratedRef.current) {
      return;
    }
    const measurement = data.measurement;
    const activeChannel = data.lockin?.active_channel ?? 0;
    setOdmrForm(createDefaultOdmrForm(measurement.last_request));
    setTrace(measurement.last_trace || createEmptyTrace());
    setSensitivityForm(createDefaultSensitivityForm(measurement.last_sensitivity_request, activeChannel));
    setSensitivityResult(normalizeSensitivityResult(measurement.last_sensitivity_result));
    setEstimatedDuration(toFiniteNumber(measurement.estimated_duration_s, 0));
    setSensitivityEstimatedDuration(
      toFiniteNumber(measurement.last_sensitivity_request?.asd_duration_s, 5)
    );
    hasHydratedRef.current = true;
  }, [data]);

  useEffect(() => {
    if (!data?.measurement || isOdmrRunning) {
      return;
    }
    setEstimatedDuration(toFiniteNumber(data.measurement.estimated_duration_s, 0));
  }, [data, isOdmrRunning]);

  useEffect(() => {
    if (!data?.measurement || isSensitivityRunning) {
      return;
    }
    setSensitivityResult(normalizeSensitivityResult(data.measurement.last_sensitivity_result));
  }, [data, isSensitivityRunning]);

  useEffect(() => {
    let active = true;
    let reconnectTimer = 0;
    let socket;

    const connect = () => {
      setOdmrSocketState("connecting");
      socket = new WebSocket(wsUrl("/measurement/odmr/ws"));
      odmrSocketRef.current = socket;

      socket.onopen = () => {
        if (!active) {
          return;
        }
        setOdmrSocketState("open");
        if (pendingOdmrRef.current) {
          socket.send(JSON.stringify(pendingOdmrRef.current));
          pendingOdmrRef.current = null;
        }
      };

      socket.onmessage = async (event) => {
        if (!active) {
          return;
        }
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "odmr_started") {
            setIsOdmrRunning(true);
            setOdmrProgress(0);
            setOdmrStatusText(payload.live_readout ? "正在真实扫描" : "正在模拟扫描");
            setCurrentPoint(0);
            setCurrentFrequencyHz(0);
            setCurrentValue(0);
            setEstimatedDuration(toFiniteNumber(payload.estimated_duration_s, 0));
            setLiveReadout(Boolean(payload.live_readout));
            setTrace({
              frequency_hz: [],
              intensity: [],
              scan_mode: payload.scan_mode,
              readout_source: payload.readout_source,
            });
          } else if (payload.type === "odmr_point") {
            setOdmrProgress(payload.progress ?? 0);
            setCurrentPoint(payload.index ?? 0);
            setCurrentFrequencyHz(toFiniteNumber(payload.frequency_hz, 0));
            setCurrentValue(toFiniteNumber(payload.value, 0));
            setLiveReadout(Boolean(payload.live_readout));
            setTrace((prev) => ({
              frequency_hz: [...(prev?.frequency_hz || []), payload.frequency_hz],
              intensity: [...(prev?.intensity || []), payload.value],
              scan_mode: payload.scan_mode,
              readout_source: payload.readout_source,
            }));
          } else if (payload.type === "odmr_complete") {
            setIsOdmrRunning(false);
            setOdmrProgress(1);
            setOdmrStatusText("扫描完成");
            setTrace(payload.trace || createEmptyTrace());
            notifications.show({ color: "teal", title: "扫描完成", message: "ODMR 扫描已完成" });
            await refresh();
          } else if (payload.type === "odmr_cancelled") {
            setIsOdmrRunning(false);
            setOdmrProgress(toFiniteNumber(payload.progress, 0));
            setOdmrStatusText("扫描已停止");
            if (payload.trace) {
              setTrace(payload.trace);
            }
            notifications.show({ color: "yellow", title: "扫描已停止", message: "当前 ODMR 任务已停止" });
            await refresh();
          } else if (payload.type === "odmr_error") {
            setIsOdmrRunning(false);
            setOdmrStatusText(payload.message || "扫描失败");
            notifications.show({
              color: "red",
              title: "扫描失败",
              message: payload.message || "ODMR WebSocket 返回错误",
            });
            await refresh();
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      socket.onerror = () => {
        if (active) {
          setOdmrSocketState("error");
        }
      };

      socket.onclose = () => {
        if (!active) {
          return;
        }
        setOdmrSocketState("connecting");
        reconnectTimer = window.setTimeout(connect, 500);
      };
    };

    connect();
    return () => {
      active = false;
      window.clearTimeout(reconnectTimer);
      socket?.close();
      odmrSocketRef.current = null;
    };
  }, [refresh]);

  useEffect(() => {
    let active = true;
    let reconnectTimer = 0;
    let socket;

    const connect = () => {
      setSensitivitySocketState("connecting");
      socket = new WebSocket(wsUrl("/measurement/sensitivity/ws"));
      sensitivitySocketRef.current = socket;

      socket.onopen = () => {
        if (!active) {
          return;
        }
        setSensitivitySocketState("open");
        if (pendingSensitivityRef.current) {
          socket.send(JSON.stringify(pendingSensitivityRef.current));
          pendingSensitivityRef.current = null;
        }
      };

      socket.onmessage = async (event) => {
        if (!active) {
          return;
        }
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === "sensitivity_started") {
            setIsSensitivityRunning(true);
            setSensitivityEstimatedDuration(toFiniteNumber(payload.estimated_duration_s, 0));
            notifications.show({
              color: "cyan",
              title: "灵敏度测量开始",
              message: `测量通道 ${toFiniteNumber(payload.channel_index, 0) + 1}`,
            });
          } else if (payload.type === "sensitivity_complete") {
            setIsSensitivityRunning(false);
            setSensitivityResult(normalizeSensitivityResult(payload.result));
            notifications.show({
              color: "teal",
              title: "灵敏度测量完成",
              message: "已得到过零点、ASD 和灵敏度谱线",
            });
            await refresh();
          } else if (payload.type === "sensitivity_cancelled") {
            setIsSensitivityRunning(false);
            notifications.show({
              color: "yellow",
              title: "灵敏度测量已停止",
              message: payload.message || "当前灵敏度任务已取消",
            });
            await refresh();
          } else if (payload.type === "sensitivity_error") {
            setIsSensitivityRunning(false);
            notifications.show({
              color: "red",
              title: "灵敏度测量失败",
              message: payload.message || "Sensitivity WebSocket 返回错误",
            });
            await refresh();
          }
        } catch {
          // Ignore malformed frames.
        }
      };

      socket.onerror = () => {
        if (active) {
          setSensitivitySocketState("error");
        }
      };

      socket.onclose = () => {
        if (!active) {
          return;
        }
        setSensitivitySocketState("connecting");
        reconnectTimer = window.setTimeout(connect, 500);
      };
    };

    connect();
    return () => {
      active = false;
      window.clearTimeout(reconnectTimer);
      socket?.close();
      sensitivitySocketRef.current = null;
    };
  }, [refresh]);

  if (!data || !odmrForm || !trace || !sensitivityForm) {
    return (
      <Stack gap="md">
        <Text className="page-title">ODMR 与灵敏度</Text>
        <Text c="dimmed">{error || (loading ? "正在加载实验页面..." : "测量数据为空")}</Text>
      </Stack>
    );
  }

  const measurement = data.measurement || {};
  const activeLockinChannel = data.lockin.channels?.[data.lockin.active_channel ?? 0];
  const isMeasurementRunning = Boolean(measurement.running) || isOdmrRunning || isSensitivityRunning;
  const activeMode = measurement.mode || "idle";
  const measurementProgress = toFiniteNumber(measurement.progress, 0);
  const measurementStatus = measurement.status || "idle";
  const currentMeasurementFrequencyHz = toFiniteNumber(
    measurement.current_frequency_hz || currentFrequencyHz,
    0
  );
  const currentMeasurementValue = toFiniteNumber(
    measurement.current_value || currentValue,
    0
  );
  const yValues = trace.intensity || [];
  const yMin = yValues.length ? Math.min(...yValues) : 0;
  const yMax = yValues.length ? Math.max(...yValues) : 0;
  const range = trace.frequency_hz?.length
    ? `${formatGHz(trace.frequency_hz[0])} - ${formatGHz(trace.frequency_hz[trace.frequency_hz.length - 1])}`
    : "暂无数据";

  const phaseTrace = sensitivityResult?.phase_trace || {};
  const timeTrace = sensitivityResult?.time_trace || {};
  const asdSpectrum = sensitivityResult?.asd_spectrum || {};
  const selectedAxis = sensitivityResult?.selected_axis || "x_v";
  const orthogonalAxis = sensitivityResult?.orthogonal_axis || "y_v";
  const phaseTraceFrequencyGHz = (phaseTrace.frequency_hz || []).map((value) => toFiniteNumber(value, 0) / 1e9);
  const zeroCrossingGHz = toFiniteNumber(sensitivityResult?.zero_crossing_hz, 0) / 1e9;
  const fitStartIndex = toFiniteNumber(phaseTrace.fit_start_index, -1);
  const fitStopIndex = toFiniteNumber(phaseTrace.fit_stop_index, -1);
  const fitStartGHz =
    fitStartIndex >= 0 && phaseTraceFrequencyGHz[fitStartIndex] !== undefined
      ? phaseTraceFrequencyGHz[fitStartIndex]
      : null;
  const fitStopGHz =
    fitStopIndex > 0 && phaseTraceFrequencyGHz[fitStopIndex - 1] !== undefined
      ? phaseTraceFrequencyGHz[fitStopIndex - 1]
      : null;
  const bestSensitivityFrequencyHz = toFiniteNumber(
    sensitivityResult?.best_sensitivity_frequency_hz,
    0
  );
  const bestSensitivity = toFiniteNumber(sensitivityResult?.best_sensitivity_t_per_sqrt_hz, NaN);
  const phasePlotShapes = [
    ...(Number.isFinite(zeroCrossingGHz) && zeroCrossingGHz > 0
      ? [
          {
            type: "line",
            x0: zeroCrossingGHz,
            x1: zeroCrossingGHz,
            y0: 0,
            y1: 1,
            yref: "paper",
            line: { color: "rgba(255, 184, 108, 0.85)", width: 2, dash: "dot" },
          },
        ]
      : []),
    ...(fitStartGHz !== null && fitStopGHz !== null
      ? [
          {
            type: "rect",
            x0: fitStartGHz,
            x1: fitStopGHz,
            y0: 0,
            y1: 1,
            yref: "paper",
            fillcolor: "rgba(100, 228, 194, 0.08)",
            line: { width: 0 },
          },
        ]
      : []),
  ];
  const phasePlotAnnotations =
    Number.isFinite(zeroCrossingGHz) && zeroCrossingGHz > 0
      ? [
          {
            x: zeroCrossingGHz,
            y: 1.06,
            yref: "paper",
            text: `过零点 ${zeroCrossingGHz.toFixed(6)} GHz`,
            showarrow: false,
            font: { color: "#ffb86c", size: 11 },
          },
        ]
      : [];
  const bestIndex = Array.isArray(asdSpectrum.frequency_hz)
    ? asdSpectrum.frequency_hz.findIndex(
        (value) => Math.abs(toFiniteNumber(value, 0) - bestSensitivityFrequencyHz) < 1e-9
      )
    : -1;
  const bestSensitivityY =
    bestIndex >= 0 && Array.isArray(asdSpectrum.sensitivity_t_per_sqrt_hz)
      ? toFiniteNumber(asdSpectrum.sensitivity_t_per_sqrt_hz[bestIndex], NaN)
      : NaN;
  const bestSensitivityFrequencyPlot = bestSensitivityFrequencyHz > 0 ? bestSensitivityFrequencyHz : null;
  const phaseYRange = buildLinearRange([
    ...(phaseTrace.selected_v || []),
    ...(phaseTrace.orthogonal_v || []),
  ]);
  const timeXRange = buildLinearRange(timeTrace.time_s || []);
  const timeYRange = buildLinearRange(timeTrace.signal_v || []);
  const asdXRange = buildLogRange(asdSpectrum.frequency_hz || []);
  const asdYRange = buildLogRange(asdSpectrum.asd_v_per_sqrt_hz || []);
  const sensitivityXRange = buildLogRange(asdSpectrum.frequency_hz || []);
  const sensitivityYRange = buildLogRange(asdSpectrum.sensitivity_t_per_sqrt_hz || []);
  const phaseRevision = `phase-${phaseTraceFrequencyGHz.length}-${zeroCrossingGHz.toFixed(6)}`;
  const timeRevision = `time-${(timeTrace.time_s || []).length}-${toFiniteNumber(lastValue(timeTrace.time_s), 0).toFixed(6)}`;
  const asdRevision = `asd-${(asdSpectrum.frequency_hz || []).length}-${toFiniteNumber(lastValue(asdSpectrum.frequency_hz), 0).toFixed(6)}`;
  const sensitivityRevision = `sensitivity-${(asdSpectrum.sensitivity_t_per_sqrt_hz || []).length}-${bestSensitivityFrequencyHz.toFixed(6)}`;

  const runOdmr = () => {
    const socket = odmrSocketRef.current;
    if (toFiniteNumber(odmrForm.start_hz) >= toFiniteNumber(odmrForm.stop_hz)) {
      notifications.show({ color: "red", title: "参数错误", message: "起始频率必须小于终止频率" });
      return;
    }
    if (toFiniteNumber(odmrForm.points) < 3) {
      notifications.show({ color: "red", title: "参数错误", message: "扫描点数至少为 3" });
      return;
    }
    if (isMeasurementRunning) {
      notifications.show({ color: "yellow", title: "已有任务在运行", message: "请先停止当前测量任务" });
      return;
    }
    if (!data.lockin.connected || !data.microwave.connected) {
      notifications.show({
        color: "yellow",
        title: "设备未全部连接",
        message: "锁相或微波源未连接时，ODMR 可能退回到模拟 trace",
      });
    }
    setOdmrProgress(0);
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      pendingOdmrRef.current = { ...odmrForm };
      setOdmrStatusText("等待 ODMR WebSocket 建立");
      notifications.show({
        color: "yellow",
        title: "连接中",
        message: "WebSocket 尚未打开，连接完成后会自动开始 ODMR 扫描",
      });
      return;
    }
    setOdmrStatusText("准备启动扫描");
    socket.send(JSON.stringify(odmrForm));
  };

  const stopOdmr = async () => {
    try {
      await api.stopOdmr();
      setOdmrStatusText("正在请求停止");
    } catch (err) {
      notifications.show({
        color: "red",
        title: "停止失败",
        message: err instanceof Error ? err.message : "未知错误",
      });
    }
  };

  const runSensitivity = () => {
    const socket = sensitivitySocketRef.current;
    if (isMeasurementRunning) {
      notifications.show({ color: "yellow", title: "已有任务在运行", message: "请先停止当前测量任务" });
      return;
    }
    if (!data.lockin.connected || !data.microwave.connected) {
      notifications.show({ color: "red", title: "设备未连接", message: "灵敏度测量必须同时连接锁相和微波源" });
      return;
    }
    if (toFiniteNumber(sensitivityForm.search_span_hz) <= 0) {
      notifications.show({ color: "red", title: "参数错误", message: "搜索带宽必须大于 0" });
      return;
    }
    if (toFiniteNumber(sensitivityForm.search_points) < 11) {
      notifications.show({ color: "red", title: "参数错误", message: "搜索点数至少为 11" });
      return;
    }
    if (!socket || socket.readyState !== WebSocket.OPEN) {
      pendingSensitivityRef.current = { ...sensitivityForm };
      notifications.show({
        color: "yellow",
        title: "连接中",
        message: "灵敏度 WebSocket 尚未打开，连接完成后会自动开始测量",
      });
      return;
    }
    socket.send(JSON.stringify(sensitivityForm));
  };

  const stopSensitivity = async () => {
    try {
      await api.stopSensitivity();
    } catch (err) {
      notifications.show({
        color: "red",
        title: "停止失败",
        message: err instanceof Error ? err.message : "未知错误",
      });
    }
  };

  const syncFromMicrowave = () => {
    const microwaveConfig = data.microwave.config || {};
    setOdmrForm((prev) => ({
      ...prev,
      start_hz: toFiniteNumber(microwaveConfig.sweep_start_hz, prev.start_hz),
      stop_hz: toFiniteNumber(microwaveConfig.sweep_stop_hz, prev.stop_hz),
      points: toFiniteNumber(microwaveConfig.sweep_points, prev.points),
      dwell_ms: toFiniteNumber(microwaveConfig.dwell_ms, prev.dwell_ms),
    }));
    notifications.show({ color: "teal", title: "已同步", message: "已导入微波页的扫频窗口与驻留时间" });
  };

  const syncReadoutFromLockin = () => {
    const nextSource = activeLockinChannel?.display_source ?? odmrForm.readout_source;
    setOdmrForm((prev) => ({ ...prev, readout_source: nextSource }));
    notifications.show({
      color: "teal",
      title: "已同步",
      message: `读出源已切换为 ${shortReadout(nextSource)}`,
    });
  };

  const syncSensitivityFromOdmr = () => {
    const startHz = toFiniteNumber(odmrForm.start_hz, 2.83e9);
    const stopHz = toFiniteNumber(odmrForm.stop_hz, 2.91e9);
    setSensitivityForm((prev) => ({
      ...prev,
      search_center_hz: (startHz + stopHz) / 2,
      search_span_hz: Math.abs(stopHz - startHz),
    }));
    notifications.show({ color: "teal", title: "已同步", message: "已用当前 ODMR 窗口更新灵敏度搜索窗口" });
  };

  const useDefaultResonance = () => {
    setSensitivityForm((prev) => ({ ...prev, search_center_hz: 2.87e9, search_span_hz: 20e6 }));
  };

  const exportTrace = () => {
    if (!trace.frequency_hz?.length) {
      notifications.show({ color: "yellow", title: "暂无数据", message: "当前没有可导出的 ODMR trace" });
      return;
    }
    const timestamp = buildExportTimestamp();
    downloadCsvFile(
      `odmr_${timestamp}.csv`,
      ["frequency_hz", "intensity_v", "readout_source", "scan_mode"],
      buildOdmrCsvRows(trace)
    );
    notifications.show({ color: "teal", title: "导出完成", message: "ODMR CSV 已开始下载" });
  };

  const exportOdmrJson = () => {
    if (!trace.frequency_hz?.length) {
      notifications.show({ color: "yellow", title: "暂无数据", message: "当前没有可导出的 ODMR trace" });
      return;
    }
    const timestamp = buildExportTimestamp();
    downloadJsonFile(`odmr_${timestamp}.json`, {
      exported_at: new Date().toISOString(),
      request: odmrForm,
      trace,
    });
    notifications.show({ color: "teal", title: "导出完成", message: "ODMR JSON 已开始下载" });
  };

  const exportSensitivityJson = () => {
    if (!sensitivityResult) {
      notifications.show({ color: "yellow", title: "暂无数据", message: "当前没有可导出的灵敏度结果" });
      return;
    }
    const timestamp = buildExportTimestamp();
    downloadJsonFile(`sensitivity_${timestamp}.json`, {
      exported_at: new Date().toISOString(),
      request: sensitivityForm,
      result: sensitivityResult,
    });
    notifications.show({ color: "teal", title: "导出完成", message: "灵敏度 JSON 已开始下载" });
  };

  const exportSensitivityCsvBundle = () => {
    if (!sensitivityResult) {
      notifications.show({ color: "yellow", title: "暂无数据", message: "当前没有可导出的灵敏度结果" });
      return;
    }

    const timestamp = buildExportTimestamp();
    const channelNumber = Math.max(
      1,
      Math.round(toFiniteNumber(sensitivityResult.channel_index, sensitivityForm.channel_index)) + 1
    );
    const baseName = `sensitivity_ch${channelNumber}_${timestamp}`;
    let exportedCount = 0;

    const phaseTraceRows = buildSensitivityPhaseTraceRows(sensitivityResult);
    if (phaseTraceRows.length) {
      downloadCsvFile(
        `${baseName}_phase_trace.csv`,
        ["frequency_hz", "x_v", "y_v", "selected_v", "orthogonal_v"],
        phaseTraceRows
      );
      exportedCount += 1;
    }

    const timeTraceRows = buildSensitivityTimeTraceRows(sensitivityResult);
    if (timeTraceRows.length) {
      downloadCsvFile(`${baseName}_time_trace.csv`, ["time_s", "signal_v"], timeTraceRows);
      exportedCount += 1;
    }

    const spectrumRows = buildSensitivitySpectrumRows(sensitivityResult);
    if (spectrumRows.length) {
      downloadCsvFile(
        `${baseName}_spectrum.csv`,
        ["frequency_hz", "asd_v_per_sqrt_hz", "sensitivity_t_per_sqrt_hz"],
        spectrumRows
      );
      exportedCount += 1;
    }

    const candidateRows = buildSensitivityPhaseCandidateRows(sensitivityResult);
    if (candidateRows.length) {
      downloadCsvFile(
        `${baseName}_phase_candidates.csv`,
        [
          "candidate_index",
          "phase_delta_deg",
          "phase_deg",
          "selected_axis",
          "score",
          "zero_crossing_hz",
          "slope_v_per_hz",
          "orthogonal_at_zero_v",
          "has_bracketed_zero",
          "gradient_window_points",
        ],
        candidateRows
      );
      exportedCount += 1;
    }

    if (!exportedCount) {
      notifications.show({ color: "yellow", title: "暂无数据", message: "灵敏度结果里没有可导出的曲线数据" });
      return;
    }

    notifications.show({
      color: "teal",
      title: "导出完成",
      message: `灵敏度 CSV 已开始下载，共 ${exportedCount} 个文件`,
    });
  };

  return (
    <Stack gap="lg">
      <div>
        <Text className="eyebrow">Step 4</Text>
        <Text className="page-title">ODMR 与灵敏度</Text>
        <Text c="dimmed" maw={920}>
          灵敏度流程会先在 2.87 GHz 附近自动扫频，再用整段共振的 X/Y 梯度估计相位并对候选相位做复扫验证，
          选出斜率更大、正交通道泄漏更小的结果，再锁到过零点采集时间序列，计算 ASD，并按 η = ASD / (|k| · γ · cosα) 绘制灵敏度谱线。
        </Text>
      </div>

      <SimpleGrid cols={{ base: 1, md: 3, xl: 6 }}>
        <MetricCard label="当前任务" value={measurementModeLabel(activeMode)} hint={measurementStatus} />
        <MetricCard
          label="任务进度"
          value={`${(measurementProgress * 100).toFixed(1)}%`}
          hint={`预计 ${toFiniteNumber(measurement.estimated_duration_s, 0).toFixed(2)} s`}
        />
        <MetricCard
          label="当前频率"
          value={currentMeasurementFrequencyHz ? formatGHz(currentMeasurementFrequencyHz) : "--"}
          hint={`当前值 ${formatScientific(currentMeasurementValue, 3)}`}
        />
        <MetricCard
          label="ODMR WebSocket"
          value={statusLabel(odmrSocketState)}
          hint={liveReadout ? "实时锁相读数" : "等待任务"}
        />
        <MetricCard
          label="灵敏度 WebSocket"
          value={statusLabel(sensitivitySocketState)}
          hint={isSensitivityRunning ? "正在测量" : "等待任务"}
        />
        <MetricCard
          label="最佳灵敏度"
          value={formatSensitivity(sensitivityResult?.best_sensitivity_t_per_sqrt_hz)}
          hint={bestSensitivityFrequencyHz ? `${bestSensitivityFrequencyHz.toFixed(1)} Hz` : "尚未计算"}
        />
      </SimpleGrid>

      <Progress value={measurementProgress * 100} color="cyan" radius="xl" size="lg" />

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="ODMR 扫描"
            description="用于快速查看共振位置，并把搜索窗口同步给灵敏度测量。"
            badge={isOdmrRunning ? "Running" : "Ready"}
          >
            <SimpleGrid cols={{ base: 1, md: 2 }}>
              <Select
                label="扫描模式"
                value={odmrForm.scan_mode}
                onChange={(value) => setOdmrForm((prev) => ({ ...prev, scan_mode: value || "software_sync" }))}
                data={[
                  { value: "software_sync", label: "软件同步" },
                  { value: "aux_map", label: "Aux1 映射" },
                ]}
              />
              <Select
                label="读出源"
                value={odmrForm.readout_source}
                onChange={(value) => setOdmrForm((prev) => ({ ...prev, readout_source: value || "r_v" }))}
                data={[
                  { value: "x_v", label: "X" },
                  { value: "y_v", label: "Y" },
                  { value: "r_v", label: "R" },
                ]}
              />
              <NumberInput
                label="起始频率 (Hz)"
                value={odmrForm.start_hz}
                onChange={(value) => setOdmrForm((prev) => ({ ...prev, start_hz: toFiniteNumber(value, prev.start_hz) }))}
              />
              <NumberInput
                label="终止频率 (Hz)"
                value={odmrForm.stop_hz}
                onChange={(value) => setOdmrForm((prev) => ({ ...prev, stop_hz: toFiniteNumber(value, prev.stop_hz) }))}
              />
              <NumberInput
                label="点数"
                value={odmrForm.points}
                onChange={(value) =>
                  setOdmrForm((prev) => ({ ...prev, points: Math.max(3, Math.round(toFiniteNumber(value, prev.points))) }))
                }
              />
              <NumberInput
                label="驻留时间 (ms)"
                value={odmrForm.dwell_ms}
                onChange={(value) =>
                  setOdmrForm((prev) => ({ ...prev, dwell_ms: Math.max(0.1, toFiniteNumber(value, prev.dwell_ms)) }))
                }
              />
              <NumberInput
                label="平均次数"
                value={odmrForm.averages}
                onChange={(value) =>
                  setOdmrForm((prev) => ({ ...prev, averages: Math.max(1, Math.round(toFiniteNumber(value, prev.averages))) }))
                }
              />
            </SimpleGrid>

            {odmrForm.scan_mode === "aux_map" ? (
              <SimpleGrid cols={{ base: 1, md: 2 }} mt="md">
                <NumberInput
                  label="Aux 最小电压 (V)"
                  value={odmrForm.aux_voltage_min_v}
                  onChange={(value) =>
                    setOdmrForm((prev) => ({ ...prev, aux_voltage_min_v: toFiniteNumber(value, prev.aux_voltage_min_v) }))
                  }
                />
                <NumberInput
                  label="Aux 最大电压 (V)"
                  value={odmrForm.aux_voltage_max_v}
                  onChange={(value) =>
                    setOdmrForm((prev) => ({ ...prev, aux_voltage_max_v: toFiniteNumber(value, prev.aux_voltage_max_v) }))
                  }
                />
                <NumberInput
                  label="映射最小频率 (Hz)"
                  value={odmrForm.aux_frequency_min_hz}
                  onChange={(value) =>
                    setOdmrForm((prev) => ({ ...prev, aux_frequency_min_hz: toFiniteNumber(value, prev.aux_frequency_min_hz) }))
                  }
                />
                <NumberInput
                  label="映射最大频率 (Hz)"
                  value={odmrForm.aux_frequency_max_hz}
                  onChange={(value) =>
                    setOdmrForm((prev) => ({ ...prev, aux_frequency_max_hz: toFiniteNumber(value, prev.aux_frequency_max_hz) }))
                  }
                />
              </SimpleGrid>
            ) : null}

            <Group mt="lg">
              <Button variant="light" color="gray" onClick={syncFromMicrowave}>从微波页同步</Button>
              <Button variant="light" color="gray" onClick={syncReadoutFromLockin}>跟随锁相读出</Button>
            </Group>
            <Group mt="md">
              <Button variant="light" color="gray" onClick={exportTrace} disabled={!trace.frequency_hz?.length}>
                导出 CSV
              </Button>
              <Button variant="light" color="gray" onClick={exportOdmrJson} disabled={!trace.frequency_hz?.length}>
                导出 JSON
              </Button>
            </Group>
            <Group mt="md">
              <Button onClick={runOdmr} loading={isOdmrRunning} disabled={isMeasurementRunning && !isOdmrRunning}>
                开始扫描
              </Button>
              <Button color="red" variant="light" onClick={stopOdmr} disabled={!isOdmrRunning}>
                停止扫描
              </Button>
            </Group>

            <SimpleGrid cols={{ base: 1, md: 3 }} mt="lg">
              <MetricCard label="扫描状态" value={odmrStatusText} hint={range} />
              <MetricCard
                label="当前点"
                value={`${currentPoint}/${odmrForm.points}`}
                hint={`进度 ${(odmrProgress * 100).toFixed(1)}%`}
              />
              <MetricCard
                label="实时值"
                value={formatScientific(currentValue, 4)}
                hint={`最小 ${formatScientific(yMin, 3)} | 最大 ${formatScientific(yMax, 3)}`}
              />
            </SimpleGrid>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="ODMR 光谱"
            description="默认保留当前 trace，便于先扫共振窗口，再进入灵敏度测量。"
            badge={shortReadout(trace.readout_source)}
          >
            <PlotCard
              x={(trace.frequency_hz || []).map((item) => toFiniteNumber(item, 0) / 1e9)}
              y={trace.intensity || []}
              xTitle="Microwave Frequency (GHz)"
              yTitle={`Lock-in ${shortReadout(trace.readout_source)} (V)`}
              lineColor="#64e4c2"
            />
          </SectionCard>
        </Grid.Col>
      </Grid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="灵敏度测量"
            description="自动调相位会先估计候选相位并复扫验证，再在过零点附近采集固定频率时间序列生成 ASD 与灵敏度谱。"
            badge={isSensitivityRunning ? "Running" : "Ready"}
          >
            <SimpleGrid cols={{ base: 1, md: 2 }}>
              <NumberInput
                label="锁相通道"
                value={sensitivityForm.channel_index}
                min={0}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({
                    ...prev,
                    channel_index: Math.max(0, Math.round(toFiniteNumber(value, prev.channel_index))),
                  }))
                }
              />
              <Select
                label="优先通道"
                value={sensitivityForm.phase_target}
                onChange={(value) => setSensitivityForm((prev) => ({ ...prev, phase_target: value || "auto" }))}
                data={[
                  { value: "auto", label: "自动选择" },
                  { value: "x_v", label: "优先 X" },
                  { value: "y_v", label: "优先 Y" },
                ]}
              />
              <NumberInput
                label="搜索中心 (Hz)"
                value={sensitivityForm.search_center_hz}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({ ...prev, search_center_hz: toFiniteNumber(value, prev.search_center_hz) }))
                }
              />
              <NumberInput
                label="搜索带宽 (Hz)"
                value={sensitivityForm.search_span_hz}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({ ...prev, search_span_hz: Math.max(1, toFiniteNumber(value, prev.search_span_hz)) }))
                }
              />
              <NumberInput
                label="搜索点数"
                value={sensitivityForm.search_points}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({ ...prev, search_points: Math.max(11, Math.round(toFiniteNumber(value, prev.search_points))) }))
                }
              />
              <NumberInput
                label="稳定等待 (ms)"
                value={sensitivityForm.settle_ms}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({ ...prev, settle_ms: Math.max(1, toFiniteNumber(value, prev.settle_ms)) }))
                }
              />
              <NumberInput
                label="斜率拟合点数"
                value={sensitivityForm.slope_fit_points}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({
                    ...prev,
                    slope_fit_points: Math.max(3, Math.round(toFiniteNumber(value, prev.slope_fit_points))),
                  }))
                }
              />
              <NumberInput
                label="ASD 采集时长 (s)"
                value={sensitivityForm.asd_duration_s}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({ ...prev, asd_duration_s: Math.max(1, toFiniteNumber(value, prev.asd_duration_s)) }))
                }
              />
              <NumberInput
                label="ASD 最低频率 (Hz)"
                value={sensitivityForm.asd_min_frequency_hz}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({ ...prev, asd_min_frequency_hz: Math.max(0, toFiniteNumber(value, prev.asd_min_frequency_hz)) }))
                }
              />
              <NumberInput
                label="cosα"
                value={sensitivityForm.cos_alpha}
                precision={4}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({ ...prev, cos_alpha: Math.min(1, Math.max(0.0001, toFiniteNumber(value, prev.cos_alpha))) }))
                }
              />
              <NumberInput
                label="γ (Hz/T)"
                value={sensitivityForm.gamma_hz_per_t}
                onChange={(value) =>
                  setSensitivityForm((prev) => ({ ...prev, gamma_hz_per_t: Math.max(1, toFiniteNumber(value, prev.gamma_hz_per_t)) }))
                }
              />
            </SimpleGrid>

            <Group mt="lg">
              <Button variant="light" color="gray" onClick={syncSensitivityFromOdmr}>继承 ODMR 窗口</Button>
              <Button variant="light" color="gray" onClick={useDefaultResonance}>回到 2.87 GHz</Button>
            </Group>
            <Group mt="md">
              <Button color="cyan" onClick={runSensitivity} loading={isSensitivityRunning} disabled={isMeasurementRunning && !isSensitivityRunning}>
                开始灵敏度测量
              </Button>
              <Button color="red" variant="light" onClick={stopSensitivity} disabled={!isSensitivityRunning}>
                停止灵敏度测量
              </Button>
            </Group>
            <Group mt="md">
              <Button variant="light" color="gray" onClick={exportSensitivityJson} disabled={!sensitivityResult}>
                导出结果 JSON
              </Button>
              <Button variant="light" color="gray" onClick={exportSensitivityCsvBundle} disabled={!sensitivityResult}>
                导出全部 CSV
              </Button>
            </Group>
            <Text c="dimmed" size="sm" mt="xs">
              灵敏度 CSV 会包含调相扫频、固定频率时间序列、ASD/灵敏度谱和候选相位对比。
            </Text>

            <SimpleGrid cols={{ base: 1, md: 2 }} mt="lg">
              <MetricCard label="选择通道" value={axisLabel(sensitivityResult?.selected_axis)} hint={`正交通道 ${axisLabel(sensitivityResult?.orthogonal_axis)}`} />
              <MetricCard
                label="相位"
                value={Number.isFinite(toFiniteNumber(sensitivityResult?.optimized_phase_deg, NaN)) ? `${toFiniteNumber(sensitivityResult?.optimized_phase_deg, 0).toFixed(2)}°` : "--"}
                hint={`估计修正 ${toFiniteNumber(sensitivityResult?.phase_delta_deg, 0).toFixed(2)}°`}
              />
              <MetricCard
                label="过零点"
                value={sensitivityResult?.zero_crossing_hz ? formatGHz(sensitivityResult.zero_crossing_hz) : "--"}
                hint={`斜率 ${formatSlope(sensitivityResult?.slope_uv_per_mhz)}`}
              />
              <MetricCard
                label="采样率"
                value={sensitivityResult?.sample_rate_hz ? `${toFiniteNumber(sensitivityResult.sample_rate_hz, 0).toFixed(2)} Hz` : "--"}
                hint={`预计 ${sensitivityEstimatedDuration.toFixed(2)} s`}
              />
            </SimpleGrid>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="调相与过零点"
            description="自动相位优化后，所选通道应在共振附近呈最大斜率，正交通道接近 0。"
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
              shapes={phasePlotShapes}
              annotations={phasePlotAnnotations}
              uirevision={phaseRevision}
            />
          </SectionCard>
        </Grid.Col>
      </Grid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 4 }}>
          <SectionCard title="固定频率时间序列" description="过零点锁定后用于 ASD 的原始时间序列。" badge={axisLabel(selectedAxis)}>
            <PlotCard
              x={timeTrace.time_s || []}
              y={timeTrace.signal_v || []}
              xTitle="Time (s)"
              yTitle={`${axisLabel(selectedAxis)} (V)`}
              xRange={timeXRange}
              yRange={timeYRange}
              lineColor="#8ab4ff"
              uirevision={timeRevision}
            />
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 4 }}>
          <SectionCard title="ASD 频谱" description="固定频率噪声谱，用于后续换算磁场灵敏度。" badge="ASD">
            <PlotCard
              x={asdSpectrum.frequency_hz || []}
              y={asdSpectrum.asd_v_per_sqrt_hz || []}
              xTitle="Frequency (Hz)"
              yTitle="ASD (V/√Hz)"
              xScale="log"
              yScale="log"
              xRange={asdXRange}
              yRange={asdYRange}
              lineColor="#7ae4ff"
              uirevision={asdRevision}
            />
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 4 }}>
          <SectionCard title="灵敏度谱线" description="由 ASD、最大斜率和 γ 换算得到的磁场灵敏度。" badge="η">
            <PlotCard
              traces={[
                {
                  name: "灵敏度",
                  x: asdSpectrum.frequency_hz || [],
                  y: asdSpectrum.sensitivity_t_per_sqrt_hz || [],
                  lineColor: "#64e4c2",
                  hovertemplate: "%{x:.2f} Hz<br>%{y:.3e} T/√Hz<extra></extra>",
                },
                ...(bestSensitivityFrequencyPlot && Number.isFinite(bestSensitivityY)
                  ? [
                      {
                        name: "最佳点",
                        x: [bestSensitivityFrequencyPlot],
                        y: [bestSensitivityY],
                        lineColor: "#ff7a90",
                        mode: "markers",
                        marker: { size: 10, color: "#ff7a90" },
                        hovertemplate: "%{x:.2f} Hz<br>%{y:.3e} T/√Hz<extra></extra>",
                      },
                    ]
                  : []),
              ]}
              xTitle="Frequency (Hz)"
              yTitle="Sensitivity (T/√Hz)"
              xScale="log"
              yScale="log"
              xRange={sensitivityXRange}
              yRange={sensitivityYRange}
              annotations={bestSensitivityFrequencyPlot && Number.isFinite(bestSensitivityY) ? [{ x: bestSensitivityFrequencyPlot, y: bestSensitivityY, text: formatSensitivity(bestSensitivity), showarrow: true, arrowhead: 2, ax: 28, ay: -36, font: { color: "#ff7a90", size: 11 } }] : []}
              uirevision={sensitivityRevision}
            />
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard title="测量说明" description="灵敏度计算现在基于真实锁相 X/Y 通道，不再使用始终为正的 R。">
        <List spacing="md" size="sm" c="dimmed">
          <List.Item>自动调相位会先用整段共振附近的 X/Y 梯度估计主方向，再对候选相位做复扫验证，减少单点噪声把相位带偏。</List.Item>
          <List.Item>过零点通过共振附近的符号翻转和局部线性拟合得到，斜率用局部拟合窗口计算。</List.Item>
          <List.Item>ASD 使用固定频率时间序列的单边谱，灵敏度按 η = ASD / (|k| · γ · cosα) 换算。</List.Item>
          <List.Item>如果搜索通道选到外参考跟踪通道，后端会自动切回其配对的测量通道执行灵敏度计算。</List.Item>
        </List>
      </SectionCard>
    </Stack>
  );
}
