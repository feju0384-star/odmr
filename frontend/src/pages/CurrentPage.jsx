import { useEffect, useRef, useState } from "react";
import {
  Badge,
  Button,
  Group,
  SimpleGrid,
  Stack,
  Table,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { CurrentTrackingPanel } from "../components/CurrentTrackingPanel";
import { MetricCard } from "../components/MetricCard";
import { SectionCard } from "../components/SectionCard";
import { useDashboard } from "../hooks/useDashboard";

const PHYSICAL_CALIBRATION_STORAGE_KEY = "nv-current-physical-calibration-v3";
const LEGACY_CURRENT_STORAGE_KEY = "nv-current-measurement-state-v2";

function toFiniteNumber(value, fallback = 0) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : fallback;
}

function isPhysicalCalibrationPoint(point) {
  return (
    point?.source === "physical_peak_tracking" &&
    Number.isFinite(Number(point?.current_a)) &&
    Number.isFinite(Number(point?.resonance_splitting_hz)) &&
    Number(point.resonance_splitting_hz) > 0
  );
}

function loadPhysicalCalibrationPoints() {
  if (typeof window === "undefined") {
    return [];
  }
  try {
    const currentRaw = window.localStorage.getItem(
      PHYSICAL_CALIBRATION_STORAGE_KEY
    );
    if (currentRaw) {
      const current = JSON.parse(currentRaw);
      const points = Array.isArray(current) ? current : current?.points;
      return (Array.isArray(points) ? points : [])
        .filter(isPhysicalCalibrationPoint)
        .sort((left, right) => Number(left.current_a) - Number(right.current_a));
    }

    // 只迁移旧存储中的物理峰心点；旧过零点数据原样保留在 v2，不破坏用户数据。
    const legacyRaw = window.localStorage.getItem(LEGACY_CURRENT_STORAGE_KEY);
    const legacy = legacyRaw ? JSON.parse(legacyRaw) : {};
    return (Array.isArray(legacy?.calibrationPoints)
      ? legacy.calibrationPoints
      : []
    )
      .filter(isPhysicalCalibrationPoint)
      .sort((left, right) => Number(left.current_a) - Number(right.current_a));
  } catch {
    return [];
  }
}

function persistPhysicalCalibrationPoints(points) {
  if (typeof window === "undefined") {
    return;
  }
  window.localStorage.setItem(
    PHYSICAL_CALIBRATION_STORAGE_KEY,
    JSON.stringify({
      version: 3,
      updated_at: new Date().toISOString(),
      definition: "I = a * (fR - fL) + b",
      points,
    })
  );
}

function fitPhysicalCalibration(points) {
  const valid = (Array.isArray(points) ? points : []).filter(
    isPhysicalCalibrationPoint
  );
  if (valid.length < 2) {
    return null;
  }

  const pairs = valid.map((point) => ({
    x: Number(point.resonance_splitting_hz),
    y: Number(point.current_a),
  }));
  const n = pairs.length;
  const sumX = pairs.reduce((sum, point) => sum + point.x, 0);
  const sumY = pairs.reduce((sum, point) => sum + point.y, 0);
  const sumXX = pairs.reduce((sum, point) => sum + point.x * point.x, 0);
  const sumXY = pairs.reduce((sum, point) => sum + point.x * point.y, 0);
  const denominator = n * sumXX - sumX * sumX;
  if (Math.abs(denominator) < 1e-30) {
    return null;
  }

  const slope = (n * sumXY - sumX * sumY) / denominator;
  const intercept = (sumY - slope * sumX) / n;
  const meanY = sumY / n;
  const residuals = pairs.map(
    (point) => point.y - (slope * point.x + intercept)
  );
  const totalVariance = pairs.reduce(
    (sum, point) => sum + (point.y - meanY) ** 2,
    0
  );
  const residualVariance = residuals.reduce(
    (sum, residual) => sum + residual * residual,
    0
  );
  return {
    slope_a_per_hz: slope,
    intercept_a: intercept,
    r_squared:
      totalVariance > 0 ? 1 - residualVariance / totalVariance : 1,
    residual_rms_a: Math.sqrt(residualVariance / n),
    point_count: n,
    delta_f_min_hz: Math.min(...pairs.map((point) => point.x)),
    delta_f_max_hz: Math.max(...pairs.map((point) => point.x)),
    current_min_a: Math.min(...pairs.map((point) => point.y)),
    current_max_a: Math.max(...pairs.map((point) => point.y)),
  };
}

function createDefaultCurrentForm(measurement, activeChannel) {
  const tracking = measurement?.last_current_tracking_request || {};
  const legacy = measurement?.last_current_request || {};
  return {
    channel_index: toFiniteNumber(
      tracking.channel_index ?? legacy.channel_index,
      activeChannel
    ),
    start_hz: toFiniteNumber(
      tracking.start_hz ?? legacy.start_hz,
      2.83e9
    ),
    stop_hz: toFiniteNumber(
      tracking.stop_hz ?? legacy.stop_hz,
      2.91e9
    ),
    search_points: Math.max(
      11,
      Math.round(
        toFiniteNumber(tracking.search_points ?? legacy.search_points, 121)
      )
    ),
    settle_ms: Math.max(
      0.1,
      toFiniteNumber(
        tracking.search_settle_ms ?? legacy.settle_ms,
        30
      )
    ),
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

function formatSplittingMHz(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? `${(numeric / 1e6).toFixed(6)} MHz`
    : "--";
}

function formatScientific(value, digits = 4) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric.toExponential(digits) : "--";
}

function formatGHz(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric)
    ? `${(numeric / 1e9).toFixed(9)} GHz`
    : "--";
}

function toCsvCell(value) {
  if (value === null || value === undefined) {
    return "";
  }
  const serialized = String(value);
  return /[",\n]/.test(serialized)
    ? `"${serialized.replace(/"/g, '""')}"`
    : serialized;
}

function downloadFile(filename, content, type) {
  const blob = new Blob([content], { type });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function exportTimestamp(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}${pad(date.getMonth() + 1)}${pad(
    date.getDate()
  )}_${pad(date.getHours())}${pad(date.getMinutes())}${pad(
    date.getSeconds()
  )}`;
}

function measurementModeLabel(mode) {
  if (mode === "current_tracking") {
    return "PID 双峰跟踪";
  }
  if (mode === "odmr") {
    return "ODMR 扫描";
  }
  if (mode === "sensitivity") {
    return "灵敏度测量";
  }
  return "空闲";
}

export default function CurrentPage() {
  const { data, refresh, error, loading } = useDashboard(1500);
  const [currentForm, setCurrentForm] = useState(null);
  const [calibrationPoints, setCalibrationPoints] = useState(
    loadPhysicalCalibrationPoints
  );
  const hasHydratedRef = useRef(false);

  useEffect(() => {
    persistPhysicalCalibrationPoints(calibrationPoints);
  }, [calibrationPoints]);

  useEffect(() => {
    if (!data?.measurement || hasHydratedRef.current) {
      return;
    }
    setCurrentForm(
      createDefaultCurrentForm(
        data.measurement,
        data.lockin?.active_channel ?? 0
      )
    );
    hasHydratedRef.current = true;
  }, [data]);

  if (!data || !currentForm) {
    return (
      <Stack gap="md">
        <Text className="page-title">电流测量</Text>
        <Text c="dimmed">
          {error || (loading ? "正在加载电流测量页面..." : "测量数据为空")}
        </Text>
      </Stack>
    );
  }

  const measurement = data.measurement || {};
  const calibrationModel = fitPhysicalCalibration(calibrationPoints);

  const syncFromOdmr = () => {
    const odmrRequest = measurement.last_request || {};
    setCurrentForm((previous) => ({
      ...previous,
      start_hz: toFiniteNumber(odmrRequest.start_hz, 2.83e9),
      stop_hz: toFiniteNumber(odmrRequest.stop_hz, 2.91e9),
    }));
    notifications.show({
      color: "teal",
      title: "已同步",
      message: "已继承当前 ODMR 的扫描窗口。",
    });
  };

  const useDefaultResonance = () => {
    setCurrentForm((previous) => ({
      ...previous,
      start_hz: 2.83e9,
      stop_hz: 2.91e9,
    }));
  };

  const addPhysicalCalibrationPoint = (point) => {
    setCalibrationPoints((previous) =>
      [
        ...previous,
        {
          id: `${Date.now()}-${Math.random().toString(16).slice(2, 8)}`,
          created_at: new Date().toISOString(),
          current_a: Number(point.current_a),
          resonance_splitting_hz: Number(point.resonance_splitting_hz),
          left_resonance_hz: Number(point.left_resonance_hz),
          right_resonance_hz: Number(point.right_resonance_hz),
          delta_f_sigma_hz: Number(point.delta_f_sigma_hz),
          source: "physical_peak_tracking",
        },
      ].sort((left, right) => Number(left.current_a) - Number(right.current_a))
    );
  };

  const removeCalibrationPoint = (id) => {
    setCalibrationPoints((previous) =>
      previous.filter((point) => point.id !== id)
    );
  };

  const clearCalibration = () => {
    setCalibrationPoints([]);
    notifications.show({
      color: "yellow",
      title: "已清空",
      message: "物理峰心标定表已清空。",
    });
  };

  const exportCalibrationCsv = () => {
    const header = [
      "current_a",
      "left_resonance_hz",
      "right_resonance_hz",
      "delta_f_hz",
      "delta_f_sigma_hz",
      "created_at",
    ];
    const rows = calibrationPoints.map((point) => [
      point.current_a,
      point.left_resonance_hz,
      point.right_resonance_hz,
      point.resonance_splitting_hz,
      point.delta_f_sigma_hz,
      point.created_at,
    ]);
    const csv = [header, ...rows]
      .map((row) => row.map(toCsvCell).join(","))
      .join("\n");
    downloadFile(
      `physical_peak_calibration_${exportTimestamp()}.csv`,
      `\uFEFF${csv}`,
      "text/csv;charset=utf-8"
    );
  };

  const exportCalibrationJson = () => {
    downloadFile(
      `physical_peak_calibration_${exportTimestamp()}.json`,
      JSON.stringify(
        {
          exported_at: new Date().toISOString(),
          definition: "I = a * (fR - fL) + b",
          model: calibrationModel,
          points: calibrationPoints,
        },
        null,
        2
      ),
      "application/json;charset=utf-8"
    );
  };

  return (
    <Stack gap="lg">
      <div>
        <Text className="eyebrow">Step 5</Text>
        <Text className="page-title">电流测量</Text>
        <Text c="dimmed" maw={1000}>
          使用 FM R 双瓣谷确定左右物理共振峰心，以 X/Y 复数投影连续跟踪
          fL、fR，并通过物理峰心劈裂 Δf=fR-fL 换算电流。过零点标定不再参与本页计算。
        </Text>
      </div>

      <SimpleGrid cols={{ base: 1, md: 2, xl: 6 }}>
        <MetricCard
          label="当前任务"
          value={measurementModeLabel(measurement.mode)}
          hint={measurement.status || "idle"}
        />
        <MetricCard
          label="物理峰心标定点"
          value={String(calibrationPoints.length)}
          hint="只统计 physical_peak_tracking"
        />
        <MetricCard
          label="标定 Δf 范围"
          value={
            calibrationModel
              ? `${formatSplittingMHz(
                  calibrationModel.delta_f_min_hz
                )} ～ ${formatSplittingMHz(calibrationModel.delta_f_max_hz)}`
              : "--"
          }
          hint="超出范围不外推"
        />
        <MetricCard
          label="标定电流范围"
          value={
            calibrationModel
              ? `${formatCurrent(
                  calibrationModel.current_min_a
                )} ～ ${formatCurrent(calibrationModel.current_max_a)}`
              : "--"
          }
          hint="建议覆盖实际实验范围"
        />
        <MetricCard
          label="标定斜率"
          value={formatScientific(calibrationModel?.slope_a_per_hz)}
          hint="A/Hz"
        />
        <MetricCard
          label="标定质量"
          value={
            calibrationModel
              ? `R² ${calibrationModel.r_squared.toFixed(6)}`
              : "--"
          }
          hint={
            calibrationModel
              ? `残差 RMS ${formatCurrent(
                  calibrationModel.residual_rms_a
                )}`
              : "至少需要两个不同 Δf 的点"
          }
        />
      </SimpleGrid>

      <CurrentTrackingPanel
        currentForm={currentForm}
        onCurrentFormChange={setCurrentForm}
        onSyncFromOdmr={syncFromOdmr}
        onUseDefaultResonance={useDefaultResonance}
        calibrationPoints={calibrationPoints}
        onAddPhysicalCalibrationPoint={addPhysicalCalibrationPoint}
        lockinConnected={Boolean(data.lockin.connected)}
        microwaveConnected={Boolean(data.microwave.connected)}
        measurementBusy={Boolean(measurement.running)}
      />

      <SectionCard
        title="物理峰心电流标定"
        description="只使用左右物理共振峰心的 Δf=fR-fL 拟合 I=aΔf+b；旧过零点标定点不会进入拟合。"
        badge={calibrationModel ? "Calibrated" : "Need Points"}
      >
        <Group mb="md">
          <Button
            variant="light"
            color="gray"
            onClick={exportCalibrationCsv}
            disabled={!calibrationPoints.length}
          >
            导出标定 CSV
          </Button>
          <Button
            variant="light"
            color="gray"
            onClick={exportCalibrationJson}
            disabled={!calibrationPoints.length}
          >
            导出标定 JSON
          </Button>
          <Button
            variant="subtle"
            color="red"
            onClick={clearCalibration}
            disabled={!calibrationPoints.length}
          >
            清空物理峰心标定
          </Button>
          <Button variant="subtle" color="gray" onClick={refresh}>
            刷新状态
          </Button>
        </Group>

        {calibrationPoints.length ? (
          <div style={{ overflowX: "auto" }}>
            <Table striped highlightOnHover withTableBorder>
              <Table.Thead>
                <Table.Tr>
                  <Table.Th>#</Table.Th>
                  <Table.Th>已知电流</Table.Th>
                  <Table.Th>左峰 fL</Table.Th>
                  <Table.Th>右峰 fR</Table.Th>
                  <Table.Th>物理劈裂 Δf</Table.Th>
                  <Table.Th>Δf 不确定度</Table.Th>
                  <Table.Th>时间</Table.Th>
                  <Table.Th>来源</Table.Th>
                  <Table.Th>操作</Table.Th>
                </Table.Tr>
              </Table.Thead>
              <Table.Tbody>
                {calibrationPoints.map((point, index) => (
                  <Table.Tr key={point.id}>
                    <Table.Td>{index + 1}</Table.Td>
                    <Table.Td>{formatCurrent(point.current_a)}</Table.Td>
                    <Table.Td>{formatGHz(point.left_resonance_hz)}</Table.Td>
                    <Table.Td>{formatGHz(point.right_resonance_hz)}</Table.Td>
                    <Table.Td>
                      {formatSplittingMHz(point.resonance_splitting_hz)}
                    </Table.Td>
                    <Table.Td>
                      {formatSplittingMHz(point.delta_f_sigma_hz)}
                    </Table.Td>
                    <Table.Td>
                      {String(point.created_at || "")
                        .replace("T", " ")
                        .slice(0, 19)}
                    </Table.Td>
                    <Table.Td>
                      <Badge variant="light" color="teal">
                        物理峰心
                      </Badge>
                    </Table.Td>
                    <Table.Td>
                      <Button
                        variant="subtle"
                        color="red"
                        onClick={() => removeCalibrationPoint(point.id)}
                      >
                        删除
                      </Button>
                    </Table.Td>
                  </Table.Tr>
                ))}
              </Table.Tbody>
            </Table>
          </div>
        ) : (
          <Text c="dimmed">
            暂无物理峰心标定点。先锁定双峰，输入已知电流，再点击“将当前物理峰心
            Δf 加入标定”。
          </Text>
        )}
      </SectionCard>
    </Stack>
  );
}
