import { useEffect, useRef, useState } from "react";
import {
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

import { api, formatGHz, wsUrl } from "../lib/api";
import { MetricCard } from "./MetricCard";
import { PlotCard } from "./PlotCard";
import { SectionCard } from "./SectionCard";

const MAX_TRACKING_POINTS = 400;

const DEFAULT_TRACKING_FORM = {
  tracking_target: "complex_projection",
  independent_dc_channel_index: -1,
  probe_offset_hz: 250_000,
  tracking_settle_ms: 3,
  sample_averages: 1,
  timing_report_interval_cycles: 10,
  record_enabled: true,
  record_interval_s: 1,
  record_label: "",
  kp: 0.45,
  ki_per_s: 0.03,
  kd_s: 0,
  derivative_filter_tau_s: 0.1,
  antiwindup_gain_per_s: 1,
  max_step_hz: 500_000,
  maximum_slew_hz_per_s: 10_000_000,
  integral_limit_hz: 1_000_000,
  lock_error_limit_hz: 1_500_000,
  minimum_complex_fit_r2: 0.7,
  orthogonal_limit_fraction: 0.5,
  maximum_error_fraction: 0.8,
  minimum_depth_fraction: 0.15,
  slope_ratio_min: 0.3,
  slope_ratio_max: 3,
  maximum_slope_angle_change_rad: 1,
  verify_interval_visits: 20,
  slope_verification_max_age_s: 10,
  bad_samples_to_suspect: 1,
  bad_samples_to_lose: 3,
  good_samples_to_lock: 3,
  relock_gain_ramp_samples: 5,
  saturation_loss_threshold: 5,
  calibration_points_each_side: 2,
  enable_velocity_prediction: true,
  velocity_filter_tau_s: 0.5,
  maximum_velocity_hz_per_s: 20_000_000,
  maximum_acceleration_hz_per_s2: 100_000_000,
  maximum_extrapolation_age_s: 1,
  maximum_delta_f_sigma_hz: 2_000_000,
  delta_f_min_hz: 0,
  delta_f_max_hz: 1_000_000_000,
  local_scan_points: 17,
  local_scan_initial_width_fraction: 1,
  local_scan_expansion_factor: 2,
  local_scan_max_expansions: 3,
  reacquire_identity_guard_fraction: 0.25,
  minimum_resolvable_separation_factor: 0.75,
  relock_cooldown_s: 0.1,
  max_relock_attempts: 5,
  max_tracking_duration_s: 0,
};

function numberOr(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function fitCalibration(points, splittingField) {
  const valid = (Array.isArray(points) ? points : [])
    .map((point) => ({
      x: Number(point?.[splittingField]),
      y: Number(point?.current_a),
    }))
    .filter((point) => Number.isFinite(point.x) && Number.isFinite(point.y));
  if (valid.length < 2) {
    return null;
  }
  const n = valid.length;
  const sumX = valid.reduce((sum, point) => sum + point.x, 0);
  const sumY = valid.reduce((sum, point) => sum + point.y, 0);
  const sumXX = valid.reduce((sum, point) => sum + point.x * point.x, 0);
  const sumXY = valid.reduce((sum, point) => sum + point.x * point.y, 0);
  const denominator = n * sumXX - sumX * sumX;
  if (Math.abs(denominator) < 1e-30) {
    return null;
  }
  const slope = (n * sumXY - sumX * sumY) / denominator;
  return {
    slope_a_per_hz: slope,
    intercept_a: (sumY - slope * sumX) / n,
    point_count: n,
  };
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

function formatMHz(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${(numeric / 1e6).toFixed(6)} MHz` : "--";
}

function formatKHz(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${(numeric / 1e3).toFixed(3)} kHz` : "--";
}

function targetLabel(target) {
  if (target === "complex_projection") {
    return "复数投影";
  }
  if (target === "zero_crossing") {
    return "过零点";
  }
  if (target === "minimum") {
    return "最低点";
  }
  return "自动";
}

function lockLabel(state) {
  const labels = {
    idle: "待机",
    connecting: "连接中",
    acquiring: "扫描捕获",
    locked: "已锁定",
    warning: "锁定告警",
    relocking: "重新扫峰",
    error: "错误",
    stopped: "已停止",
  };
  return labels[state] || state || "待机";
}

function lockColor(state) {
  if (state === "locked") {
    return "teal";
  }
  if (state === "warning" || state === "relocking") {
    return "yellow";
  }
  if (state === "error") {
    return "red";
  }
  return "gray";
}

function timingBottleneckLabel(value) {
  const labels = {
    microwave_command_ms: "微波 SCPI 写频",
    settle_ms: "稳定等待",
    lock_wait_ms: "Zurich 设备锁等待",
    lockin_read_ms: "Zurich 单点读取",
    other_ms: "计算/调度/其他",
  };
  return labels[value] || "等待诊断";
}

function formatMs(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? `${numeric.toFixed(2)} ms` : "--";
}

export function CurrentTrackingPanel({
  currentForm,
  onCurrentFormChange,
  onSyncFromOdmr,
  onUseDefaultResonance,
  calibrationPoints,
  onAddPhysicalCalibrationPoint,
  lockinConnected,
  microwaveConnected,
  measurementBusy,
}) {
  const [form, setForm] = useState(DEFAULT_TRACKING_FORM);
  const [isTracking, setIsTracking] = useState(false);
  const [lockState, setLockState] = useState("idle");
  const [statusText, setStatusText] = useState("等待启动");
  const [activeTarget, setActiveTarget] = useState("complex_projection");
  const [points, setPoints] = useState([]);
  const [latestPoint, setLatestPoint] = useState(null);
  const [relockCount, setRelockCount] = useState(0);
  const [warningReasons, setWarningReasons] = useState([]);
  const [capabilityWarning, setCapabilityWarning] = useState("");
  const [dcIndependent, setDcIndependent] = useState(false);
  const [knownCurrentA, setKnownCurrentA] = useState(null);
  const [timingDiagnostics, setTimingDiagnostics] = useState(null);
  const [recordingStatus, setRecordingStatus] = useState(null);
  const [isDownloadingRecording, setIsDownloadingRecording] = useState(false);
  const socketRef = useRef(null);

  useEffect(
    () => () => {
      socketRef.current?.close();
      socketRef.current = null;
    },
    []
  );

  useEffect(() => {
    let active = true;
    api.currentTrackingRecordingStatus()
      .then((result) => {
        if (active && result?.data?.session_id) {
          setRecordingStatus(result.data);
        }
      })
      .catch(() => {
        // 没有历史记录或后端尚未启动时保持空状态。
      });
    return () => {
      active = false;
    };
  }, []);

  const updateNumber = (field, minimum = 0) => (value) => {
    setForm((previous) => ({
      ...previous,
      [field]: Math.max(minimum, numberOr(value, previous[field])),
    }));
  };

  const physicalCalibrationPoints = (Array.isArray(calibrationPoints) ? calibrationPoints : [])
    .filter((point) => point?.source === "physical_peak_tracking");
  const minimumCalibration = fitCalibration(
    physicalCalibrationPoints,
    "resonance_splitting_hz"
  );
  const calibrationSplittings = physicalCalibrationPoints
    .map((point) => Number(point?.resonance_splitting_hz))
    .filter((value) => Number.isFinite(value));
  const calibrationDeltaMinHz = calibrationSplittings.length
    ? Math.min(...calibrationSplittings)
    : null;
  const calibrationDeltaMaxHz = calibrationSplittings.length
    ? Math.max(...calibrationSplittings)
    : null;
  const canConvertSelectedTarget = Boolean(minimumCalibration);

  const closeSocket = () => {
    const socket = socketRef.current;
    socketRef.current = null;
    if (socket && socket.readyState < WebSocket.CLOSING) {
      socket.close();
    }
  };

  const startTracking = () => {
    if (measurementBusy || isTracking) {
      notifications.show({
        color: "yellow",
        title: "已有任务运行",
        message: "请先停止当前测量任务。",
      });
      return;
    }
    if (!lockinConnected || !microwaveConnected) {
      notifications.show({
        color: "red",
        title: "设备未连接",
        message: "PID 双峰跟踪需要同时连接锁相和微波源。",
      });
      return;
    }
    if (!canConvertSelectedTarget) {
      notifications.show({
        color: "yellow",
        title: "将只输出峰位和 Δf",
        message: "当前缺少至少 2 个物理峰心电流标定点；跟踪可以启动，但电流会保持 invalid。",
      });
    }

    closeSocket();
    setPoints([]);
    setLatestPoint(null);
    setRelockCount(0);
    setWarningReasons([]);
    setCapabilityWarning("");
    setTimingDiagnostics(null);
    setRecordingStatus(null);
    setLockState("connecting");
    setStatusText("正在连接跟踪通道");
    const socket = new WebSocket(wsUrl("/measurement/current/tracking/ws"));
    socketRef.current = socket;

    socket.onopen = () => {
      setIsTracking(true);
      socket.send(
        JSON.stringify({
          ...form,
          channel_index: currentForm.channel_index,
          start_hz: currentForm.start_hz,
          stop_hz: currentForm.stop_hz,
          search_points: currentForm.search_points,
          search_settle_ms: currentForm.settle_ms,
          minimum_calibration_slope_a_per_hz:
            minimumCalibration?.slope_a_per_hz ?? null,
          minimum_calibration_intercept_a:
            minimumCalibration?.intercept_a ?? null,
          calibration_delta_f_min_hz: calibrationDeltaMinHz,
          calibration_delta_f_max_hz: calibrationDeltaMaxHz,
        })
      );
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        if (payload.type === "current_tracking_started") {
          setLockState("acquiring");
          setRecordingStatus(payload.recording || null);
          setStatusText("正在初始扫描并捕获左右共振峰");
        } else if (payload.type === "current_tracking_capability") {
          const warning = payload.warning || "";
          setDcIndependent(Boolean(payload.dc_independent));
          setCapabilityWarning(warning);
          if (warning) {
            notifications.show({
              color: "yellow",
              title: "峰存在性能力告警",
              message: warning,
            });
          }
        } else if (payload.type === "current_tracking_state") {
          const state = payload.global_state || "";
          setLockState(state === "FULL_REACQUIRE" ? "relocking" : "acquiring");
          setStatusText(
            state === "FULL_REACQUIRE"
              ? "输出已标记无效，正在全频段重捕获"
              : state === "CALIBRATE"
                ? "正在标定左右峰复数 b/g 模型"
                : "正在执行完整 FM R 双瓣谷扫频"
          );
        } else if (payload.type === "current_tracking_models_calibrated") {
          setActiveTarget("complex_projection");
          setDcIndependent(Boolean(payload.dc_independent));
          setLockState("acquiring");
          setStatusText("复数模型已标定，等待连续有效样本确认锁定");
        } else if (payload.type === "current_tracking_local_reacquire") {
          if (payload.stage === "start") {
            setLockState("relocking");
            setStatusText(`${payload.peak_id === "left" ? "左峰" : "右峰"}正在局部重捕获`);
          } else if (payload.stage === "success") {
            setLockState("acquiring");
            setStatusText("局部重捕获成功，正在无扰渐升 PID 增益");
          } else if (payload.stage === "failed") {
            setLockState("relocking");
            setStatusText("局部重捕获失败，即将进行全频段重扫");
          }
        } else if (payload.type === "current_tracking_slope_verification") {
          if (!payload.valid) {
            setLockState("warning");
            setStatusText(`${payload.peak_id === "left" ? "左峰" : "右峰"}实时复数斜率验证失败`);
          }
        } else if (payload.type === "current_tracking_timing") {
          setTimingDiagnostics(payload.timing || null);
        } else if (payload.type === "current_tracking_recording") {
          setRecordingStatus(payload.recording || null);
        } else if (payload.type === "current_tracking_acquiring") {
          setLockState(payload.reason === "initial" ? "acquiring" : "relocking");
          setStatusText(payload.reason === "initial" ? "正在扫描双峰" : "失锁后正在重新扫峰");
        } else if (payload.type === "current_tracking_target_fallback") {
          setStatusText(payload.message || "已切换跟踪目标");
          notifications.show({
            color: "yellow",
            title: "自动降级",
            message: payload.message || "过零点质量不足，改用最低点跟踪。",
          });
        } else if (payload.type === "current_tracking_locked") {
          setLockState("locked");
          setActiveTarget(payload.tracking_target || "auto");
          setRelockCount(numberOr(payload.relock_count, 0));
          setWarningReasons([]);
          setStatusText(payload.reason === "reacquired" ? "重扫成功，双峰已重新锁定" : "双峰已锁定");
          setLatestPoint((previous) => ({
            ...(previous || {}),
            left_frequency_hz: payload.left_frequency_hz,
            right_frequency_hz: payload.right_frequency_hz,
            splitting_hz: payload.splitting_hz,
            tracking_target: payload.tracking_target,
          }));
          if (payload.reason === "reacquired") {
            notifications.show({
              color: "teal",
              title: "重新锁定",
              message: `第 ${numberOr(payload.relock_count, 0)} 次重扫成功。`,
            });
          }
        } else if (payload.type === "current_tracking_point") {
          const point = payload.point || {};
          setLockState(point.valid ? "locked" : point.global_state === "FULL_REACQUIRE" ? "relocking" : "warning");
          setActiveTarget(point.tracking_target || "complex_projection");
          setLatestPoint(point);
          setRelockCount(numberOr(point.relock_count, 0));
          setWarningReasons(point.valid || !point.invalid_reason ? [] : [point.invalid_reason]);
          setStatusText(point.valid ? "双峰可靠锁定，输出有效" : `输出无效：${point.invalid_reason || "等待锁定"}`);
          setPoints((previous) => [...previous, point].slice(-MAX_TRACKING_POINTS));
        } else if (payload.type === "current_tracking_lock_warning") {
          setLockState("warning");
          setWarningReasons(Array.isArray(payload.reasons) ? payload.reasons : []);
          setStatusText(
            `锁定质量告警 ${numberOr(payload.consecutive_invalid_cycles, 0)}/${numberOr(payload.lost_lock_cycles, 0)}`
          );
        } else if (payload.type === "current_tracking_lock_lost") {
          setLockState("relocking");
          setRelockCount(numberOr(payload.relock_count, 0));
          setWarningReasons(Array.isArray(payload.reasons) ? payload.reasons : []);
          setStatusText("双峰失锁，正在自动重新扫峰");
          notifications.show({
            color: "orange",
            title: "检测到失锁",
            message: (payload.reasons || []).join("；") || "正在自动重扫。",
          });
        } else if (
          payload.type === "current_tracking_complete" ||
          payload.type === "current_tracking_cancelled"
        ) {
          setIsTracking(false);
          setLockState("stopped");
          setRecordingStatus(payload.result?.recording || recordingStatus);
          setStatusText(
            payload.type === "current_tracking_cancelled" ? "跟踪已停止" : "跟踪时长结束"
          );
          closeSocket();
        } else if (payload.type === "current_tracking_error") {
          setIsTracking(false);
          setLockState("error");
          setStatusText(payload.message || "PID 双峰跟踪失败");
          notifications.show({
            color: "red",
            title: "PID 跟踪失败",
            message: payload.message || "后端返回未知错误。",
          });
          closeSocket();
        }
      } catch {
        // 忽略格式不完整的帧，下一帧仍可继续跟踪。
      }
    };

    socket.onerror = () => {
      setLockState("error");
      setStatusText("跟踪 WebSocket 连接异常");
    };
    socket.onclose = () => {
      if (socketRef.current === socket) {
        socketRef.current = null;
        setIsTracking(false);
        setLockState("error");
        setStatusText("跟踪连接意外断开");
        api.currentTrackingRecordingStatus()
          .then((result) => setRecordingStatus(result?.data || null))
          .catch(() => {});
      }
    };
  };

  const stopTracking = async () => {
    try {
      setStatusText("正在请求停止");
      await api.stopCurrent();
    } catch (error) {
      notifications.show({
        color: "red",
        title: "停止失败",
        message: error instanceof Error ? error.message : "未知错误",
      });
    }
  };

  const updateCurrentNumber = (field, minimum = null, integer = false) => (value) => {
    const previousValue = currentForm?.[field];
    let numeric = numberOr(value, previousValue);
    if (integer) {
      numeric = Math.round(numeric);
    }
    if (minimum !== null) {
      numeric = Math.max(minimum, numeric);
    }
    onCurrentFormChange?.({
      ...currentForm,
      [field]: numeric,
    });
  };

  const downloadRecording = async () => {
    try {
      setIsDownloadingRecording(true);
      const result = await api.downloadCurrentTrackingRecording(
        recordingStatus?.session_id || ""
      );
      const url = URL.createObjectURL(result.blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = result.filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(url);
      const statusResult = await api.currentTrackingRecordingStatus(
        recordingStatus?.session_id || ""
      );
      setRecordingStatus(statusResult?.data || recordingStatus);
      notifications.show({
        color: "teal",
        title: "Excel 已生成",
        message: "下载内容是截至当前时刻的 1 秒聚合数据快照。",
      });
    } catch (error) {
      notifications.show({
        color: "red",
        title: "导出失败",
        message: error instanceof Error ? error.message : "未知错误",
      });
    } finally {
      setIsDownloadingRecording(false);
    }
  };

  const addPhysicalCalibrationPoint = () => {
    const currentA = Number(knownCurrentA);
    const splittingHz = Number(latestPoint?.splitting_hz);
    const peaksLocked =
      latestPoint?.left_state === "LOCKED" && latestPoint?.right_state === "LOCKED";
    if (!Number.isFinite(currentA)) {
      notifications.show({ color: "red", title: "缺少已知电流", message: "请先输入标定电流值。" });
      return;
    }
    if (!peaksLocked || !Number.isFinite(splittingHz) || splittingHz <= 0) {
      notifications.show({
        color: "yellow",
        title: "物理峰心尚未可靠锁定",
        message: "只有左右峰均为 LOCKED 时才能写入物理峰心标定。",
      });
      return;
    }
    onAddPhysicalCalibrationPoint?.({
      current_a: currentA,
      resonance_splitting_hz: splittingHz,
      left_resonance_hz: Number(latestPoint.left_frequency_hz),
      right_resonance_hz: Number(latestPoint.right_frequency_hz),
      delta_f_sigma_hz: Number(latestPoint.delta_f_sigma_hz),
    });
    setKnownCurrentA(null);
    notifications.show({
      color: "teal",
      title: "已加入物理峰心标定",
      message: `${formatCurrent(currentA)} ↔ ${formatMHz(splittingHz)}。达到 2 点后请重新启动跟踪以加载新标定。`,
    });
  };

  const elapsed = points.map((point) => numberOr(point.elapsed_s));
  const leftGHz = points.map((point) => numberOr(point.left_frequency_hz) / 1e9);
  const rightGHz = points.map((point) => numberOr(point.right_frequency_hz) / 1e9);
  const currentMa = points.map((point) => numberOr(point.estimated_current_a, NaN) * 1e3);
  const leftPid = latestPoint?.left_pid || {};
  const rightPid = latestPoint?.right_pid || {};
  const saturated = Boolean(leftPid.saturated || rightPid.saturated);
  const latestUpdateRateHz =
    points.length >= 2
      ? 1 /
        Math.max(
          1e-9,
          numberOr(points[points.length - 1]?.elapsed_s) -
            numberOr(points[points.length - 2]?.elapsed_s)
        )
      : NaN;

  return (
    <SectionCard
      title="PID 闭环双峰连续跟踪"
      description="按 FM 1f 解调后的 R≈|dS/df|，用每个共振的左瓣—谷—右瓣定义物理峰心；再在峰心附近拟合独立复数 b/g 模型，将 X+jY 投影成有符号 Hz 误差，按 L→R 交替驱动两个受限 PID。"
      badge={lockLabel(lockState)}
    >
      <Group mb="md">
        <Badge color={lockColor(lockState)} variant="light">
          {statusText}
        </Badge>
        {warningReasons.length ? (
          <Text c="orange" size="sm">{warningReasons.join("；")}</Text>
        ) : null}
      </Group>
      {capabilityWarning ? (
        <Text c="yellow" size="sm" mb="md">{capabilityWarning}</Text>
      ) : null}

      <Text fw={600} mb="xs">初始双峰捕获范围</Text>
      <SimpleGrid cols={{ base: 1, md: 2, xl: 4 }}>
        <NumberInput
          label="锁相通道"
          value={currentForm.channel_index}
          disabled={isTracking}
          min={0}
          onChange={updateCurrentNumber("channel_index", 0, true)}
        />
        <NumberInput
          label="起始频率 (Hz)"
          value={currentForm.start_hz}
          disabled={isTracking}
          onChange={updateCurrentNumber("start_hz")}
        />
        <NumberInput
          label="终止频率 (Hz)"
          value={currentForm.stop_hz}
          disabled={isTracking}
          onChange={updateCurrentNumber("stop_hz")}
        />
        <NumberInput
          label="搜索点数"
          value={currentForm.search_points}
          disabled={isTracking}
          min={11}
          onChange={updateCurrentNumber("search_points", 11, true)}
        />
        <NumberInput
          label="初始扫频稳定等待 (ms)"
          value={currentForm.settle_ms}
          disabled={isTracking}
          min={0.1}
          onChange={updateCurrentNumber("settle_ms", 0.1)}
        />
        <NumberInput
          label="独立 DC/峰存在性通道 (-1=未配置)"
          value={form.independent_dc_channel_index}
          disabled={isTracking}
          min={-1}
          onChange={(value) =>
            setForm((previous) => ({
              ...previous,
              independent_dc_channel_index: Math.max(
                -1,
                Math.round(numberOr(value, previous.independent_dc_channel_index))
              ),
            }))
          }
        />
        <NumberInput
          label="复数模型/斜率探测偏移 (Hz)"
          value={form.probe_offset_hz}
          disabled={isTracking}
          onChange={updateNumber("probe_offset_hz", 1)}
        />
        <NumberInput
          label="每点稳定等待 (ms)"
          value={form.tracking_settle_ms}
          disabled={isTracking}
          onChange={updateNumber("tracking_settle_ms", 0.1)}
        />
        <NumberInput
          label="每点平均次数"
          value={form.sample_averages}
          disabled={isTracking}
          min={1}
          onChange={(value) =>
            setForm((previous) => ({
              ...previous,
              sample_averages: Math.max(1, Math.round(numberOr(value, previous.sample_averages))),
            }))
          }
        />
        <NumberInput
          label="耗时分析报告间隔 (周期)"
          value={form.timing_report_interval_cycles}
          disabled={isTracking}
          min={1}
          onChange={(value) =>
            setForm((previous) => ({
              ...previous,
              timing_report_interval_cycles: Math.max(
                1,
                Math.round(numberOr(value, previous.timing_report_interval_cycles))
              ),
            }))
          }
        />
        <NumberInput label="Kp" value={form.kp} disabled={isTracking} onChange={updateNumber("kp")} />
        <NumberInput
          label="Ki (1/s)"
          value={form.ki_per_s}
          disabled={isTracking}
          onChange={updateNumber("ki_per_s")}
        />
        <NumberInput
          label="Kd (s)"
          value={form.kd_s}
          disabled={isTracking}
          onChange={updateNumber("kd_s")}
        />
        <NumberInput
          label="微分滤波时间常数 (s)"
          value={form.derivative_filter_tau_s}
          disabled={isTracking}
          min={0}
          onChange={updateNumber("derivative_filter_tau_s")}
        />
        <NumberInput
          label="抗饱和反算增益 (1/s)"
          value={form.antiwindup_gain_per_s}
          disabled={isTracking}
          min={0}
          onChange={updateNumber("antiwindup_gain_per_s")}
        />
        <NumberInput
          label="单周期最大校正 (Hz)"
          value={form.max_step_hz}
          disabled={isTracking}
          onChange={updateNumber("max_step_hz", 1)}
        />
        <NumberInput
          label="最大变化率 (Hz/s)"
          value={form.maximum_slew_hz_per_s}
          disabled={isTracking}
          onChange={updateNumber("maximum_slew_hz_per_s", 1)}
        />
        <NumberInput
          label="积分项限幅 (Hz)"
          value={form.integral_limit_hz}
          disabled={isTracking}
          onChange={updateNumber("integral_limit_hz")}
        />
        <NumberInput
          label="失锁偏差阈值 (Hz)"
          value={form.lock_error_limit_hz}
          disabled={isTracking}
          onChange={updateNumber("lock_error_limit_hz", 1)}
        />
        <NumberInput
          label="复数模型最小 R²"
          value={form.minimum_complex_fit_r2}
          disabled={isTracking}
          min={0}
          max={1}
          onChange={updateNumber("minimum_complex_fit_r2")}
        />
        <NumberInput
          label="正交残差限值比例"
          value={form.orthogonal_limit_fraction}
          disabled={isTracking}
          min={0.001}
          onChange={updateNumber("orthogonal_limit_fraction", 0.001)}
        />
        <NumberInput
          label="实时斜率验证间隔 (每峰访问数)"
          value={form.verify_interval_visits}
          disabled={isTracking}
          min={1}
          onChange={(value) =>
            setForm((previous) => ({
              ...previous,
              verify_interval_visits: Math.max(
                1,
                Math.round(numberOr(value, previous.verify_interval_visits))
              ),
            }))
          }
        />
        <NumberInput
          label="进入可疑的坏样本数"
          value={form.bad_samples_to_suspect}
          disabled={isTracking}
          min={1}
          onChange={(value) =>
            setForm((previous) => ({
              ...previous,
              bad_samples_to_suspect: Math.max(
                1,
                Math.round(numberOr(value, previous.bad_samples_to_suspect))
              ),
            }))
          }
        />
        <NumberInput
          label="进入重捕获的坏样本数"
          value={form.bad_samples_to_lose}
          disabled={isTracking}
          min={1}
          onChange={(value) =>
            setForm((previous) => ({
              ...previous,
              bad_samples_to_lose: Math.max(
                1,
                Math.round(numberOr(value, previous.bad_samples_to_lose))
              ),
            }))
          }
        />
        <NumberInput
          label="确认锁定的好样本数"
          value={form.good_samples_to_lock}
          disabled={isTracking}
          min={1}
          onChange={(value) =>
            setForm((previous) => ({
              ...previous,
              good_samples_to_lock: Math.max(
                1,
                Math.round(numberOr(value, previous.good_samples_to_lock))
              ),
            }))
          }
        />
        <NumberInput
          label="最大自动重扫次数 (0=不限)"
          value={form.max_relock_attempts}
          disabled={isTracking}
          min={0}
          onChange={(value) =>
            setForm((previous) => ({
              ...previous,
              max_relock_attempts: Math.max(
                0,
                Math.round(numberOr(value, previous.max_relock_attempts))
              ),
            }))
          }
        />
        <NumberInput
          label="最长跟踪时间 (s，0=连续)"
          value={form.max_tracking_duration_s}
          disabled={isTracking}
          onChange={updateNumber("max_tracking_duration_s")}
        />
      </SimpleGrid>

      <Group mt="md">
        <Button
          variant="light"
          color="gray"
          onClick={onSyncFromOdmr}
          disabled={isTracking}
        >
          继承 ODMR 扫频范围
        </Button>
        <Button
          variant="light"
          color="gray"
          onClick={onUseDefaultResonance}
          disabled={isTracking}
        >
          回到 2.87 GHz 默认范围
        </Button>
        <Text c="dimmed" size="sm">
          局部重扫会自动扩大，但不会超过这里设置的起止频率。
        </Text>
      </Group>

      <SimpleGrid cols={{ base: 1, md: 3 }} mt="lg">
        <Switch
          label="保存长期电流数据"
          description="后台每秒聚合，CSV 增量落盘并可导出 Excel"
          checked={form.record_enabled}
          disabled={isTracking}
          onChange={(event) =>
            setForm((previous) => ({
              ...previous,
              record_enabled: event.currentTarget.checked,
            }))
          }
        />
        <NumberInput
          label="保存间隔 (s)"
          description="13 小时建议保持 1 s"
          value={form.record_interval_s}
          disabled={isTracking || !form.record_enabled}
          min={0.1}
          max={3600}
          decimalScale={1}
          onChange={updateNumber("record_interval_s", 0.1)}
        />
        <TextInput
          label="实验标签"
          description="会写入文件名和参数表"
          value={form.record_label}
          disabled={isTracking || !form.record_enabled}
          maxLength={80}
          placeholder="例如 coil_2A_13h"
          onChange={(event) =>
            setForm((previous) => ({
              ...previous,
              record_label: event.currentTarget.value,
            }))
          }
        />
      </SimpleGrid>

      <Group mt="lg">
        <Button
          color="cyan"
          onClick={startTracking}
          loading={lockState === "connecting" || lockState === "acquiring"}
          disabled={measurementBusy || isTracking}
        >
          启动 PID 双峰跟踪
        </Button>
        <Button color="red" variant="light" onClick={stopTracking} disabled={!isTracking}>
          停止连续跟踪
        </Button>
        <Button
          variant="light"
          color="blue"
          onClick={downloadRecording}
          loading={isDownloadingRecording}
          disabled={!recordingStatus?.download_available}
        >
          下载 Excel 快照
        </Button>
        {form.record_enabled || recordingStatus?.session_id ? (
          <Badge
            variant="light"
            color={recordingStatus?.status === "recording" ? "teal" : "gray"}
          >
            已保存 {numberOr(recordingStatus?.rows_written, 0)} 点
          </Badge>
        ) : null}
        <Badge variant="light" color={minimumCalibration ? "teal" : "gray"}>
          物理峰心标定 {minimumCalibration?.point_count || 0} 点
        </Badge>
        <Badge variant="light" color={dcIndependent ? "teal" : "yellow"}>
          {dcIndependent ? "独立峰存在性观测" : "FM R 双瓣谷 + 周期 b/g 验证"}
        </Badge>
        <Badge variant="light" color="gray">电流幅值模式</Badge>
      </Group>

      <Group mt="md" align="end">
        <NumberInput
          label="当前已知标定电流 (A)"
          value={knownCurrentA}
          onChange={setKnownCurrentA}
          placeholder="例如 0.01"
          w={240}
        />
        <Button variant="light" onClick={addPhysicalCalibrationPoint}>
          将当前物理峰心 Δf 加入标定
        </Button>
      </Group>

      <SimpleGrid cols={{ base: 1, md: 2, xl: 6 }} mt="lg">
        <MetricCard
          label="实际目标"
          value={targetLabel(latestPoint?.tracking_target || activeTarget)}
          hint={`${latestPoint?.left_state || "--"} / ${latestPoint?.right_state || "--"}`}
        />
        <MetricCard
          label="左峰 fL"
          value={Number.isFinite(Number(latestPoint?.left_frequency_hz)) ? formatGHz(latestPoint.left_frequency_hz) : "--"}
          hint={`误差 ${formatKHz(latestPoint?.left_error_hz)}`}
        />
        <MetricCard
          label="右峰 fR"
          value={Number.isFinite(Number(latestPoint?.right_frequency_hz)) ? formatGHz(latestPoint.right_frequency_hz) : "--"}
          hint={`误差 ${formatKHz(latestPoint?.right_error_hz)}`}
        />
        <MetricCard
          label="实时劈裂 Δf"
          value={formatMHz(latestPoint?.splitting_hz)}
          hint={
            Number.isFinite(Number(latestPoint?.delta_f_sigma_hz))
              ? `σ ${formatKHz(latestPoint.delta_f_sigma_hz)}`
              : "fR - fL"
          }
        />
        <MetricCard
          label="实时电流"
          value={formatCurrent(latestPoint?.estimated_current_a)}
          hint={
            latestPoint?.valid
              ? saturated
                ? "有效；PID 输出已限幅"
                : "有效输出"
              : `无效：${latestPoint?.invalid_reason || "等待锁定"}`
          }
        />
        <MetricCard
          label="更新速率"
          value={
            Number.isFinite(latestUpdateRateHz)
              ? `${latestUpdateRateHz.toFixed(2)} Hz`
              : "--"
          }
          hint={`自动重扫 ${relockCount} 次`}
        />
      </SimpleGrid>

      <SimpleGrid cols={{ base: 1, md: 2, xl: 4 }} mt="md">
        <MetricCard
          label="左下发频率"
          value={Number.isFinite(Number(leftPid.applied_hz)) ? formatGHz(leftPid.applied_hz) : "--"}
          hint={`P ${formatKHz(leftPid.p_hz)} / I ${formatKHz(leftPid.i_hz)} / D ${formatKHz(leftPid.d_hz)}`}
        />
        <MetricCard
          label="右下发频率"
          value={Number.isFinite(Number(rightPid.applied_hz)) ? formatGHz(rightPid.applied_hz) : "--"}
          hint={`P ${formatKHz(rightPid.p_hz)} / I ${formatKHz(rightPid.i_hz)} / D ${formatKHz(rightPid.d_hz)}`}
        />
        <MetricCard
          label="左积分状态"
          value={formatKHz(leftPid.i_hz)}
          hint={leftPid.saturated ? "混合抗饱和生效" : `q ${formatKHz(latestPoint?.left_q_hz)}`}
        />
        <MetricCard
          label="右积分状态"
          value={formatKHz(rightPid.i_hz)}
          hint={rightPid.saturated ? "混合抗饱和生效" : `q ${formatKHz(latestPoint?.right_q_hz)}`}
        />
      </SimpleGrid>

      <Text fw={600} mt="lg">跟踪时间瓶颈分析</Text>
      <SimpleGrid cols={{ base: 1, md: 2, xl: 4 }} mt="xs">
        <MetricCard
          label="自动判定瓶颈"
          value={timingBottleneckLabel(timingDiagnostics?.bottleneck)}
          hint={
            Number.isFinite(Number(timingDiagnostics?.stage_share?.[timingDiagnostics?.bottleneck]))
              ? `占采集时间 ${(Number(timingDiagnostics.stage_share[timingDiagnostics.bottleneck]) * 100).toFixed(1)}%`
              : "累计首个闭环周期后开始分析"
          }
        />
        <MetricCard
          label="实测双峰更新率"
          value={
            Number.isFinite(Number(timingDiagnostics?.measured_update_rate_hz))
              ? `${Number(timingDiagnostics.measured_update_rate_hz).toFixed(2)} Hz`
              : "--"
          }
          hint={`周期 P50 ${formatMs(timingDiagnostics?.cycle_median_ms)} / P95 ${formatMs(timingDiagnostics?.cycle_p95_ms)}`}
        />
        <MetricCard
          label="微波 SCPI 写频"
          value={formatMs(timingDiagnostics?.stage_mean_ms?.microwave_command_ms)}
          hint="每个频点的 VISA resource.write 耗时"
        />
        <MetricCard
          label="显式稳定等待"
          value={formatMs(timingDiagnostics?.stage_mean_ms?.settle_ms)}
          hint={`配置 ${formatMs(timingDiagnostics?.configured_tracking_settle_ms)} / 实际下限 ${formatMs(timingDiagnostics?.effective_settle_ms)}`}
        />
        <MetricCard
          label="Zurich 设备锁等待"
          value={formatMs(timingDiagnostics?.stage_mean_ms?.lock_wait_ms)}
          hint={
            timingDiagnostics?.background_sampler_running
              ? `后台 poll 开启，记录窗 ${formatMs(timingDiagnostics?.background_poll_recording_ms)}`
              : "后台 poll 未运行"
          }
        />
        <MetricCard
          label="Zurich 单点读取"
          value={formatMs(timingDiagnostics?.stage_mean_ms?.lockin_read_ms)}
          hint="demod.sample() 调用耗时"
        />
        <MetricCard
          label="单频点采集"
          value={formatMs(timingDiagnostics?.acquisition_median_ms)}
          hint={`P95 ${formatMs(timingDiagnostics?.acquisition_p95_ms)}`}
        />
        <MetricCard
          label="实际锁相配置"
          value={
            Number.isFinite(Number(timingDiagnostics?.device_bandwidth_hz ?? timingDiagnostics?.lockin_bandwidth_hz))
              ? `${Number(timingDiagnostics?.device_bandwidth_hz ?? timingDiagnostics?.lockin_bandwidth_hz).toFixed(2)} Hz`
              : "--"
          }
          hint={`Demod ${timingDiagnostics?.demod_index ?? "--"} / τ ${formatMs(timingDiagnostics?.device_time_constant_ms ?? timingDiagnostics?.lockin_time_constant_ms)} / ${timingDiagnostics?.device_filter_order ?? timingDiagnostics?.lockin_filter_order ?? "--"} 阶${timingDiagnostics?.filter_cache_mismatch ? "；后端缓存不一致" : ""}`}
        />
      </SimpleGrid>

      <Grid mt="lg">
        <Grid.Col span={{ base: 12, xl: 7 }}>
          <Stack gap="xs">
            <Text fw={600}>左右共振频率</Text>
            <PlotCard
              traces={[
                {
                  name: "fL",
                  x: elapsed,
                  y: leftGHz,
                  lineColor: "#64e4c2",
                  hovertemplate: "%{x:.3f} s<br>fL=%{y:.9f} GHz<extra></extra>",
                },
                {
                  name: "fR",
                  x: elapsed,
                  y: rightGHz,
                  lineColor: "#ffb86c",
                  hovertemplate: "%{x:.3f} s<br>fR=%{y:.9f} GHz<extra></extra>",
                },
              ]}
              xTitle="Elapsed Time (s)"
              yTitle="Frequency (GHz)"
              uirevision="current-pid-frequency"
            />
          </Stack>
        </Grid.Col>
        <Grid.Col span={{ base: 12, xl: 5 }}>
          <Stack gap="xs">
            <Text fw={600}>实时换算电流</Text>
            <PlotCard
              traces={[
                {
                  name: "Current",
                  x: elapsed,
                  y: currentMa,
                  lineColor: "#8ab4ff",
                  hovertemplate: "%{x:.3f} s<br>I=%{y:.6f} mA<extra></extra>",
                },
              ]}
              xTitle="Elapsed Time (s)"
              yTitle="Current (mA)"
              uirevision="current-pid-current"
            />
          </Stack>
        </Grid.Col>
      </Grid>
    </SectionCard>
  );
}
