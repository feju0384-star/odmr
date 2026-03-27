import { useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Code,
  Grid,
  Group,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  NumberInput,
  Select,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconInfoCircle } from "@tabler/icons-react";

import { MetricCard } from "../components/MetricCard";
import { LogPanel } from "../components/LogPanel";
import { SectionCard } from "../components/SectionCard";
import { useDashboard } from "../hooks/useDashboard";
import { api } from "../lib/api";

function DeviceList({ title, items = [], onPick }) {
  return (
    <div className="device-list">
      <Text className="metric-label">{title}</Text>
      <Group mt="sm" gap="sm">
        {items.length ? (
          items.map((item) => (
            <Button
              key={item.serial}
              variant="light"
              color="gray"
              radius="xl"
              onClick={() => onPick(item.serial)}
            >
              {item.label}
            </Button>
          ))
        ) : (
          <Text c="dimmed" size="sm">
            暂无数据
          </Text>
        )}
      </Group>
    </div>
  );
}

export default function DevicePage() {
  const { data, error, refresh } = useDashboard(3000);
  const [serverHost, setServerHost] = useState("localhost");
  const [serverPort, setServerPort] = useState(8004);
  const [hf2, setHf2] = useState("false");
  const [serial, setSerial] = useState("dev1234");
  const [iface, setIface] = useState("");
  const [address, setAddress] = useState("TCPIP0::192.168.1.100::inst0::INSTR");
  const [timeoutMs, setTimeoutMs] = useState(3000);
  const [discoverResult, setDiscoverResult] = useState({ visible: [], connected: [] });
  const [resources, setResources] = useState([]);

  const capabilityBadges = useMemo(() => {
    if (!data) {
      return [];
    }
    return [
      data.capabilities.zhinst_toolkit ? "zhinst-toolkit ready" : "zhinst-toolkit missing",
      data.capabilities.pyvisa ? "PyVISA ready" : "PyVISA missing",
    ];
  }, [data]);

  const act = async (runner, success) => {
    try {
      await runner();
      notifications.show({ color: "teal", title: "Success", message: success });
      await refresh();
    } catch (err) {
      notifications.show({
        color: "red",
        title: "Request failed",
        message: err instanceof Error ? err.message : "Unknown error",
      });
    }
  };

  if (!data) {
    return null;
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start">
        <div>
          <Text className="eyebrow">Step 1</Text>
          <Text className="page-title">设备发现与连接</Text>
          <Text c="dimmed" maw={860}>
            用 LabOne Data Server 发现锁相设备，用 PyVISA 发现微波源资源。这个页面只负责连接，不混入参数配置。
          </Text>
        </div>
        <Group gap="xs">
          {capabilityBadges.map((item) => (
            <Badge key={item} variant="light" color="cyan">
              {item}
            </Badge>
          ))}
        </Group>
      </Group>

      {error ? (
        <Alert variant="light" color="red" icon={<IconInfoCircle size={18} />}>
          {error}
        </Alert>
      ) : null}

      <SimpleGrid cols={{ base: 1, md: 3 }}>
        <MetricCard
          label="LabOne Server"
          value={`${data.lockin.server_host}:${data.lockin.server_port}`}
          hint="锁相设备发现入口"
        />
        <MetricCard
          label="Lock-in"
          value={data.lockin.connected ? data.lockin.serial : "未连接"}
          hint={data.lockin.connected ? data.lockin.name : "等待连接"}
        />
        <MetricCard
          label="Microwave"
          value={data.microwave.connected ? data.microwave.address : "未连接"}
          hint={data.microwave.connected ? data.microwave.idn || "Connected" : "等待连接"}
        />
      </SimpleGrid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="锁相设备"
            description="先设置 Data Server 地址，再扫描可见设备。选中序列号后可直接连接。"
            badge="LabOne Discovery"
          >
            <SimpleGrid cols={{ base: 1, md: 3 }}>
              <TextInput label="Server Host" value={serverHost} onChange={(e) => setServerHost(e.currentTarget.value)} />
              <NumberInput label="Server Port" value={serverPort} onChange={(value) => setServerPort(Number(value) || 8004)} />
              <Select
                label="HF2 Server"
                value={hf2}
                onChange={(value) => setHf2(value ?? "false")}
                data={[
                  { value: "false", label: "false" },
                  { value: "true", label: "true" },
                ]}
              />
            </SimpleGrid>

            <Group mt="md">
              <Button
                onClick={() =>
                  act(async () => {
                    const result = await api.discoverLockins({
                      server_host: serverHost,
                      server_port: serverPort,
                      hf2: hf2 === "true",
                    });
                    setDiscoverResult(result.data);
                  }, "锁相设备扫描完成")
                }
              >
                扫描锁相设备
              </Button>
            </Group>

            <Stack mt="lg" gap="lg">
              <DeviceList title="Visible Devices" items={discoverResult.visible} onPick={setSerial} />
              <DeviceList title="Connected Devices" items={discoverResult.connected} onPick={setSerial} />
            </Stack>

            <SimpleGrid cols={{ base: 1, md: 2 }} mt="lg">
              <TextInput label="Serial" value={serial} onChange={(e) => setSerial(e.currentTarget.value)} />
              <TextInput label="Interface" value={iface} onChange={(e) => setIface(e.currentTarget.value)} placeholder="1GbE / USB" />
            </SimpleGrid>

            <Group mt="md">
              <Button
                variant="gradient"
                gradient={{ from: "cyan", to: "teal" }}
                onClick={() =>
                  act(
                    () =>
                      api.connectLockin({
                        server_host: serverHost,
                        server_port: serverPort,
                        hf2: hf2 === "true",
                        serial,
                        interface: iface || null,
                      }),
                    `已连接锁相设备 ${serial}`,
                  )
                }
              >
                连接锁相
              </Button>
            </Group>
          </SectionCard>
        </Grid.Col>

        <Grid.Col span={{ base: 12, xl: 5 }}>
          <SectionCard
            title="微波源"
            description="扫描 VISA 资源，选择地址后建立连接。"
            badge="PyVISA"
          >
            <Group>
              <Button
                onClick={() =>
                  act(async () => {
                    const result = await api.discoverMicrowaves();
                    setResources(result.data.resources || []);
                  }, "VISA 资源扫描完成")
                }
              >
                扫描 VISA 资源
              </Button>
            </Group>

            <Group mt="lg" gap="sm">
              {resources.length ? (
                resources.map((item) => (
                  <Button key={item} variant="light" color="gray" radius="xl" onClick={() => setAddress(item)}>
                    {item}
                  </Button>
                ))
              ) : (
                <Text c="dimmed" size="sm">
                  暂无资源
                </Text>
              )}
            </Group>

            <Stack mt="lg">
              <TextInput label="VISA Address" value={address} onChange={(e) => setAddress(e.currentTarget.value)} />
              <NumberInput label="Timeout (ms)" value={timeoutMs} onChange={(value) => setTimeoutMs(Number(value) || 3000)} />
            </Stack>

            <Group mt="md">
              <Button
                onClick={() =>
                  act(
                    () => api.connectMicrowave({ address, timeout_ms: timeoutMs }),
                    `已连接微波源 ${address}`,
                  )
                }
              >
                连接微波源
              </Button>
            </Group>

            <Alert variant="light" color="cyan" mt="lg" icon={<IconInfoCircle size={18} />}>
              后端当前已接入设备发现、连接和状态保存。真实 SCPI 细节还需要按你的具体型号进一步收口。
            </Alert>
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard title="系统日志" description="设备发现、连接、报错和状态变更统一记录。">
        <LogPanel logs={data.logs} />
        <Text c="dimmed" size="sm" mt="md">
          当前后端入口：<Code>/api/instruments/dashboard</Code>
        </Text>
      </SectionCard>
    </Stack>
  );
}
