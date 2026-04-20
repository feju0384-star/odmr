import { useEffect, useRef, useState } from "react";
import {
  Button,
  Grid,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Tabs,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { MetricCard } from "../components/MetricCard";
import { SectionCard } from "../components/SectionCard";
import { StreamingSignalPlot } from "../components/StreamingSignalPlot";
import { useDashboard } from "../hooks/useDashboard";
import { api, shortReadout, wsUrl } from "../lib/api";

const BWTC_SCALING = {
  1: 1.0,
  2: 0.643594,
  3: 0.509825,
  4: 0.434979,
  5: 0.385614,
  6: 0.349946,
  7: 0.322629,
  8: 0.300845,
};
const TIME_WINDOW_OPTIONS = [
  { label: "1 s", value: "1" },
  { label: "2 s", value: "2" },
  { label: "5 s", value: "5" },
  { label: "10 s", value: "10" },
  { label: "60 s", value: "60" },
  { label: "300 s", value: "300" },
];
const FALLBACK_REFERENCE_SOURCES = [
  { value: "internal", label: "内部参考" },
  { value: "external", label: "外部参考" },
];

function factor(order) {
  return BWTC_SCALING[Number(order)] ?? BWTC_SCALING[4];
}

function bandwidthFromTcMs(timeConstantMs, order) {
  const tcSeconds = Number(timeConstantMs) / 1000;
  if (!tcSeconds) {
    return 0;
  }
  return factor(order) / (2 * Math.PI * tcSeconds);
}

function tcMsFromBandwidth(bandwidthHz, order) {
  const bw = Number(bandwidthHz);
  if (!bw) {
    return 0;
  }
  return (factor(order) / (2 * Math.PI * bw)) * 1000;
}

function formatUv(value) {
  return `${Number(value ?? 0).toFixed(3)} uV`;
}

function isExternalReferenceCapableDemod(demodIndex) {
  return Number(demodIndex) === 1 || Number(demodIndex) === 3;
}

function resolveExternalReferenceTrackerChannel(channelIndex, channelCount) {
  if (channelCount <= 1) {
    return 0;
  }
  if (channelIndex < 2) {
    return Math.min(1, channelCount - 1);
  }
  return Math.min(3, channelCount - 1);
}

function useLockinLive() {
  const [data, setData] = useState(null);

  useEffect(() => {
    let active = true;
    let socket;

    const connect = () => {
      socket = new WebSocket(wsUrl("/instruments/lockin/ws"));
      socket.onmessage = (event) => {
        if (!active) {
          return;
        }
        try {
          const next = JSON.parse(event.data);
          if (next.type === "lockin_live") {
            setData(next);
          }
        } catch {
          // Ignore malformed frames.
        }
      };
      socket.onclose = () => {
        if (active) {
          window.setTimeout(connect, 500);
        }
      };
    };

    connect();
    return () => {
      active = false;
      socket?.close();
    };
  }, []);

  return data;
}

export default function LockinPage() {
  const { data, refresh, error, loading } = useDashboard(1500);
  const live = useLockinLive();
  const [channelIndex, setChannelIndex] = useState("0");
  const [metric, setMetric] = useState("r_uv");
  const [timeWindowSec, setTimeWindowSec] = useState("10");
  const [form, setForm] = useState(null);
  const [isDirty, setIsDirty] = useState(false);
  const [yAxisAuto, setYAxisAuto] = useState(true);
  const [manualYMin, setManualYMin] = useState("");
  const [manualYMax, setManualYMax] = useState("");
  const [plotMeta, setPlotMeta] = useState({ unitLabel: "uV", visiblePoints: 0, yRange: null });
  const lastChannelRef = useRef(-1);

  const activeChannel = Number(channelIndex);
  const selectedWindowSec = Number(timeWindowSec) || 10;
  const channel = data?.lockin.channels?.[activeChannel];
  const selectors = data?.lockin?.selectors ?? {};
  const activeSignal = live?.signal_channels?.[activeChannel] ?? data?.signal_channels?.[activeChannel];
  const activeBatch = live?.signal_batches?.[activeChannel];
  const sampleRateHz = Number(live?.sample_rate_hz ?? form?.sample_rate_hz ?? 0);
  const referenceSources = selectors.reference_sources ?? FALLBACK_REFERENCE_SOURCES;
  const externalReferenceInputs = selectors.external_reference_inputs ?? selectors.input_signals ?? [];
  const activeDemodIndex = Number(form?.demod_index ?? channel?.demod_index ?? activeChannel);
  const trackerChannelIndex = resolveExternalReferenceTrackerChannel(
    activeChannel,
    data?.lockin.channels?.length ?? 0,
  );
  const directExternalReferenceCapable = isExternalReferenceCapableDemod(activeDemodIndex);

  useEffect(() => {
    if (!channel) {
      return;
    }
    if (lastChannelRef.current === -1) {
      lastChannelRef.current = activeChannel;
      setForm(channel);
      return;
    }
    const channelChanged = lastChannelRef.current !== activeChannel;
    if (channelChanged) {
      lastChannelRef.current = activeChannel;
      setForm(channel);
      setIsDirty(false);
      setYAxisAuto(true);
      setManualYMin("");
      setManualYMax("");
      setPlotMeta({ unitLabel: "uV", visiblePoints: 0, yRange: null });
      return;
    }
    if (!isDirty) {
      setForm(channel);
    }
  }, [activeChannel, channel, isDirty]);

  useEffect(() => {
    setYAxisAuto(true);
    setManualYMin("");
    setManualYMax("");
  }, [metric, activeChannel]);

  const updateForm = (changes) => {
    setIsDirty(true);
    setForm((prev) => ({ ...(prev || {}), ...changes }));
  };

  const updateNumber = (key, fallback = 0) => (value) => {
    updateForm({ [key]: Number(value) || fallback });
  };

  const updateBandwidth = (value) => {
    const bandwidth = Number(value) || 0;
    updateForm({
      low_pass_bandwidth_hz: bandwidth,
      time_constant_ms: bandwidth ? tcMsFromBandwidth(bandwidth, form.low_pass_order) : 0,
    });
  };

  const updateTimeConstant = (value) => {
    const timeConstantMs = Number(value) || 0;
    updateForm({
      time_constant_ms: timeConstantMs,
      low_pass_bandwidth_hz: timeConstantMs ? bandwidthFromTcMs(timeConstantMs, form.low_pass_order) : 0,
    });
  };

  const updateFilterOrder = (value) => {
    const order = Number(value) || 1;
    updateForm({
      low_pass_order: order,
      low_pass_bandwidth_hz: form.time_constant_ms
        ? bandwidthFromTcMs(form.time_constant_ms, order)
        : form.low_pass_bandwidth_hz,
    });
  };

  const save = async () => {
    try {
      await api.saveLockinChannel({
        ...form,
        channel_index: activeChannel,
        display_source: metric.replace("_uv", "_v"),
      });
      notifications.show({
        color: "teal",
        title: "保存成功",
        message: `锁相通道 ${activeChannel + 1} 参数已更新`,
      });
      setIsDirty(false);
      await refresh();
    } catch (err) {
      notifications.show({
        color: "red",
        title: "保存失败",
        message: err instanceof Error ? err.message : "未知错误",
      });
    }
  };

  const revertChanges = () => {
    if (!channel) {
      return;
    }
    setForm(channel);
    setIsDirty(false);
  };

  const reloadChannel = async () => {
    setIsDirty(false);
    await refresh();
    notifications.show({
      color: "teal",
      title: "已重载",
      message: `通道 ${activeChannel + 1} 已从设备状态重新同步`,
    });
  };

  const presetExternalAuxReference = async () => {
    const channels = data?.lockin.channels ?? [];
    if (!channels.length) {
      return;
    }
    const trackerChannel = channels[trackerChannelIndex] ?? {};
    const sharedOscIndex = Number(form?.osc_index ?? channel?.osc_index ?? trackerChannel.osc_index ?? 0) || 0;
    const nominalFrequencyHz =
      Number(form?.demod_freq_hz ?? channel?.demod_freq_hz ?? trackerChannel.demod_freq_hz ?? 10000) || 10000;
    try {
      const response = await api.saveLockinChannel({
        ...trackerChannel,
        channel_index: trackerChannelIndex,
        demod_index: Number(trackerChannel.demod_index ?? trackerChannelIndex),
        osc_index: sharedOscIndex,
        demod_freq_hz: nominalFrequencyHz,
        input_signal: 8,
        reference_source: "external",
        external_reference_index: 8,
        enabled: true,
        display_source: trackerChannel.display_source ?? "r_v",
      });
      setIsDirty(false);
      await refresh();
      setChannelIndex(String(trackerChannelIndex));
      notifications.show({
        color: "teal",
        title: "外参考已写入",
        message:
          response?.message ??
          `已把通道 ${trackerChannelIndex + 1} 配成 Aux In 1 外参考跟踪器；与其共用 Osc 的测量通道会自动锁频`,
      });
    } catch (err) {
      notifications.show({
        color: "red",
        title: "外参考预设失败",
        message: err instanceof Error ? err.message : "未知错误",
      });
    }
  };

  if (!data || !form) {
    return (
      <Stack gap="md">
        <Text className="page-title">锁相通道设置与实时解调</Text>
        <Text c="dimmed">{error || (loading ? "正在加载锁相数据..." : "锁相数据为空")}</Text>
      </Stack>
    );
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start">
        <div>
          <Text className="eyebrow">Step 2</Text>
          <Text className="page-title">锁相通道设置与实时解调</Text>
          <Text c="dimmed" maw={860}>
            外参考现在按 LabOne 的真实逻辑处理: 由 Demod 2/4 负责参考恢复，测量通道只需要和它共用同一个 Osc 即可自动锁频。
          </Text>
        </div>
        <SegmentedControl
          value={metric}
          onChange={setMetric}
          data={[
            { label: "R", value: "r_uv" },
            { label: "X", value: "x_uv" },
            { label: "Y", value: "y_uv" },
          ]}
        />
      </Group>

      <Group justify="space-between" align="center">
        <Text fw={700}>显示时间窗</Text>
        <SegmentedControl value={timeWindowSec} onChange={setTimeWindowSec} data={TIME_WINDOW_OPTIONS} />
      </Group>

      <SimpleGrid cols={{ base: 1, md: 4 }}>
        <MetricCard
          label="锁相设备"
          value={data.lockin.connected ? data.lockin.serial : "未连接"}
          hint={data.lockin.name || "Zurich Instruments"}
        />
        <MetricCard
          label="参考源"
          value={form.reference_source === "external" ? "外部参考" : "内部参考"}
          hint={`通道 ${activeChannel + 1}`}
        />
        <MetricCard
          label="解调频率"
          value={`${Number(form.demod_freq_hz ?? 0).toFixed(3)} Hz`}
          hint={`${Number(form.time_constant_ms ?? 0).toFixed(3)} ms`}
        />
        <MetricCard
          label="实时读数"
          value={formatUv(activeSignal?.[metric])}
          hint={`${shortReadout(metric.replace("_uv", "_v"))} | ${sampleRateHz.toFixed(0)} Sa/s | ${plotMeta.visiblePoints} pts`}
        />
      </SimpleGrid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="通道参数"
            description="编辑中的参数不会被后台轮询覆盖；只有点击保存后才会真正下发。"
            badge={isDirty ? "有未保存修改" : "已同步"}
          >
            <Tabs value={channelIndex} onChange={(value) => setChannelIndex(value ?? "0")} color="cyan">
              <Tabs.List grow mb="md">
                {data.lockin.channels.map((item, index) => (
                  <Tabs.Tab key={index} value={String(index)}>
                    通道 {index + 1}
                  </Tabs.Tab>
                ))}
              </Tabs.List>
            </Tabs>

            <SimpleGrid cols={{ base: 1, md: 2 }} spacing="lg">
              <Stack gap="sm">
                <Text fw={700}>信号输入</Text>
                <Select
                  label="输入信号"
                  value={String(form.input_signal ?? 0)}
                  onChange={(value) => updateForm({ input_signal: Number(value) || 0 })}
                  data={(selectors.input_signals || []).map((item) => ({
                    value: String(item.value),
                    label: item.label,
                  }))}
                />
                <NumberInput label="电压量程 (mV)" value={form.input_range_mv} onChange={updateNumber("input_range_mv", 0)} />
                <NumberInput label="电压缩放 (V/V)" value={form.input_voltage_scaling} onChange={updateNumber("input_voltage_scaling", 1)} />
                <NumberInput label="电流量程 (mA)" value={form.current_range_ma} onChange={updateNumber("current_range_ma", 0)} />
                <NumberInput label="电流缩放 (A/A)" value={form.current_scaling} onChange={updateNumber("current_scaling", 1)} />
                <SimpleGrid cols={2}>
                  <Switch
                    checked={Boolean(form.input_impedance_50ohm)}
                    onChange={(event) => updateForm({ input_impedance_50ohm: event.currentTarget.checked })}
                    label="50 Ω 输入"
                  />
                  <Switch
                    checked={Boolean(form.input_ac_coupling)}
                    onChange={(event) => updateForm({ input_ac_coupling: event.currentTarget.checked })}
                    label="AC 耦合"
                  />
                  <Switch
                    checked={Boolean(form.input_differential)}
                    onChange={(event) => updateForm({ input_differential: event.currentTarget.checked })}
                    label="差分"
                  />
                  <Switch
                    checked={Boolean(form.input_float)}
                    onChange={(event) => updateForm({ input_float: event.currentTarget.checked })}
                    label="电压浮地"
                  />
                  <Switch
                    checked={Boolean(form.current_float)}
                    onChange={(event) => updateForm({ current_float: event.currentTarget.checked })}
                    label="电流浮地"
                  />
                </SimpleGrid>
              </Stack>

              <Stack gap="sm">
                <Text fw={700}>振荡器 / 参考源</Text>
                <NumberInput label="解调器索引" value={form.demod_index} onChange={updateNumber("demod_index", 0)} />
                <NumberInput label="振荡器索引" value={form.osc_index} onChange={updateNumber("osc_index", 0)} />
                <NumberInput
                  label={form.reference_source === "external" ? "锁定振荡器 / 名义频率 (Hz)" : "内部参考频率 (Hz)"}
                  value={form.demod_freq_hz}
                  onChange={updateNumber("demod_freq_hz", 0)}
                />
                <NumberInput label="谐波" value={form.harmonic} onChange={updateNumber("harmonic", 1)} />
                <NumberInput label="相位 (deg)" value={form.phase_deg} onChange={updateNumber("phase_deg", 0)} />
                <Select
                  label="参考源"
                  value={form.reference_source ?? "internal"}
                  onChange={(value) => updateForm({ reference_source: value || "internal" })}
                  data={referenceSources.map((item) => ({ value: String(item.value), label: item.label }))}
                  disabled={!directExternalReferenceCapable}
                />
                <Text size="xs" c="dimmed">
                  {directExternalReferenceCapable
                    ? "当前 Demod 可直接切到外参考模式。"
                    : `当前 Demod 不能直接切外参考；请用下面按钮把通道 ${trackerChannelIndex + 1} 设成 Aux In 1 外参考跟踪器。`}
                </Text>
                <Select
                  label="外部参考输入"
                  value={String(form.external_reference_index ?? 0)}
                  onChange={(value) => updateForm({ external_reference_index: Number(value) || 0 })}
                  disabled={!directExternalReferenceCapable || form.reference_source !== "external"}
                  data={externalReferenceInputs.map((item) => ({
                    value: String(item.value),
                    label: item.label,
                  }))}
                />
                <Button variant="light" color="cyan" onClick={presetExternalAuxReference}>
                  用通道 {trackerChannelIndex + 1} 跟踪 Aux In 1 (10 kHz)
                </Button>
              </Stack>

              <Stack gap="sm">
                <Text fw={700}>低通滤波器</Text>
                <Select
                  label="阶数"
                  value={String(form.low_pass_order ?? 4)}
                  onChange={updateFilterOrder}
                  data={(selectors.filter_orders || []).map((item) => ({
                    value: String(item.value),
                    label: item.label,
                  }))}
                />
                <NumberInput label="时间常数 (ms)" value={form.time_constant_ms} onChange={updateTimeConstant} decimalScale={6} />
                <NumberInput label="3 dB 带宽 (Hz)" value={form.low_pass_bandwidth_hz} onChange={updateBandwidth} decimalScale={6} />
                <Switch checked={Boolean(form.sinc_enabled)} onChange={(event) => updateForm({ sinc_enabled: event.currentTarget.checked })} label="Sinc" />
              </Stack>

              <Stack gap="sm">
                <Text fw={700}>数据传输 / 触发</Text>
                <Switch checked={Boolean(form.enabled)} onChange={(event) => updateForm({ enabled: event.currentTarget.checked })} label="数据传输开启" />
                <NumberInput label="采样率 (Sa/s)" value={form.sample_rate_hz} onChange={updateNumber("sample_rate_hz", 0)} decimalScale={3} />
                <Select
                  label="触发模式"
                  value={String(form.trigger_mode ?? 0)}
                  onChange={(value) => updateForm({ trigger_mode: Number(value) || 0 })}
                  data={(selectors.trigger_modes || []).map((item) => ({
                    value: String(item.value),
                    label: item.label,
                  }))}
                />
              </Stack>

              <Stack gap="sm">
                <Text fw={700}>AUX 手动输出</Text>
                <NumberInput label="AUX 通道" value={form.aux_output_channel} onChange={updateNumber("aux_output_channel", 0)} />
                <NumberInput label="AUX 偏置 (V)" value={form.aux_output_offset_v} onChange={updateNumber("aux_output_offset_v", 0)} decimalScale={6} />
                <Button variant="light" color="gray" onClick={() => updateForm({ aux_output_offset_v: 0 })}>
                  AUX 置零
                </Button>
              </Stack>
            </SimpleGrid>

            <Group mt="lg">
              <Button onClick={save}>保存当前通道</Button>
              <Button variant="light" color="gray" onClick={revertChanges} disabled={!isDirty}>
                撤销修改
              </Button>
              <Button variant="light" color="cyan" onClick={reloadChannel}>
                从设备重载
              </Button>
            </Group>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="实时解调曲线"
            description="图像刷新目标是 30-60 FPS，每帧承载一批样本；时间窗越长，前端只做显示级抽稀，不丢实时读数。"
            badge={`显示 ${shortReadout(metric.replace("_uv", "_v"))}`}
          >
            <Group justify="space-between" align="flex-end" mb="md">
              <Switch
                checked={yAxisAuto}
                onChange={(event) => {
                  const nextAuto = event.currentTarget.checked;
                  setYAxisAuto(nextAuto);
                  if (nextAuto) {
                    setManualYMin("");
                    setManualYMax("");
                  }
                }}
                label="纵轴自动缩放"
              />
              <Group align="flex-end">
                <NumberInput
                  label={`Y 最小值 (${plotMeta.unitLabel})`}
                  value={manualYMin}
                  onChange={(value) => {
                    setYAxisAuto(false);
                    setManualYMin(value === "" ? "" : String(value));
                  }}
                  disabled={yAxisAuto}
                  decimalScale={6}
                  hideControls
                  w={132}
                />
                <NumberInput
                  label={`Y 最大值 (${plotMeta.unitLabel})`}
                  value={manualYMax}
                  onChange={(value) => {
                    setYAxisAuto(false);
                    setManualYMax(value === "" ? "" : String(value));
                  }}
                  disabled={yAxisAuto}
                  decimalScale={6}
                  hideControls
                  w={132}
                />
              </Group>
            </Group>

            <StreamingSignalPlot
              batch={activeBatch}
              metric={metric}
              timeWindowSec={selectedWindowSec}
              lineColor={metric === "r_uv" ? "#59d1ff" : metric === "x_uv" ? "#7ef0c1" : "#ffb36f"}
              yAxisAuto={yAxisAuto}
              manualYMin={manualYMin}
              manualYMax={manualYMax}
              resetKey={`${data.lockin.serial}-${activeChannel}`}
              onMetaChange={setPlotMeta}
              yLabel={shortReadout(metric.replace("_uv", "_v"))}
              xLabel="时间 (s)"
            />

            <SimpleGrid cols={3} mt="md">
              <MetricCard label="X" value={formatUv(activeSignal?.x_uv)} />
              <MetricCard label="Y" value={formatUv(activeSignal?.y_uv)} />
              <MetricCard label="R" value={formatUv(activeSignal?.r_uv)} />
            </SimpleGrid>
          </SectionCard>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
