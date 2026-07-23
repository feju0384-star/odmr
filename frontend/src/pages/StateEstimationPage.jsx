import { useEffect, useMemo, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Divider,
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
import {
  IconAlertTriangle,
  IconPlayerPlay,
  IconPlayerStop,
} from "@tabler/icons-react";

import { MetricCard } from "../components/MetricCard";
import { PlotCard } from "../components/PlotCard";
import { SectionCard } from "../components/SectionCard";
import { useDashboard } from "../hooks/useDashboard";
import { api, wsUrl } from "../lib/api";

const CALIBRATION_STORAGE_KEY = "nv-current-physical-calibration-v3";
const FORM_STORAGE_KEY = "nv-state-estimation-form-v1";
const MAX_PLOT_POINTS = 600;

const DEFAULT_FORM = {
  estimator_type: "ekf",
  channel_index: 0,
  start_hz: 2.83e9,
  stop_hz: 2.91e9,
  search_points: 121,
  search_settle_ms: 10,
  tracking_settle_ms: 3,
  sample_averages: 1,
  probe_offset_hz: 250000,
  calibration_points_each_side: 2,
  minimum_complex_fit_r2: 0.6,
  minimum_peak_prominence_fraction: 0.05,
  peak_pair_ambiguity_score_ratio: 0.9,
  delta_f_min_hz: 0,
  delta_f_max_hz: 1e9,
  minimum_resolvable_separation_factor: 0.75,
  identity_guard_fraction: 0.2,
  measurement_noise_v: 0,
  initial_frequency_sigma_hz: 250000,
  initial_velocity_sigma_hz_per_s: 2e6,
  acceleration_noise_hz_per_s2: 5e6,
  baseline_process_noise_v_per_sqrt_s: 2e-5,
  slope_relative_process_noise_per_sqrt_s: 0.02,
  calibration_residual_sigma_a: 0,
  innovation_gate_sigma: 4,
  bad_updates_to_reacquire: 4,
  maximum_frequency_sigma_hz: 1.5e6,
  maximum_delta_f_sigma_hz: 2e6,
  maximum_prediction_age_s: 1,
  max_reacquire_attempts: 5,
  max_tracking_duration_s: 0,
};

const STATE_LABELS = [
  ["f_left_hz", "fL", "Hz"],
  ["f_right_hz", "fR", "Hz"],
  ["f_left_velocity_hz_per_s", "dfL/dt", "Hz/s"],
  ["f_right_velocity_hz_per_s", "dfR/dt", "Hz/s"],
  ["b_left_re_v", "Re(bL)", "V"],
  ["b_left_im_v", "Im(bL)", "V"],
  ["g_left_re_v_per_hz", "Re(gL)", "V/Hz"],
  ["g_left_im_v_per_hz", "Im(gL)", "V/Hz"],
  ["b_right_re_v", "Re(bR)", "V"],
  ["b_right_im_v", "Im(bR)", "V"],
  ["g_right_re_v_per_hz", "Re(gR)", "V/Hz"],
  ["g_right_im_v_per_hz", "Im(gR)", "V/Hz"],
  ["current_a", "I", "A"],
];

function finite(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function loadForm() {
  if (typeof window === "undefined") {
    return DEFAULT_FORM;
  }
  try {
    const saved = JSON.parse(
      window.localStorage.getItem(FORM_STORAGE_KEY) || "{}"
    );
    return { ...DEFAULT_FORM, ...saved };
  } catch {
    return DEFAULT_FORM;
  }
}

function loadCalibrationPoints() {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const saved = JSON.parse(
      window.localStorage.getItem(CALIBRATION_STORAGE_KEY) || "{}"
    );
    const points = Array.isArray(saved) ? saved : saved?.points;
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
  const slope = (count * sumXY - sumX * sumY) / denominator;
  const intercept = (sumY - slope * sumX) / count;
  const residuals = pairs.map(
    (point) => point.y - (slope * point.x + intercept)
  );
  return {
    slope_a_per_hz: slope,
    intercept_a: intercept,
    residual_sigma_a: Math.sqrt(
      residuals.reduce((sum, residual) => sum + residual ** 2, 0) /
        Math.max(1, count - 2)
    ),
    delta_f_min_hz: Math.min(...pairs.map((point) => point.x)),
    delta_f_max_hz: Math.max(...pairs.map((point) => point.x)),
    point_count: count,
  };
}

function appendBounded(previous, point) {
  const next = [...previous, point];
  return next.length > MAX_PLOT_POINTS
    ? next.slice(next.length - MAX_PLOT_POINTS)
    : next;
}

function formatFrequency(value) {
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

function formatCurrent(value) {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) {
    return "--";
  }
  return Math.abs(numeric) >= 1
    ? `${numeric.toFixed(6)} A`
    : `${(numeric * 1e3).toFixed(3)} mA`;
}

function formatScientific(value, digits = 4) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toExponential(digits) : "--";
}

function phaseLabel(phase) {
  const labels = {
    IDLE: "空闲",
    FULL_SCAN: "初始完整扫频",
    FULL_REACQUIRE: "全频段重新扫峰",
    CALIBRATE: "复数 b/g 标定",
    TRACK: "联合状态跟踪",
  };
  return labels[phase] || phase || "空闲";
}

export default function StateEstimationPage() {
  const { data, error, loading, refresh } = useDashboard(1500);
  const [form, setForm] = useState(loadForm);
  const [running, setRunning] = useState(false);
  const [phase, setPhase] = useState("IDLE");
  const [status, setStatus] = useState("等待启动");
  const [latestPoint, setLatestPoint] = useState(null);
  const [history, setHistory] = useState([]);
  const [scanTrace, setScanTrace] = useState([]);
  const [reacquireMessage, setReacquireMessage] = useState("");
  const socketRef = useRef(null);
  const terminalRef = useRef(true);
  const hydratedFromBackendRef = useRef(false);
  const calibrationPoints = useMemo(loadCalibrationPoints, [data]);
  const calibration = useMemo(
    () => fitCalibration(calibrationPoints),
    [calibrationPoints]
  );

  useEffect(() => {
    window.localStorage.setItem(FORM_STORAGE_KEY, JSON.stringify(form));
  }, [form]);

  useEffect(
    () => () => {
      socketRef.current?.close();
    },
    []
  );

  useEffect(() => {
    if (
      !data?.measurement ||
      data.measurement.last_state_estimation_request == null ||
      hydratedFromBackendRef.current
    ) {
      return;
    }
    const previous = data.measurement.last_state_estimation_request;
    setForm((current) => ({ ...current, ...previous }));
    hydratedFromBackendRef.current = true;
  }, [data?.measurement?.last_state_estimation_request]);

  const updateNumber = (field, minimum = undefined) => (value) => {
    const numeric = finite(value, form[field]);
    setForm((current) => ({
      ...current,
      [field]:
        minimum === undefined ? numeric : Math.max(minimum, numeric),
    }));
  };

  const stop = async () => {
    try {
      await api.stopStateEstimationCurrent();
      setStatus("正在停止状态估计...");
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

  const start = () => {
    if (!data?.lockin?.connected || !data?.microwave?.connected) {
      notifications.show({
        color: "red",
        title: "设备未连接",
        message: "请先连接锁相放大器和微波源。",
      });
      return;
    }
    if (!calibration) {
      notifications.show({
        color: "yellow",
        title: "缺少物理峰心标定",
        message: "请先在“电流测量”页建立至少两个 Δf↔I 标定点。",
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
    setPhase("FULL_SCAN");
    setStatus("正在建立 WebSocket...");
    setLatestPoint(null);
    setHistory([]);
    setScanTrace([]);
    setReacquireMessage("");

    const socket = new WebSocket(wsUrl("/state-estimation-current/ws"));
    socketRef.current = socket;
    socket.onopen = () => {
      socket.send(
        JSON.stringify({
          ...form,
          calibration_slope_a_per_hz: calibration.slope_a_per_hz,
          calibration_intercept_a: calibration.intercept_a,
          calibration_delta_f_min_hz: calibration.delta_f_min_hz,
          calibration_delta_f_max_hz: calibration.delta_f_max_hz,
          calibration_residual_sigma_a:
            form.calibration_residual_sigma_a > 0
              ? form.calibration_residual_sigma_a
              : calibration.residual_sigma_a,
        })
      );
    };
    socket.onmessage = (message) => {
      const payload = JSON.parse(message.data);
      if (payload.type === "state_estimation_started") {
        setStatus(`${String(payload.estimator_type).toUpperCase()} 已启动`);
      } else if (payload.type === "state_estimation_state") {
        setPhase(payload.phase || "FULL_SCAN");
        setStatus(
          payload.phase === "CALIBRATE"
            ? "正在标定左右峰复数 b/g 模型"
            : phaseLabel(payload.phase)
        );
        if (payload.phase === "FULL_REACQUIRE") {
          setScanTrace([]);
        }
      } else if (payload.type === "state_estimation_scan_point") {
        setScanTrace((previous) =>
          appendBounded(previous, {
            frequency_hz: payload.frequency_hz,
            r_v: payload.r_v,
          })
        );
        setStatus(
          `${phaseLabel(payload.phase)} ${payload.index}/${payload.points}`
        );
      } else if (payload.type === "state_estimation_initialized") {
        setPhase("TRACK");
        setStatus(
          `${String(payload.estimator_type).toUpperCase()} 模型已初始化`
        );
      } else if (payload.type === "state_estimation_point") {
        const point = payload.point;
        setLatestPoint(point);
        setHistory((previous) => appendBounded(previous, point));
        setPhase("TRACK");
        setStatus(
          point.prediction_only
            ? "短时信号异常：当前为模型预测"
            : "测量更新已接受"
        );
      } else if (payload.type === "state_estimation_reacquire") {
        setReacquireMessage(
          `第 ${payload.reacquire_count} 次重扫：${payload.reason}`
        );
        setStatus("后验不确定度/创新判据触发重新扫峰");
        setPhase("FULL_REACQUIRE");
        setScanTrace([]);
      } else if (
        payload.type === "state_estimation_complete" ||
        payload.type === "state_estimation_cancelled"
      ) {
        terminalRef.current = true;
        setRunning(false);
        setPhase("IDLE");
        setStatus(
          payload.type === "state_estimation_cancelled"
            ? "状态估计已停止"
            : "设定时长已完成"
        );
        refresh();
      } else if (payload.type === "state_estimation_error") {
        terminalRef.current = true;
        setRunning(false);
        setPhase("IDLE");
        setStatus(payload.message || "状态估计异常");
        notifications.show({
          color: "red",
          title: "状态估计失败",
          message: payload.message || "未知错误",
        });
        refresh();
      }
    };
    socket.onerror = () => {
      setStatus("WebSocket 连接异常");
    };
    socket.onclose = () => {
      if (!terminalRef.current) {
        setRunning(false);
        setPhase("IDLE");
        setStatus("连接已关闭；后端任务已请求停止");
      }
      if (socketRef.current === socket) {
        socketRef.current = null;
      }
    };
  };

  if (!data) {
    return (
      <Stack gap="md">
        <Text className="page-title">EKF / UKF 状态估计电流</Text>
        <Text c="dimmed">
          {error || (loading ? "正在加载状态估计页面..." : "测量数据为空")}
        </Text>
      </Stack>
    );
  }

  const measurement = data.measurement || {};
  const otherTaskRunning =
    Boolean(measurement.running) &&
    measurement.mode !== "state_estimation_current";
  const confidencePercent =
    100 * finite(latestPoint?.confidence_0_to_1, 0);
  const timeValues = history.map((point) => finite(point.elapsed_s));

  return (
    <Stack gap="lg">
      <div>
        <Text className="eyebrow">Independent Estimator</Text>
        <Text className="page-title">EKF / UKF 状态估计电流</Text>
        <Text c="dimmed" maw={1050}>
          这是与 PID 电流测量隔离的独立功能。滤波器联合估计
          fL、fR、两峰速度、左右复数 b/g 与标定电流 I；短时失去有效
          X/Y 时继续预测，并用后验协方差和创新门限决定是否重新扫峰。
        </Text>
      </div>

      {!calibration ? (
        <Alert
          color="yellow"
          icon={<IconAlertTriangle size={18} />}
          title="还不能输出电流"
        >
          请先到“电流测量”页完成物理峰心标定。该页面只读取统一的
          I=a(fR-fL)+b 标定，不读取旧过零点数据。
        </Alert>
      ) : null}

      {reacquireMessage ? (
        <Alert color="orange" title="最近一次自动重扫">
          {reacquireMessage}
        </Alert>
      ) : null}

      <SimpleGrid cols={{ base: 1, sm: 2, xl: 4 }}>
        <MetricCard
          label="运行阶段"
          value={phaseLabel(phase)}
          hint={status}
        />
        <MetricCard
          label="估计电流"
          value={formatCurrent(latestPoint?.current_a)}
          hint={
            latestPoint?.current_ci95_a
              ? `95% CI ${formatCurrent(
                  latestPoint.current_ci95_a[0]
                )} ～ ${formatCurrent(latestPoint.current_ci95_a[1])}`
              : "等待可信区间"
          }
        />
        <MetricCard
          label="物理劈裂 Δf"
          value={formatMHz(latestPoint?.splitting_hz)}
          hint={
            latestPoint?.splitting_ci95_hz
              ? `95% CI ${formatMHz(
                  latestPoint.splitting_ci95_hz[0]
                )} ～ ${formatMHz(latestPoint.splitting_ci95_hz[1])}`
              : "等待可信区间"
          }
        />
        <MetricCard
          label="更新速率"
          value={`${finite(latestPoint?.update_rate_hz).toFixed(2)} Hz`}
          hint={`单次 ${finite(latestPoint?.timing?.total_ms).toFixed(1)} ms`}
        />
        <MetricCard
          label="左峰 fL"
          value={formatFrequency(latestPoint?.f_left_hz)}
          hint={`速度 ${formatScientific(
            latestPoint?.f_left_velocity_hz_per_s
          )} Hz/s`}
        />
        <MetricCard
          label="右峰 fR"
          value={formatFrequency(latestPoint?.f_right_hz)}
          hint={`速度 ${formatScientific(
            latestPoint?.f_right_velocity_hz_per_s
          )} Hz/s`}
        />
        <MetricCard
          label="数据来源"
          value={
            latestPoint?.prediction_only
              ? "仅预测"
              : latestPoint
                ? "实测更新"
                : "--"
          }
          hint={`最长预测龄 ${finite(
            latestPoint?.maximum_prediction_age_s
          ).toFixed(3)} s`}
        />
        <MetricCard
          label="自动重扫"
          value={String(latestPoint?.reacquire_count ?? 0)}
          hint={`拒绝 ${latestPoint?.rejected_update_count ?? 0} 次更新`}
        />
      </SimpleGrid>

      <SectionCard
        title="置信度与更新判据"
        description="置信度综合后验 Δf 不确定度、双峰数据新鲜度和创新大小；预测态会明确标记，不冒充新测量。"
        badge={latestPoint?.output_valid ? "Output Valid" : "No Valid Output"}
      >
        <Stack gap="xs">
          <Group justify="space-between">
            <Text>当前置信度</Text>
            <Text fw={700}>{confidencePercent.toFixed(1)}%</Text>
          </Group>
          <Progress
            value={confidencePercent}
            color={
              confidencePercent >= 80
                ? "teal"
                : confidencePercent >= 50
                  ? "yellow"
                  : "red"
            }
            size="lg"
          />
          <Group gap="lg" mt="xs">
            <Badge
              color={latestPoint?.measurement_update_accepted ? "teal" : "orange"}
              variant="light"
            >
              {latestPoint?.measurement_update_accepted
                ? "创新已接受"
                : "创新未接受"}
            </Badge>
            <Text size="sm" c="dimmed">
              NIS{" "}
              {formatScientific(
                latestPoint?.innovation?.normalized_innovation_squared,
                3
              )}{" "}
              / 门限 {formatScientific(latestPoint?.innovation?.gate_threshold, 3)}
            </Text>
            <Text size="sm" c="dimmed">
              输出原因：{latestPoint?.invalid_reason || "valid"}
            </Text>
          </Group>
        </Stack>
      </SectionCard>

      <SectionCard
        title="扫描与探测参数"
        description="初始完整扫频仍使用 FM R 双瓣谷识别物理峰心；跟踪阶段交替访问左右峰并在预测峰心两侧探测 X/Y。"
        badge="Isolated"
      >
        <Grid>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <Select
              label="估计器"
              value={form.estimator_type}
              data={[
                { value: "ekf", label: "EKF（更快，推荐先用）" },
                { value: "ukf", label: "UKF（非线性更强）" },
              ]}
              onChange={(value) =>
                setForm((current) => ({
                  ...current,
                  estimator_type: value || "ekf",
                }))
              }
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="锁相通道"
              value={form.channel_index}
              min={0}
              step={1}
              onChange={updateNumber("channel_index", 0)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="扫频起点 (Hz)"
              value={form.start_hz}
              step={1e6}
              onChange={updateNumber("start_hz")}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="扫频终点 (Hz)"
              value={form.stop_hz}
              step={1e6}
              onChange={updateNumber("stop_hz")}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="完整扫频点数"
              value={form.search_points}
              min={11}
              step={2}
              onChange={updateNumber("search_points", 11)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="完整扫频等待 (ms)"
              value={form.search_settle_ms}
              min={0.1}
              step={1}
              onChange={updateNumber("search_settle_ms", 0.1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="跟踪等待 (ms)"
              value={form.tracking_settle_ms}
              min={0.1}
              step={0.5}
              onChange={updateNumber("tracking_settle_ms", 0.1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="峰心两侧探测偏移 (Hz)"
              value={form.probe_offset_hz}
              min={1}
              step={50000}
              onChange={updateNumber("probe_offset_hz", 1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="单点平均次数"
              value={form.sample_averages}
              min={1}
              step={1}
              onChange={updateNumber("sample_averages", 1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 3 }}>
            <NumberInput
              label="最大运行时长 (s，0=不限)"
              value={form.max_tracking_duration_s}
              min={0}
              step={60}
              onChange={updateNumber("max_tracking_duration_s", 0)}
              disabled={running}
            />
          </Grid.Col>
        </Grid>
      </SectionCard>

      <SectionCard
        title="过程噪声、测量噪声与重扫门限"
        description="这些参数定义状态能多快变化、一次 X/Y 测量有多可信，以及允许模型独立预测多久。"
        badge="Covariance"
      >
        <Grid>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="初始峰频 1σ (Hz)"
              value={form.initial_frequency_sigma_hz}
              min={1}
              onChange={updateNumber("initial_frequency_sigma_hz", 1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="初始速度 1σ (Hz/s)"
              value={form.initial_velocity_sigma_hz_per_s}
              min={1}
              onChange={updateNumber("initial_velocity_sigma_hz_per_s", 1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="加速度过程噪声 (Hz/s²)"
              value={form.acceleration_noise_hz_per_s2}
              min={1}
              onChange={updateNumber("acceleration_noise_hz_per_s2", 1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="b 随机游走 (V/√s)"
              value={form.baseline_process_noise_v_per_sqrt_s}
              min={0}
              decimalScale={9}
              onChange={updateNumber(
                "baseline_process_noise_v_per_sqrt_s",
                0
              )}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="g 相对随机游走 (1/√s)"
              value={form.slope_relative_process_noise_per_sqrt_s}
              min={0}
              decimalScale={6}
              onChange={updateNumber(
                "slope_relative_process_noise_per_sqrt_s",
                0
              )}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="X/Y 单轴噪声 (V，0=自动)"
              value={form.measurement_noise_v}
              min={0}
              decimalScale={12}
              onChange={updateNumber("measurement_noise_v", 0)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="创新门限 (σ)"
              value={form.innovation_gate_sigma}
              min={1}
              step={0.5}
              onChange={updateNumber("innovation_gate_sigma", 1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="连续拒绝后重扫"
              value={form.bad_updates_to_reacquire}
              min={1}
              step={1}
              onChange={updateNumber("bad_updates_to_reacquire", 1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="最大峰频 1σ (Hz)"
              value={form.maximum_frequency_sigma_hz}
              min={1}
              onChange={updateNumber("maximum_frequency_sigma_hz", 1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="最大 Δf 1σ (Hz)"
              value={form.maximum_delta_f_sigma_hz}
              min={1}
              onChange={updateNumber("maximum_delta_f_sigma_hz", 1)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="最长仅预测时间 (s)"
              value={form.maximum_prediction_age_s}
              min={0.01}
              step={0.1}
              onChange={updateNumber("maximum_prediction_age_s", 0.01)}
              disabled={running}
            />
          </Grid.Col>
          <Grid.Col span={{ base: 12, md: 4, xl: 3 }}>
            <NumberInput
              label="最大自动重扫次数"
              value={form.max_reacquire_attempts}
              min={0}
              step={1}
              onChange={updateNumber("max_reacquire_attempts", 0)}
              disabled={running}
            />
          </Grid.Col>
        </Grid>
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
          启动独立状态估计
        </Button>
        <Button
          leftSection={<IconPlayerStop size={18} />}
          color="red"
          variant="light"
          onClick={stop}
          disabled={!running}
        >
          停止
        </Button>
        <Button variant="subtle" color="gray" onClick={refresh}>
          刷新设备状态
        </Button>
        {otherTaskRunning ? (
          <Text c="yellow">当前有其他测量任务运行：{measurement.mode}</Text>
        ) : null}
      </Group>

      <SimpleGrid cols={{ base: 1, xl: 2 }}>
        <SectionCard
          title="双峰频率与 1σ 置信带"
          description="左右峰按同一滤波时刻对齐；虚线是 ±1σ。"
        >
          <PlotCard
            traces={[
              {
                name: "fL",
                x: timeValues,
                y: history.map((point) => finite(point.f_left_hz) / 1e9),
                lineColor: "#5ad1ff",
              },
              {
                name: "fR",
                x: timeValues,
                y: history.map((point) => finite(point.f_right_hz) / 1e9),
                lineColor: "#45e0a8",
              },
              {
                name: "fL + 1σ",
                x: timeValues,
                y: history.map(
                  (point) =>
                    (finite(point.f_left_hz) +
                      finite(point.f_left_sigma_hz)) /
                    1e9
                ),
                lineColor: "#5ad1ff",
                lineDash: "dot",
              },
              {
                name: "fR - 1σ",
                x: timeValues,
                y: history.map(
                  (point) =>
                    (finite(point.f_right_hz) -
                      finite(point.f_right_sigma_hz)) /
                    1e9
                ),
                lineColor: "#45e0a8",
                lineDash: "dot",
              },
            ]}
            xTitle="运行时间 (s)"
            yTitle="频率 (GHz)"
            uirevision="state-estimation-frequencies"
          />
        </SectionCard>

        <SectionCard
          title="电流估计"
          description="只有标定范围、峰身份、后验不确定度和数据新鲜度同时满足门限时 output_valid 才为真。"
        >
          <PlotCard
            traces={[
              {
                name: "I",
                x: timeValues,
                y: history.map((point) =>
                  point.current_a == null
                    ? null
                    : finite(point.current_a) * 1e3
                ),
                lineColor: "#f2c94c",
              },
              {
                name: "I + 1σ",
                x: timeValues,
                y: history.map((point) =>
                  point.current_a == null
                    ? null
                    : (finite(point.current_a) +
                        finite(point.current_sigma_a)) *
                      1e3
                ),
                lineColor: "#ff9f43",
                lineDash: "dot",
              },
              {
                name: "I - 1σ",
                x: timeValues,
                y: history.map((point) =>
                  point.current_a == null
                    ? null
                    : (finite(point.current_a) -
                        finite(point.current_sigma_a)) *
                      1e3
                ),
                lineColor: "#ff9f43",
                lineDash: "dot",
              },
            ]}
            xTitle="运行时间 (s)"
            yTitle="电流 (mA)"
            uirevision="state-estimation-current"
          />
        </SectionCard>
      </SimpleGrid>

      {phase === "FULL_SCAN" || phase === "FULL_REACQUIRE" || scanTrace.length ? (
        <SectionCard
          title="最近一次完整扫频"
          description="R 双瓣谷只用于建立/恢复物理峰身份；跟踪更新使用复数 X/Y 模型。"
          badge={phaseLabel(phase)}
        >
          <PlotCard
            x={scanTrace.map((point) => point.frequency_hz / 1e9)}
            y={scanTrace.map((point) => point.r_v)}
            xTitle="频率 (GHz)"
            yTitle="FM R (V)"
            uirevision="state-estimation-scan"
          />
        </SectionCard>
      ) : null}

      <SectionCard
        title="联合状态向量"
        description="b 与 g 是复数，因此在滤波器内部拆成实部/虚部；I 是由 Δf 标定约束的增广派生状态。"
        badge={`${String(form.estimator_type).toUpperCase()} · 13 states`}
      >
        <div style={{ overflowX: "auto" }}>
          <Table striped withTableBorder>
            <Table.Thead>
              <Table.Tr>
                <Table.Th>状态</Table.Th>
                <Table.Th>物理含义</Table.Th>
                <Table.Th>当前估计</Table.Th>
                <Table.Th>单位</Table.Th>
              </Table.Tr>
            </Table.Thead>
            <Table.Tbody>
              {STATE_LABELS.map(([key, label, unit]) => (
                <Table.Tr key={key}>
                  <Table.Td>{key}</Table.Td>
                  <Table.Td>{label}</Table.Td>
                  <Table.Td>
                    {formatScientific(latestPoint?.state?.[key], 6)}
                  </Table.Td>
                  <Table.Td>{unit}</Table.Td>
                </Table.Tr>
              ))}
            </Table.Tbody>
          </Table>
        </div>
        <Divider my="md" />
        <Text size="sm" c="dimmed">
          当前标定：{calibration ? `${calibration.point_count} 点，a=${formatScientific(
            calibration.slope_a_per_hz
          )} A/Hz，Δf 范围 ${formatMHz(
            calibration.delta_f_min_hz
          )} ～ ${formatMHz(calibration.delta_f_max_hz)}` : "未标定"}
        </Text>
      </SectionCard>
    </Stack>
  );
}
