import { useEffect, useState } from "react";
import {
  Button,
  Grid,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
  TextInput,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { MetricCard } from "../components/MetricCard";
import { SectionCard } from "../components/SectionCard";
import { useDashboard } from "../hooks/useDashboard";
import { api, formatGHz } from "../lib/api";

export default function MicrowavePage() {
  const { data, refresh } = useDashboard(3000);
  const [form, setForm] = useState(null);

  useEffect(() => {
    if (data?.microwave?.config) {
      setForm(data.microwave.config);
    }
  }, [data]);

  if (!data || !form) {
    return null;
  }

  const save = async () => {
    try {
      await api.saveMicrowave(form);
      notifications.show({ color: "teal", title: "Saved", message: "微波参数已保存" });
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
      <div>
        <Text className="eyebrow">Step 3</Text>
        <Text className="page-title">微波源模式与调制设置</Text>
        <Text c="dimmed" maw={860}>
          这一页覆盖定频、硬件扫频和 FM 调制三组参数，适合接你后面的真实微波源控制逻辑。
        </Text>
      </div>

      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <MetricCard label="Address" value={data.microwave.connected ? data.microwave.address : "未连接"} hint={data.microwave.idn || "等待连接"} />
        <MetricCard label="Mode" value={String(form.mode).toUpperCase()} hint={form.output_enabled ? "RF output on" : "RF output off"} />
        <MetricCard label="Carrier" value={formatGHz(form.mode === "cw" ? form.frequency_hz : form.center_frequency_hz)} hint={`${Number(form.power_dbm).toFixed(1)} dBm`} />
      </SimpleGrid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="基础模式"
            description="选择微波源工作在定频还是扫频。硬件扫频参数会被 ODMR 页面直接复用。"
            badge="CW / Sweep"
          >
            <SimpleGrid cols={{ base: 1, md: 3 }}>
              <Select
                label="Mode"
                value={form.mode}
                onChange={(value) => setForm((prev) => ({ ...prev, mode: value || "cw" }))}
                data={[
                  { value: "cw", label: "CW" },
                  { value: "sweep", label: "Sweep" },
                ]}
              />
              <NumberInput label="Frequency (Hz)" value={form.frequency_hz} onChange={(value) => setForm((prev) => ({ ...prev, frequency_hz: Number(value) || 0 }))} />
              <NumberInput label="Center Frequency (Hz)" value={form.center_frequency_hz} onChange={(value) => setForm((prev) => ({ ...prev, center_frequency_hz: Number(value) || 0 }))} />
              <NumberInput label="Power (dBm)" value={form.power_dbm} onChange={(value) => setForm((prev) => ({ ...prev, power_dbm: Number(value) || 0 }))} />
              <NumberInput label="Sweep Start (Hz)" value={form.sweep_start_hz} onChange={(value) => setForm((prev) => ({ ...prev, sweep_start_hz: Number(value) || 0 }))} />
              <NumberInput label="Sweep Stop (Hz)" value={form.sweep_stop_hz} onChange={(value) => setForm((prev) => ({ ...prev, sweep_stop_hz: Number(value) || 0 }))} />
              <NumberInput label="Sweep Points" value={form.sweep_points} onChange={(value) => setForm((prev) => ({ ...prev, sweep_points: Number(value) || 2 }))} />
              <NumberInput label="Dwell (ms)" value={form.dwell_ms} onChange={(value) => setForm((prev) => ({ ...prev, dwell_ms: Number(value) || 0 }))} />
            </SimpleGrid>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="输出与 FM"
            description="在这里统一管理 RF、IQ 和 FM 调制。"
            badge="Modulation"
          >
            <Stack gap="md">
              <TextInput label="Connected Address" value={data.microwave.address || "未连接"} readOnly />
              <SimpleGrid cols={2}>
                <Switch checked={form.output_enabled} onChange={(event) => setForm((prev) => ({ ...prev, output_enabled: event.currentTarget.checked }))} label="RF Output" />
                <Switch checked={form.iq_enabled} onChange={(event) => setForm((prev) => ({ ...prev, iq_enabled: event.currentTarget.checked }))} label="IQ Enable" />
                <Switch checked={form.fm_enabled} onChange={(event) => setForm((prev) => ({ ...prev, fm_enabled: event.currentTarget.checked }))} label="FM Enable" />
              </SimpleGrid>
              <Select
                label="FM Source"
                value={form.fm_source}
                onChange={(value) => setForm((prev) => ({ ...prev, fm_source: value || "internal" }))}
                data={[
                  { value: "internal", label: "Internal" },
                  { value: "external", label: "External" },
                ]}
              />
              <NumberInput label="FM Deviation (Hz)" value={form.fm_deviation_hz} onChange={(value) => setForm((prev) => ({ ...prev, fm_deviation_hz: Number(value) || 0 }))} />
              <NumberInput label="FM Rate (Hz)" value={form.fm_rate_hz} onChange={(value) => setForm((prev) => ({ ...prev, fm_rate_hz: Number(value) || 0 }))} />
            </Stack>
            <Button mt="lg" onClick={save}>
              保存微波参数
            </Button>
          </SectionCard>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
