import { useEffect, useRef, useState } from "react";
import {
  Button,
  Grid,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Switch,
  Text,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";

import { MetricCard } from "../components/MetricCard";
import { SectionCard } from "../components/SectionCard";
import { useDashboard } from "../hooks/useDashboard";
import { api, formatGHz } from "../lib/api";

const LF_OUTPUT_SOURCE_OPTIONS = [
  { value: "monitor", label: "跟随 FM 内部函数" },
  { value: "function1", label: "独立 Function 1" },
  { value: "dc", label: "DC" },
];

const LF_OUTPUT_LOAD_OPTIONS = [
  { value: "50", label: "50 Ohm" },
  { value: "600", label: "600 Ohm" },
  { value: "1000000", label: "1 MOhm" },
];

export default function MicrowavePage() {
  const { data, refresh, error, loading } = useDashboard(2000);
  const [form, setForm] = useState(null);
  const [isDirty, setIsDirty] = useState(false);
  const hasHydratedRef = useRef(false);

  useEffect(() => {
    if (!data?.microwave?.config) {
      return;
    }
    if (!hasHydratedRef.current || !isDirty) {
      setForm(data.microwave.config);
      setIsDirty(false);
      hasHydratedRef.current = true;
    }
  }, [data, isDirty]);

  const updateForm = (changes) => {
    setIsDirty(true);
    setForm((prev) => ({ ...prev, ...changes }));
  };

  const save = async (nextForm = form, successMessage = "微波参数已保存") => {
    try {
      await api.saveMicrowave(nextForm);
      notifications.show({ color: "teal", title: "保存成功", message: successMessage });
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
    if (!data?.microwave?.config) {
      return;
    }
    setForm(data.microwave.config);
    setIsDirty(false);
  };

  const reloadFromDevice = async () => {
    setIsDirty(false);
    await refresh();
    notifications.show({
      color: "teal",
      title: "已重载",
      message: "微波页参数已从后端状态重新同步",
    });
  };

  const setRfOutput = async (enabled) => {
    const nextForm = { ...form, output_enabled: enabled };
    setForm(nextForm);
    await save(nextForm, enabled ? "RF 输出已打开" : "RF 输出已关闭");
  };

  const applyAuxReferencePreset = () => {
    updateForm({
      fm_enabled: true,
      fm_source: "external",
      fm_rate_hz: 10000,
      lf_output_enabled: true,
      lf_output_source: "function1",
      lf_output_amplitude_v: Number(form.lf_output_amplitude_v ?? 1) || 1,
      lf_output_offset_v: 0,
      lf_output_load_ohm: 1000000,
    });
    notifications.show({
      color: "teal",
      title: "已填入实验预设",
      message: "已设置为 FM Source = Ext1，并从 LF OUT 输出 Function1 10 kHz 调制信号",
    });
  };

  if (!data || !form) {
    return (
      <Stack gap="md">
        <Text className="page-title">微波源模式与调制设置</Text>
        <Text c="dimmed">{error || (loading ? "正在加载微波源数据..." : "微波源数据为空")}</Text>
      </Stack>
    );
  }

  return (
    <Stack gap="lg">
      <div>
        <Text className="eyebrow">Step 3</Text>
        <Text className="page-title">微波源模式与调制设置</Text>
        <Text c="dimmed" maw={860}>
          这里补上了更适合实验操作的按钮：应用当前配置、撤销修改、RF 打开和 RF 关闭。ODMR 页面也可以直接复用这里的扫频参数。
        </Text>
      </div>

      <SimpleGrid cols={{ base: 1, md: 4 }}>
        <MetricCard
          label="微波地址"
          value={data.microwave.connected ? data.microwave.address : "未连接"}
          hint={data.microwave.idn || "等待连接"}
        />
        <MetricCard
          label="工作模式"
          value={form.mode === "cw" ? "定频" : "扫频"}
          hint={form.output_enabled ? "RF 输出开启" : "RF 输出关闭"}
        />
        <MetricCard
          label="载波频率"
          value={formatGHz(form.mode === "cw" ? form.frequency_hz : form.center_frequency_hz)}
          hint={`${Number(form.power_dbm).toFixed(1)} dBm`}
        />
        <MetricCard
          label="扫频范围"
          value={`${formatGHz(form.sweep_start_hz)} - ${formatGHz(form.sweep_stop_hz)}`}
          hint={`${form.sweep_points} points | ${Number(form.dwell_ms).toFixed(1)} ms`}
        />
        <MetricCard
          label="LF OUT"
          value={form.lf_output_enabled ? "已开启" : "未开启"}
          hint={form.lf_output_source === "monitor" ? "跟随 FM Function1" : form.lf_output_source === "function1" ? "独立 Function1" : "DC"}
        />
      </SimpleGrid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="基础模式"
            description="支持定频和扫频两种模式。扫频配置会被 ODMR 页面直接复用。"
            badge={isDirty ? "有未保存修改" : "已同步"}
          >
            <SimpleGrid cols={{ base: 1, md: 2 }}>
              <Select
                label="模式"
                value={form.mode}
                onChange={(value) => updateForm({ mode: value || "cw" })}
                data={[
                  { value: "cw", label: "定频" },
                  { value: "sweep", label: "扫频" },
                ]}
              />
              <NumberInput label="功率 (dBm)" value={form.power_dbm} onChange={(value) => updateForm({ power_dbm: Number(value) || 0 })} />
              <NumberInput label="定频频率 (Hz)" value={form.frequency_hz} onChange={(value) => updateForm({ frequency_hz: Number(value) || 0 })} />
              <NumberInput label="中心频率 (Hz)" value={form.center_frequency_hz} onChange={(value) => updateForm({ center_frequency_hz: Number(value) || 0 })} />
              <NumberInput label="扫频起点 (Hz)" value={form.sweep_start_hz} onChange={(value) => updateForm({ sweep_start_hz: Number(value) || 0 })} />
              <NumberInput label="扫频终点 (Hz)" value={form.sweep_stop_hz} onChange={(value) => updateForm({ sweep_stop_hz: Number(value) || 0 })} />
              <NumberInput label="扫频点数" value={form.sweep_points} onChange={(value) => updateForm({ sweep_points: Number(value) || 2 })} />
              <NumberInput label="驻留时间 (ms)" value={form.dwell_ms} onChange={(value) => updateForm({ dwell_ms: Number(value) || 0 })} />
            </SimpleGrid>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="输出与调制"
            description="统一管理 RF、IQ、FM 和 LF OUT。要把 10 kHz 同时送去锁相 Aux In 1 并用于 FM，推荐用“跟随 FM 内部函数”。"
            badge="输出控制"
          >
            <Stack gap="md">
              <SimpleGrid cols={2}>
                <Switch checked={form.output_enabled} onChange={(event) => updateForm({ output_enabled: event.currentTarget.checked })} label="RF 输出" />
                <Switch checked={form.iq_enabled} onChange={(event) => updateForm({ iq_enabled: event.currentTarget.checked })} label="IQ 输出" />
                <Switch checked={form.fm_enabled} onChange={(event) => updateForm({ fm_enabled: event.currentTarget.checked })} label="FM 调制" />
                <Switch checked={form.lf_output_enabled} onChange={(event) => updateForm({ lf_output_enabled: event.currentTarget.checked })} label="LF OUT 输出" />
              </SimpleGrid>

              <Select
                label="FM 源"
                value={form.fm_source}
                onChange={(value) => updateForm({ fm_source: value || "external" })}
                data={[
                  { value: "internal", label: "内部" },
                  { value: "external", label: "外部" },
                ]}
              />
              <NumberInput
                label="FM 偏移 (Hz)"
                value={form.fm_deviation_hz}
                onChange={(value) => updateForm({ fm_deviation_hz: Number(value) || 0 })}
                disabled={!form.fm_enabled}
              />
              <NumberInput
                label="FM 速率 (Hz)"
                value={form.fm_rate_hz}
                onChange={(value) => updateForm({ fm_rate_hz: Number(value) || 0 })}
                disabled={!form.fm_enabled}
              />
              <Select
                label="LF OUT 源"
                value={form.lf_output_source}
                onChange={(value) => updateForm({ lf_output_source: value || "monitor" })}
                data={LF_OUTPUT_SOURCE_OPTIONS}
                disabled={!form.lf_output_enabled}
              />
              <NumberInput
                label="LF OUT 幅度 (V)"
                value={form.lf_output_amplitude_v}
                onChange={(value) => updateForm({ lf_output_amplitude_v: Number(value) || 0 })}
                disabled={!form.lf_output_enabled}
              />
              <NumberInput
                label="LF OUT 偏置 (V)"
                value={form.lf_output_offset_v}
                onChange={(value) => updateForm({ lf_output_offset_v: Number(value) || 0 })}
                disabled={!form.lf_output_enabled}
              />
              <Select
                label="LF OUT 负载"
                value={String(form.lf_output_load_ohm ?? 1000000)}
                onChange={(value) => updateForm({ lf_output_load_ohm: Number(value) || 1000000 })}
                data={LF_OUTPUT_LOAD_OPTIONS}
                disabled={!form.lf_output_enabled}
              />

              <Button variant="light" color="cyan" onClick={applyAuxReferencePreset}>
                一键设置 10 kHz FM + Aux1 参考输出
              </Button>

              <Group mt="sm">
                <Button onClick={() => save()}>应用当前配置</Button>
                <Button variant="light" color="gray" onClick={revertChanges} disabled={!isDirty}>
                  撤销修改
                </Button>
                <Button variant="light" color="cyan" onClick={reloadFromDevice}>
                  从设备重载
                </Button>
              </Group>
              <Group>
                <Button variant="light" color="teal" onClick={() => setRfOutput(true)} disabled={form.output_enabled}>
                  RF 打开
                </Button>
                <Button variant="light" color="red" onClick={() => setRfOutput(false)} disabled={!form.output_enabled}>
                  RF 关闭
                </Button>
              </Group>
            </Stack>
          </SectionCard>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}
