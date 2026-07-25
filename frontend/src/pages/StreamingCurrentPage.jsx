import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Grid,
  Group,
  NumberInput,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import {
  IconAlertTriangle,
  IconDownload,
  IconPlayerPlay,
  IconPlayerStop,
} from "@tabler/icons-react";

import { MetricCard } from "../components/MetricCard";
import { PlotCard } from "../components/PlotCard";
import { SectionCard } from "../components/SectionCard";
import { useDashboard } from "../hooks/useDashboard";
import { api, wsUrl } from "../lib/api";

const CALIBRATION_STORAGE_KEY = "nv-current-physical-calibration-v3";
const FORM_STORAGE_KEY = "nv-streaming-current-form-v1";
const MAX_PLOT_POINTS = 600;
const UI_REFRESH_INTERVAL_MS = 100;

const DEFAULT_FORM = {
  channel_index: 0,
  independent_dc_channel_index: -1,
  start_hz: 2.83e9,
  stop_hz: 2.91e9,
  search_points: 121,
  search_settle_ms: 10,
  probe_offset_hz: 250000,
  tracking_settle_ms: 5,
  sample_averages: 1,
  timing_report_interval_cycles: 10,
  kp: 0.45,
  ki_per_s: 0.03,
  kd_s: 0,
  derivative_filter_tau_s: 0.1,
  antiwindup_gain_per_s: 1,
  max_step_hz: 500000,
  maximum_slew_hz_per_s: 1e7,
  integral_limit_hz: 1e6,
  lock_error_limit_hz: 1.5e6,
  verify_interval_visits: 20,
  maximum_delta_f_sigma_hz: 2e6,
  delta_f_min_hz: 0,
  delta_f_max_hz: 1e9,
  max_relock_attempts: 5,
  max_tracking_duration_s: 0,
  stream_sample_rate_hz: 2000,
  stream_poll_window_ms: 5,
  stream_poll_timeout_ms: 250,
  stream_timestamp_margin_ms: 1,
  stream_warmup_timeout_s: 2,
  record_enabled: true,
  record_label: "",
  record_batch_points: 100,
  record_flush_interval_s: 1,
  record_queue_capacity: 100000,
};

function finite(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function loadForm() {
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(FORM_STORAGE_KEY) || "{}"
    );
    return { ...DEFAULT_FORM, ...stored };
  } catch {
    return DEFAULT_FORM;
  }
}

function loadCalibrationPoints() {
  try {
    const stored = JSON.parse(
      window.localStorage.getItem(CALIBRATION_STORAGE_KEY) || "{}"
    );
    const points = Array.isArray(stored) ? stored : stored?.points;
    return (Array.isArray(points) ? points : []).filter(
      (point) =>
        point?.source === "physical_peak_tracking" &&
        Number.isFinite(Number(point.current_a)) &&
        Number.isFinite(Number(point.resonance_splitting_hz)) &&
        Number(point.resonance_splitting_hz) > 0
    );
  } catch {
    return [];
  }
}

function fitCalibration(points) {
  if (points.length < 2) {
    return null;
  }
  const pairs = points.map((point) => ({
    x: Number(point.resonance_splitting_hz),
    y: Number(point.current_a),
  }));
  const count = pairs.length;
  const sumX = pairs.reduce((sum, point) => sum + point.x, 0);
  const sumY = pairs.reduce((sum, point) => sum + point.y, 0);
  const sumXX = pairs.reduce((sum, point) => sum + point.x ** 2, 0);
  const sumXY = pairs.reduce((sum, point) => sum + point.x * point.y, 0);
  const denominator = count * sumXX - sumX ** 2;
  if (Math.abs(denominator) < 1e-30) {
    return null;
  }
  return {
    slope_a_per_hz: (count * sumXY - sumX * sumY) / denominator,
    intercept_a:
      (sumY -
        ((count * sumXY - sumX * sumY) / denominator) * sumX) /
      count,
    delta_f_min_hz: Math.min(...pairs.map((point) => point.x)),
    delta_f_max_hz: Math.max(...pairs.map((point) => point.x)),
    point_count: count,
  };
}

function formatGHz(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? `${(numeric / 1e9).toFixed(9)} GHz`
    : "--";
}

function formatMHz(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? `${(numeric / 1e6).toFixed(6)} MHz`
    : "--";
}

function formatKHz(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? `${(numeric / 1e3).toFixed(3)} kHz`
    : "--";
}

function formatMs(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(2)} ms` : "--";
}

function formatCurrent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  return Math.abs(numeric) >= 1
    ? `${numeric.toFixed(6)} A`
    : `${(numeric * 1e3).toFixed(3)} mA`;
}

function formatBytes(value) {
  const numeric = finite(value);
  if (numeric >= 1024 ** 3) {
    return `${(numeric / 1024 ** 3).toFixed(2)} GB`;
  }
  if (numeric >= 1024 ** 2) {
    return `${(numeric / 1024 ** 2).toFixed(2)} MB`;
  }
  if (numeric >= 1024) {
    return `${(numeric / 1024).toFixed(2)} KB`;
  }
  return `${numeric.toFixed(0)} B`;
}

function saveBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function phaseMessage(globalState) {
  if (globalState === "FULL_REACQUIRE") {
    return "正在执行全频段重新扫峰";
  }
  if (globalState === "CALIBRATE") {
    return "正在用连续订阅样本标定复数 b/g";
  }
  if (globalState === "FULL_SCAN") {
    return "正在用连续订阅样本完成初始扫峰";
  }
  return "流式跟踪运行中";
}

export default function StreamingCurrentPage() {
  const { data, error, loading, refresh } = useDashboard(1500);
  const [form, setForm] = useState(loadForm);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState("等待启动");
  const [latestPoint, setLatestPoint] = useState(null);
  const [points, setPoints] = useState([]);
  const [timing, setTiming] = useState(null);
  const [streamDiagnostics, setStreamDiagnostics] = useState(null);
  const [recording, setRecording] = useState(null);
  const [relockCount, setRelockCount] = useState(0);
  const socketRef = useRef(null);
  const terminalRef = useRef(true);
  const hydratedRef = useRef(false);
  const pendingPointBatchRef = useRef([]);
  const uiFlushTimerRef = useRef(null);
  const calibrationPoints = useMemo(loadCalibrationPoints, [data]);
  const calibration = useMemo(
    () => fitCalibration(calibrationPoints),
    [calibrationPoints]
  );

  useEffect(() => {
    window.localStorage.setItem(FORM_STORAGE_KEY, JSON.stringify(form));
  }, [form]);

  useEffect(() => {
    api
      .streamingCurrentRecordingStatus()
      .then((result) => setRecording(result?.data || null))
      .catch(() => {});
  }, []);

  useEffect(() => {
    if (
      hydratedRef.current ||
      !data?.measurement?.last_streaming_current_request
    ) {
      return;
    }
    setForm((current) => ({
      ...current,
      ...data.measurement.last_streaming_current_request,
    }));
    hydratedRef.current = true;
  }, [data]);

  useEffect(
    () => () => {
      socketRef.current?.close();
      if (uiFlushTimerRef.current !== null) {
        window.clearTimeout(uiFlushTimerRef.current);
      }
    },
    []
  );

  const updateNumber = (field, minimum = undefined) => (value) => {
    const numeric = finite(value, form[field]);
    setForm((current) => ({
      ...current,
      [field]:
        minimum === undefined ? numeric : Math.max(minimum, numeric),
    }));
  };

  const start = () => {
    if (!data?.lockin?.connected || !data?.microwave?.connected) {
      notifications.show({
        color: "red",
        title: "设备未连接",
        message: "请先连接Zurich锁相和微波源。",
      });
      return;
    }
    if (!calibration) {
      notifications.show({
        color: "yellow",
        title: "缺少物理峰心标定",
        message: "请先在电流测量页建立至少两个Δf↔I标定点。",
      });
      return;
    }
    if (form.stop_hz <= form.start_hz) {
      notifications.show({
        color: "red",
        title: "扫频范围无效",
        message: "终止频率必须大于起始频率。",
      });
      return;
    }

    socketRef.current?.close();
    terminalRef.current = false;
    setRunning(true);
    setStatus("正在建立流式测量连接");
    setLatestPoint(null);
    setPoints([]);
    setTiming(null);
    setStreamDiagnostics(null);
    setRelockCount(0);
    pendingPointBatchRef.current = [];
    if (uiFlushTimerRef.current !== null) {
      window.clearTimeout(uiFlushTimerRef.current);
      uiFlushTimerRef.current = null;
    }

    const socket = new WebSocket(wsUrl("/streaming-current/ws"));
    socketRef.current = socket;
    const flushPendingPoints = () => {
      uiFlushTimerRef.current = null;
      const batch = pendingPointBatchRef.current;
      pendingPointBatchRef.current = [];
      if (batch.length === 0) {
        return;
      }
      const point = batch[batch.length - 1];
      setLatestPoint(point);
      setPoints((previous) => {
        const next = [...previous, ...batch];
        return next.length > MAX_PLOT_POINTS
          ? next.slice(next.length - MAX_PLOT_POINTS)
          : next;
      });
      setTiming((previous) => point.timing || previous);
      setStreamDiagnostics((previous) => point.stream || previous);
      setRelockCount(finite(point.relock_count));
      if (!terminalRef.current) {
        setStatus(
          point.valid
            ? "连续订阅双峰锁定，输出有效"
            : `输出无效：${point.invalid_reason || "等待锁定"}`
        );
      }
    };
    const schedulePointRefresh = (point) => {
      pendingPointBatchRef.current.push(point);
      if (uiFlushTimerRef.current === null) {
        uiFlushTimerRef.current = window.setTimeout(
          flushPendingPoints,
          UI_REFRESH_INTERVAL_MS
        );
      }
    };
    const flushBeforeTerminalState = () => {
      if (uiFlushTimerRef.current !== null) {
        window.clearTimeout(uiFlushTimerRef.current);
      }
      flushPendingPoints();
    };
    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          ...form,
          minimum_calibration_slope_a_per_hz: calibration.slope_a_per_hz,
          minimum_calibration_intercept_a: calibration.intercept_a,
          calibration_delta_f_min_hz: calibration.delta_f_min_hz,
          calibration_delta_f_max_hz: calibration.delta_f_max_hz,
        })
      );
    };
    socket.onmessage = (message) => {
      const payload = JSON.parse(message.data);
      if (payload.type === "streaming_current_started") {
        setStatus("正在暂停普通poll并建立目标Demod连续订阅");
      } else if (payload.type === "streaming_current_state") {
        setStatus(phaseMessage(payload.global_state));
      } else if (payload.type === "streaming_current_models_calibrated") {
        setStatus("流式复数模型已标定，正在确认双峰锁定");
      } else if (payload.type === "streaming_current_local_reacquire") {
        setStatus(
          payload.stage === "start"
            ? `${payload.peak_id === "left" ? "左峰" : "右峰"}流式局部重捕获`
            : payload.stage === "success"
              ? "局部重捕获成功"
              : "局部重捕获失败，准备全频重扫"
        );
      } else if (payload.type === "streaming_current_timing") {
        setTiming(payload.timing || null);
        setStreamDiagnostics(payload.stream || null);
      } else if (payload.type === "streaming_current_recording") {
        setRecording(payload.recording || null);
      } else if (payload.type === "streaming_current_point") {
        const point = payload.point || {};
        schedulePointRefresh(point);
      } else if (payload.type === "streaming_current_lock_lost") {
        setStatus("检测到失锁，正在使用流式样本重扫");
        setRelockCount(finite(payload.relock_count));
      } else if (
        payload.type === "streaming_current_complete" ||
        payload.type === "streaming_current_cancelled"
      ) {
        flushBeforeTerminalState();
        terminalRef.current = true;
        setRunning(false);
        setStatus(
          payload.type === "streaming_current_cancelled"
            ? "流式测量已停止，CSV队列已写完"
            : "设定时长完成，CSV队列已写完"
        );
        setRecording(
          (previous) => payload.result?.recording || previous
        );
        refresh();
      } else if (payload.type === "streaming_current_error") {
        flushBeforeTerminalState();
        terminalRef.current = true;
        setRunning(false);
        setRecording(
          (previous) => payload.recording || previous
        );
        setStatus(payload.message || "流式测量异常");
        notifications.show({
          color: "red",
          title: "流式测量失败",
          message: payload.message || "未知错误",
        });
        refresh();
      }
    };
    socket.onerror = () => setStatus("流式测量WebSocket异常");
    socket.onclose = () => {
      if (!terminalRef.current) {
        flushBeforeTerminalState();
        terminalRef.current = true;
        setRunning(false);
        setStatus("连接中断，后端已请求安全停止");
      }
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
    };
  };

  const stop = async () => {
    try {
      await api.stopStreamingCurrent();
      setStatus("正在停止；等待CSV队列写完");
    } catch (requestError) {
      notifications.show({
        color: "red",
        title: "停止失败",
        message:
          requestError instanceof Error
            ? requestError.message
            : "停止请求失败",
      });
    }
  };

  const downloadCsv = async () => {
    try {
      const result = await api.downloadStreamingCurrentRecording(
        recording?.session_id || ""
      );
      saveBlob(result.blob, result.filename);
    } catch (requestError) {
      notifications.show({
        color: "red",
        title: "CSV下载失败",
        message:
          requestError instanceof Error
            ? requestError.message
            : "下载请求失败",
      });
    }
  };

  if (!data) {
    return (
      <Stack gap="md">
        <Text className="page-title">流式电流测量</Text>
        <Text c="dimmed">
          {error || (loading ? "正在加载..." : "后端数据为空")}
        </Text>
      </Stack>
    );
  }

  const measurement = data.measurement || {};
  const otherTaskRunning =
    Boolean(measurement.running) &&
    measurement.mode !== "streaming_current_tracking";
  const updateRate = finite(
    timing?.measured_update_rate_hz,
    finite(latestPoint?.timing?.measured_update_rate_hz)
  );
  const elapsed = points.map((point) => finite(point.elapsed_s));
  const queueUsage =
    100 *
    finite(recording?.queue_depth) /
    Math.max(1, finite(recording?.queue_capacity, 1));

  return (
    <Stack gap="lg">
      <div>
        <Text className="eyebrow">Continuous Subscription</Text>
        <Text className="page-title">流式电流测量</Text>
        <Text c="dimmed" maw={1080}>
          独立使用Zurich目标Demod连续订阅和设备时间戳缓冲区。微波捷变后等待锁相稳定，
          直接取得稳定时刻之后的新X/Y样本，不调用同步demod.sample()。PID、失锁检测、
          自动重扫和本页CSV记录状态均与原电流页隔离。
        </Text>
      </div>

      {!calibration ? (
        <Alert
          color="yellow"
          icon={<IconAlertTriangle size={18} />}
          title="缺少物理峰心标定"
        >
          请先在“电流测量”页完成I=a(fR-fL)+b标定。流式页面不会使用旧过零点标定。
        </Alert>
      ) : null}

      <SimpleGrid cols={{ base: 1, sm: 2, xl: 4 }}>
        <MetricCard
          label="实时电流"
          value={
            latestPoint?.valid
              ? formatCurrent(latestPoint.estimated_current_a)
              : "--"
          }
          hint={
            latestPoint?.valid
              ? `1σ ${formatCurrent(latestPoint.current_sigma_a)}`
              : latestPoint?.invalid_reason || "等待有效输出"
          }
        />
        <MetricCard
          label="双峰更新率"
          value={updateRate > 0 ? `${updateRate.toFixed(2)} Hz` : "--"}
          hint={`周期P50 ${formatMs(timing?.cycle_median_ms)}`}
        />
        <MetricCard
          label="Zurich流采样率"
          value={
            finite(streamDiagnostics?.measured_sample_rate_hz) > 0
              ? `${finite(
                  streamDiagnostics.measured_sample_rate_hz
                ).toFixed(1)} Hz`
              : "--"
          }
          hint={`请求 ${finite(
            streamDiagnostics?.requested_sample_rate_hz,
            form.stream_sample_rate_hz
          ).toFixed(0)} Hz`}
        />
        <MetricCard
          label="CSV逐输出点保存"
          value={`${finite(recording?.rows_written).toFixed(0)} 行`}
          hint={`入队 ${finite(recording?.enqueued_rows).toFixed(
            0
          )}，丢失 ${finite(recording?.dropped_rows).toFixed(0)}`}
        />
        <MetricCard
          label="左峰 fL"
          value={formatGHz(latestPoint?.left_frequency_hz)}
          hint={`误差 ${formatKHz(latestPoint?.left_error_hz)}`}
        />
        <MetricCard
          label="右峰 fR"
          value={formatGHz(latestPoint?.right_frequency_hz)}
          hint={`误差 ${formatKHz(latestPoint?.right_error_hz)}`}
        />
        <MetricCard
          label="物理劈裂 Δf"
          value={formatMHz(latestPoint?.splitting_hz)}
          hint={`1σ ${formatKHz(latestPoint?.delta_f_sigma_hz)}`}
        />
        <MetricCard
          label="自动重扫"
          value={String(relockCount)}
          hint={status}
        />
      </SimpleGrid>

      <SectionCard
        title="流式采样时间分析"
        description="普通50 ms后台poll在本页运行期间暂停；这里的“订阅样本等待”是等待稳定时间之后的新设备时间戳样本，不是同步sample()调用。"
        badge="No demod.sample()"
      >
        <SimpleGrid cols={{ base: 1, md: 2, xl: 4 }}>
          <MetricCard
            label="单频点采集"
            value={formatMs(timing?.acquisition_median_ms)}
            hint={`P95 ${formatMs(timing?.acquisition_p95_ms)}`}
          />
          <MetricCard
            label="微波SCPI"
            value={formatMs(timing?.stage_mean_ms?.microwave_command_ms)}
            hint="VISA写频返回耗时"
          />
          <MetricCard
            label="显式稳定等待"
            value={formatMs(timing?.stage_mean_ms?.settle_ms)}
            hint={`配置 ${form.tracking_settle_ms.toFixed(2)} ms`}
          />
          <MetricCard
            label="订阅样本等待"
            value={formatMs(timing?.stage_mean_ms?.lockin_read_ms)}
            hint="等待设备时间戳越过稳定时刻"
          />
          <MetricCard
            label="设备锁等待"
            value={formatMs(timing?.stage_mean_ms?.lock_wait_ms)}
            hint="流式模式应接近0 ms"
          />
          <MetricCard
            label="流式poll P50/P95"
            value={formatMs(streamDiagnostics?.poll_p50_ms)}
            hint={`P95 ${formatMs(streamDiagnostics?.poll_p95_ms)}`}
          />
          <MetricCard
            label="最新样本龄"
            value={formatMs(latestPoint?.stream_sample_age_ms)}
            hint={`序号 ${latestPoint?.stream_sequence || 0}`}
          />
          <MetricCard
            label="订阅累计样本"
            value={finite(
              streamDiagnostics?.samples_received
            ).toLocaleString()}
            hint={`poll窗口 ${finite(
              streamDiagnostics?.poll_window_ms,
              form.stream_poll_window_ms
            ).toFixed(1)} ms`}
          />
        </SimpleGrid>
      </SectionCard>

      <SectionCard
        title="Zurich连续订阅与扫峰参数"
        description="采样率是Demod数据流速率；poll窗口决定批次交付延迟。提高采样率不会突破锁相滤波器本身的有效带宽。"
        badge="Independent Buffer"
      >
        <Grid>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="锁相通道"
              value={form.channel_index}
              min={0}
              step={1}
              disabled={running}
              onChange={updateNumber("channel_index", 0)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="扫频起点 (Hz)"
              value={form.start_hz}
              step={1e6}
              disabled={running}
              onChange={updateNumber("start_hz")}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="扫频终点 (Hz)"
              value={form.stop_hz}
              step={1e6}
              disabled={running}
              onChange={updateNumber("stop_hz")}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="完整扫频点数"
              value={form.search_points}
              min={11}
              step={2}
              disabled={running}
              onChange={updateNumber("search_points", 11)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="Demod流采样率 (Hz)"
              value={form.stream_sample_rate_hz}
              min={100}
              step={500}
              disabled={running}
              onChange={updateNumber("stream_sample_rate_hz", 100)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="poll记录窗口 (ms)"
              value={form.stream_poll_window_ms}
              min={1}
              step={1}
              disabled={running}
              onChange={updateNumber("stream_poll_window_ms", 1)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="稳定后时间戳余量 (ms)"
              value={form.stream_timestamp_margin_ms}
              min={0}
              step={0.5}
              disabled={running}
              onChange={updateNumber("stream_timestamp_margin_ms", 0)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="样本等待超时 (ms)"
              value={form.stream_poll_timeout_ms}
              min={10}
              step={10}
              disabled={running}
              onChange={updateNumber("stream_poll_timeout_ms", 10)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="跟踪稳定等待 (ms)"
              value={form.tracking_settle_ms}
              min={0.1}
              step={0.5}
              disabled={running}
              onChange={updateNumber("tracking_settle_ms", 0.1)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="单点平均样本数"
              value={form.sample_averages}
              min={1}
              step={1}
              disabled={running}
              onChange={updateNumber("sample_averages", 1)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="峰心探测偏移 (Hz)"
              value={form.probe_offset_hz}
              min={1}
              step={50000}
              disabled={running}
              onChange={updateNumber("probe_offset_hz", 1)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="最大运行时长 (s，0=不限)"
              value={form.max_tracking_duration_s}
              min={0}
              step={60}
              disabled={running}
              onChange={updateNumber("max_tracking_duration_s", 0)}
            />
          </Grid.Col>
        </Grid>
      </SectionCard>

      <SectionCard
        title="PID与失锁参数"
        description="与原PID页使用同一套已验证的控制器类，但本页持有独立PID对象和状态。"
        badge="Same Controller · New State"
      >
        <Grid>
          {[
            ["kp", "Kp", 0, 0.05],
            ["ki_per_s", "Ki (1/s)", 0, 0.01],
            ["kd_s", "Kd (s)", 0, 0.01],
            [
              "derivative_filter_tau_s",
              "微分滤波时间常数 (s)",
              0,
              0.05,
            ],
            ["antiwindup_gain_per_s", "抗饱和反算增益 (1/s)", 0, 0.1],
            ["max_step_hz", "单周期最大校正 (Hz)", 1, 50000],
            ["maximum_slew_hz_per_s", "最大变化率 (Hz/s)", 1, 1e6],
            ["integral_limit_hz", "积分项限幅 (Hz)", 0, 100000],
            ["lock_error_limit_hz", "失锁偏差阈值 (Hz)", 1, 100000],
            ["verify_interval_visits", "b/g验证间隔 (次/峰)", 1, 1],
            ["max_relock_attempts", "最大自动重扫次数", 0, 1],
          ].map(([field, label, minimum, step]) => (
            <Grid.Col key={field} span={{ base: 12, md: 4, xl: 3 }}>
              <NumberInput
                label={label}
                value={form[field]}
                min={minimum}
                step={step}
                disabled={running}
                onChange={updateNumber(field, minimum)}
              />
            </Grid.Col>
          ))}
        </Grid>
      </SectionCard>

      <SectionCard
        title="逐输出点CSV批量记录"
        description="每个双峰电流输出都进入有界内存队列；独立线程累计到指定点数或时间后一次writerows写盘。停止时会写完队列，不生成Excel。"
        badge="Lossless Queue"
      >
        <Grid align="end">
          <Grid.Col span={{ base: 12, md: 3 }}>
            <Switch
              label="保存全部电流输出"
              checked={Boolean(form.record_enabled)}
              disabled={running}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  record_enabled: event.currentTarget.checked,
                }))
              }
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <TextInput
              label="实验标签"
              value={form.record_label}
              disabled={running || !form.record_enabled}
              onChange={(event) =>
                setForm((current) => ({
                  ...current,
                  record_label: event.currentTarget.value,
                }))
              }
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 2 }}>
            <NumberInput
              label="每批点数"
              value={form.record_batch_points}
              min={1}
              disabled={running || !form.record_enabled}
              onChange={updateNumber("record_batch_points", 1)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 2 }}>
            <NumberInput
              label="最长批量间隔 (s)"
              value={form.record_flush_interval_s}
              min={0.05}
              step={0.1}
              disabled={running || !form.record_enabled}
              onChange={updateNumber("record_flush_interval_s", 0.05)}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 2 }}>
            <NumberInput
              label="队列容量"
              value={form.record_queue_capacity}
              min={1000}
              step={10000}
              disabled={running || !form.record_enabled}
              onChange={updateNumber("record_queue_capacity", 1000)}
            />
          </Grid.Col>
        </Grid>

        <SimpleGrid cols={{ base: 1, md: 2, xl: 4 }} mt="md">
          <MetricCard
            label="队列深度"
            value={`${finite(recording?.queue_depth).toFixed(0)} / ${finite(
              recording?.queue_capacity
            ).toFixed(0)}`}
            hint={`占用 ${queueUsage.toFixed(3)}%`}
          />
          <MetricCard
            label="磁盘写批次"
            value={finite(recording?.write_batches).toFixed(0)}
            hint={`平均 ${finite(
              recording?.average_rows_per_batch
            ).toFixed(1)} 行/批`}
          />
          <MetricCard
            label="最近批量写耗时"
            value={formatMs(recording?.last_write_duration_ms)}
            hint={`最近 ${finite(
              recording?.last_write_batch_size
            ).toFixed(0)} 行`}
          />
          <MetricCard
            label="CSV大小"
            value={formatBytes(recording?.csv_size_bytes)}
            hint={recording?.session_id || "尚未创建记录"}
          />
        </SimpleGrid>
      </SectionCard>

      <Group>
        <Button
          leftSection={<IconPlayerPlay size={18} />}
          onClick={start}
          disabled={
            running ||
            otherTaskRunning ||
            !calibration ||
            !data.lockin.connected ||
            !data.microwave.connected
          }
        >
          启动流式双峰跟踪
        </Button>
        <Button
          leftSection={<IconPlayerStop size={18} />}
          color="red"
          variant="light"
          onClick={stop}
          disabled={!running}
        >
          停止并写完CSV
        </Button>
        <Button
          leftSection={<IconDownload size={18} />}
          variant="light"
          color="gray"
          onClick={downloadCsv}
          disabled={!recording?.download_available}
        >
          下载CSV{running ? "快照" : ""}
        </Button>
        <Button variant="subtle" color="gray" onClick={refresh}>
          刷新设备状态
        </Button>
        {otherTaskRunning ? (
          <Text c="yellow">其他测量正在运行：{measurement.mode}</Text>
        ) : null}
      </Group>

      <SimpleGrid cols={{ base: 1, xl: 2 }}>
        <SectionCard
          title="左右物理共振峰"
          description="显示最近600个双峰输出点；完整CSV不受绘图点数限制。"
        >
          <PlotCard
            traces={[
              {
                name: "fL",
                x: elapsed,
                y: points.map(
                  (point) => finite(point.left_frequency_hz) / 1e9
                ),
                lineColor: "#5ad1ff",
              },
              {
                name: "fR",
                x: elapsed,
                y: points.map(
                  (point) => finite(point.right_frequency_hz) / 1e9
                ),
                lineColor: "#45e0a8",
              },
            ]}
            xTitle="运行时间 (s)"
            yTitle="频率 (GHz)"
            uirevision="streaming-current-frequency"
          />
        </SectionCard>
        <SectionCard
          title="流式电流输出"
          description="无效点仍写入CSV并保留invalid_reason，图中不把无效值画成0。"
        >
          <PlotCard
            x={elapsed}
            y={points.map((point) =>
              point.valid &&
              Number.isFinite(Number(point.estimated_current_a))
                ? Number(point.estimated_current_a)
                : null
            )}
            xTitle="运行时间 (s)"
            yTitle="电流 (A)"
            lineColor="#f2c94c"
            uirevision="streaming-current-output"
          />
        </SectionCard>
      </SimpleGrid>

      <Text size="sm" c="dimmed">
        当前物理峰心标定：
        {calibration
          ? `${calibration.point_count}点，Δf ${formatMHz(
              calibration.delta_f_min_hz
            )} ～ ${formatMHz(calibration.delta_f_max_hz)}`
          : "未标定"}
      </Text>
    </Stack>
  );
}
