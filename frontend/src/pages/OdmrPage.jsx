import { useEffect, useState } from "react";
import {
  Button,
  Grid,
  List,
  NumberInput,
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
import { api, formatGHz, shortReadout } from "../lib/api";

export default function OdmrPage() {
  const { data, refresh } = useDashboard(3000);
  const [form, setForm] = useState(null);
  const [trace, setTrace] = useState(null);

  useEffect(() => {
    if (data?.measurement?.last_request && !form) {
      setForm(data.measurement.last_request);
    }
    if (data?.measurement?.last_trace && !trace) {
      setTrace(data.measurement.last_trace);
    }
  }, [data, form, trace]);

  if (!data || !form || !trace) {
    return null;
  }

  const run = async () => {
    try {
      const result = await api.runOdmr(form);
      setTrace(result.data.trace);
      notifications.show({ color: "teal", title: "Sweep complete", message: "ODMR 扫描完成" });
      await refresh();
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Sweep failed",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  };

  const range = trace.frequency_hz?.length
    ? `${formatGHz(trace.frequency_hz[0])} - ${formatGHz(trace.frequency_hz[trace.frequency_hz.length - 1])}`
    : "No trace";

  return (
    <Stack gap="lg">
      <div>
        <Text className="eyebrow">Step 4</Text>
        <Text className="page-title">实时 ODMR 扫描谱</Text>
        <Text c="dimmed" maw={860}>
          你提到的两种方案都保留下来了：软件逐点同步读取，或者微波硬件扫频并把参考电压接入锁相 Aux1 后做线性映射。
        </Text>
      </div>

      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <MetricCard label="Scan Mode" value={form.scan_mode === "software_sync" ? "软件同步" : "Aux1 映射"} hint="ODMR workflow" />
        <MetricCard label="Readout" value={shortReadout(form.readout_source)} hint="锁相读出量" />
        <MetricCard label="Sweep Range" value={range} hint={`${form.points} points`} />
      </SimpleGrid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="扫描配置"
            description="切换扫描方式、读出量和频率范围。Aux1 映射参数只在第二种方式里生效。"
            badge="ODMR Config"
          >
            <SimpleGrid cols={{ base: 1, md: 2 }}>
              <Select
                label="Scan Mode"
                value={form.scan_mode}
                onChange={(value) => setForm((prev) => ({ ...prev, scan_mode: value || "software_sync" }))}
                data={[
                  { value: "software_sync", label: "软件同步" },
                  { value: "aux_map", label: "Aux1 映射" },
                ]}
              />
              <Select
                label="Readout"
                value={form.readout_source}
                onChange={(value) => setForm((prev) => ({ ...prev, readout_source: value || "r_v" }))}
                data={[
                  { value: "r_v", label: "R" },
                  { value: "x_v", label: "X" },
                  { value: "y_v", label: "Y" },
                ]}
              />
              <NumberInput label="Start (Hz)" value={form.start_hz} onChange={(value) => setForm((prev) => ({ ...prev, start_hz: Number(value) || 0 }))} />
              <NumberInput label="Stop (Hz)" value={form.stop_hz} onChange={(value) => setForm((prev) => ({ ...prev, stop_hz: Number(value) || 0 }))} />
              <NumberInput label="Points" value={form.points} onChange={(value) => setForm((prev) => ({ ...prev, points: Number(value) || 3 }))} />
              <NumberInput label="Dwell (ms)" value={form.dwell_ms} onChange={(value) => setForm((prev) => ({ ...prev, dwell_ms: Number(value) || 0 }))} />
              <NumberInput label="Averages" value={form.averages} onChange={(value) => setForm((prev) => ({ ...prev, averages: Number(value) || 1 }))} />
            </SimpleGrid>

            {form.scan_mode === "aux_map" ? (
              <SimpleGrid cols={{ base: 1, md: 2 }} mt="md">
                <NumberInput label="Aux Voltage Min (V)" value={form.aux_voltage_min_v} onChange={(value) => setForm((prev) => ({ ...prev, aux_voltage_min_v: Number(value) || 0 }))} />
                <NumberInput label="Aux Voltage Max (V)" value={form.aux_voltage_max_v} onChange={(value) => setForm((prev) => ({ ...prev, aux_voltage_max_v: Number(value) || 0 }))} />
                <NumberInput label="Mapped Freq Min (Hz)" value={form.aux_frequency_min_hz} onChange={(value) => setForm((prev) => ({ ...prev, aux_frequency_min_hz: Number(value) || 0 }))} />
                <NumberInput label="Mapped Freq Max (Hz)" value={form.aux_frequency_max_hz} onChange={(value) => setForm((prev) => ({ ...prev, aux_frequency_max_hz: Number(value) || 0 }))} />
              </SimpleGrid>
            ) : null}

            <Button mt="lg" onClick={run}>
              开始扫描
            </Button>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="实时扫描谱"
            description="横坐标是微波频率，纵坐标是当前选择的锁相读出量。"
            badge={shortReadout(trace.readout_source)}
          >
            <PlotCard
              x={(trace.frequency_hz || []).map((item) => Number(item) / 1e9)}
              y={trace.intensity || []}
              xTitle="Microwave Frequency (GHz)"
              yTitle={`ODMR ${shortReadout(trace.readout_source)}`}
              lineColor="#64e4c2"
            />
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard
        title="两种实现方式"
        description="界面已经按你的实验逻辑分成两条路径，后面可以逐步接真实设备行为。"
      >
        <List spacing="md" size="sm" c="dimmed">
          <List.Item>
            <Text span fw={700} c="white">
              软件同步：
            </Text>{" "}
            微波源每走一个频点，软件同步触发一次锁相读数，横坐标直接由当前微波频率给出。
          </List.Item>
          <List.Item>
            <Text span fw={700} c="white">
              Aux1 映射：
            </Text>{" "}
            微波源内部扫频并输出参考，锁相 Aux1 采到 0-10V 后再映射到设定频率范围，例如 2.82 GHz 到 2.92 GHz。
          </List.Item>
        </List>
      </SectionCard>
    </Stack>
  );
}
