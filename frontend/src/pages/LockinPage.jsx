import { useEffect, useMemo, useState } from "react";
import {
  Button,
  Grid,
  Group,
  NumberInput,
  SegmentedControl,
  SimpleGrid,
  Stack,
  Tabs,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { MetricCard } from "../components/MetricCard";
import { PlotCard } from "../components/PlotCard";
import { SectionCard } from "../components/SectionCard";
import { useDashboard } from "../hooks/useDashboard";
import { api, shortReadout } from "../lib/api";

function nextHistory(prev, signal) {
  const append = (list, value) => [...list.slice(-159), value];
  return {
    x_v: append(prev.x_v, signal.x_v),
    y_v: append(prev.y_v, signal.y_v),
    r_v: append(prev.r_v, signal.r_v),
  };
}

export default function LockinPage() {
  const { data, refresh } = useDashboard(1200);
  const [channelIndex, setChannelIndex] = useState("0");
  const [metric, setMetric] = useState("r_v");
  const [form, setForm] = useState(null);
  const [history, setHistory] = useState({ x_v: [], y_v: [], r_v: [] });

  const activeChannel = Number(channelIndex);
  const channel = data?.lockin.channels?.[activeChannel];
  const activeSignal = data?.signal_channels?.[activeChannel];

  useEffect(() => {
    if (!channel) {
      return;
    }
    setForm(channel);
  }, [channel]);

  useEffect(() => {
    if (!activeSignal) {
      return;
    }
    setHistory((prev) => nextHistory(prev, activeSignal));
  }, [activeSignal]);

  const xAxis = useMemo(() => history[metric].map((_, index) => index), [history, metric]);

  if (!data || !form) {
    return null;
  }

  const save = async () => {
    try {
      await api.saveLockinChannel({
        ...form,
        channel_index: activeChannel,
        display_source: metric,
      });
      notifications.show({
        color: "teal",
        title: "Saved",
        message: `锁相通道 ${activeChannel + 1} 参数已保存`,
      });
      await refresh();
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Save failed",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  };

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start">
        <div>
          <Text className="eyebrow">Step 2</Text>
          <Text className="page-title">锁相通道设置与实时解调</Text>
          <Text c="dimmed" maw={860}>
            这里按通道组织锁相参数，右侧实时曲线可以在 X、Y、R 之间切换，布局比上一版更接近 LabOne 的模块化面板。
          </Text>
        </div>
        <Group gap="xs">
          <SegmentedControl
            value={metric}
            onChange={setMetric}
            data={[
              { label: "R", value: "r_v" },
              { label: "X", value: "x_v" },
              { label: "Y", value: "y_v" },
            ]}
          />
        </Group>
      </Group>

      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <MetricCard label="Lock-in Device" value={data.lockin.connected ? data.lockin.serial : "未连接"} hint={data.lockin.name || "Zurich Instruments"} />
        <MetricCard label="Demod Frequency" value={`${(form.demod_freq_hz / 1000).toFixed(3)} kHz`} hint={`当前通道 ${activeChannel + 1}`} />
        <MetricCard label="Display Source" value={shortReadout(metric)} hint={`实时值 ${activeSignal?.[metric]?.toFixed(6) ?? "0.000000"} V`} />
      </SimpleGrid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="通道参数"
            description="每个通道维护独立的 demod、osc、input、滤波和相位配置。"
            badge="LabOne-like Module"
          >
            <Tabs value={channelIndex} onChange={(value) => setChannelIndex(value ?? "0")} color="cyan">
              <Tabs.List grow mb="md">
                {data.lockin.channels.map((item, index) => (
                  <Tabs.Tab key={index} value={String(index)}>
                    Channel {index + 1}
                  </Tabs.Tab>
                ))}
              </Tabs.List>
            </Tabs>

            <SimpleGrid cols={{ base: 1, md: 3 }}>
              <NumberInput label="Demod Index" value={form.demod_index} onChange={(value) => setForm((prev) => ({ ...prev, demod_index: Number(value) || 0 }))} />
              <NumberInput label="Osc Index" value={form.osc_index} onChange={(value) => setForm((prev) => ({ ...prev, osc_index: Number(value) || 0 }))} />
              <NumberInput label="Input Index" value={form.input_index} onChange={(value) => setForm((prev) => ({ ...prev, input_index: Number(value) || 0 }))} />
              <NumberInput label="Demod Freq (Hz)" value={form.demod_freq_hz} onChange={(value) => setForm((prev) => ({ ...prev, demod_freq_hz: Number(value) || 0 }))} />
              <NumberInput label="Time Constant (ms)" value={form.time_constant_ms} onChange={(value) => setForm((prev) => ({ ...prev, time_constant_ms: Number(value) || 0 }))} />
              <NumberInput label="Low Pass Order" value={form.low_pass_order} onChange={(value) => setForm((prev) => ({ ...prev, low_pass_order: Number(value) || 1 }))} />
              <NumberInput label="Input Range (mV)" value={form.input_range_mv} onChange={(value) => setForm((prev) => ({ ...prev, input_range_mv: Number(value) || 0 }))} />
              <NumberInput label="Phase (deg)" value={form.phase_deg} onChange={(value) => setForm((prev) => ({ ...prev, phase_deg: Number(value) || 0 }))} />
              <NumberInput label="Sample Rate (Hz)" value={form.sample_rate_hz} onChange={(value) => setForm((prev) => ({ ...prev, sample_rate_hz: Number(value) || 0 }))} />
            </SimpleGrid>

            <Group mt="lg">
              <Button onClick={save}>保存当前通道</Button>
            </Group>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="实时解调曲线"
            description="Plotly 曲线会按当前活动通道持续刷新。"
            badge={`View ${shortReadout(metric)}`}
          >
            <PlotCard
              x={xAxis}
              y={history[metric]}
              yTitle={`${shortReadout(metric)} (V)`}
              xTitle="Sample Index"
              lineColor={metric === "r_v" ? "#59d1ff" : metric === "x_v" ? "#7ef0c1" : "#ffb36f"}
            />

            <SimpleGrid cols={3} mt="md">
              <MetricCard label="X" value={`${activeSignal?.x_v?.toFixed(6) ?? "0.000000"} V`} />
              <MetricCard label="Y" value={`${activeSignal?.y_v?.toFixed(6) ?? "0.000000"} V`} />
              <MetricCard label="R" value={`${activeSignal?.r_v?.toFixed(6) ?? "0.000000"} V`} />
            </SimpleGrid>
          </SectionCard>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
