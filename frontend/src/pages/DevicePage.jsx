import { useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Code,
  Grid,
  Group,
  NumberInput,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Select,
} from "@mantine/core";
import { notifications } from "@mantine/notifications";
import { IconInfoCircle } from "@tabler/icons-react";

import { LogPanel } from "../components/LogPanel";
import { MetricCard } from "../components/MetricCard";
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
  const [serial, setSerial] = useState("dev6520");
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

  const act = async (runner, successMessage) => {
    try {
      await runner();
      notifications.show({ color: "teal", title: "操作成功", message: successMessage });
      await refresh();
    } catch (err) {
      notifications.show({
        color: "red",
        title: "请求失败",
        message: err instanceof Error ? err.message : "未知错误",
      });
    }
  };

  if (!data) {
    return (
      <Stack gap="md">
        <Text className="page-title">设备发现与连接</Text>
        <Text c="dimmed">{error || "正在加载设备状态..."}</Text>
      </Stack>
    );
  }

  return (
    <Stack gap="lg">
      <Group justify="space-between" align="flex-start">
        <div>
          <Text className="eyebrow">Step 1</Text>
          <Text className="page-title">设备发现与连接</Text>
          <Text c="dimmed" maw={860}>
            先在这里完成设备发现、连接和断开。锁相与微波的详细参数分别放在后续独立页面，避免实验页里混入连接管理。
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
          label="锁相设备"
          value={data.lockin.connected ? data.lockin.serial : "未连接"}
          hint={data.lockin.connected ? data.lockin.name : "等待连接"}
        />
        <MetricCard
          label="微波源"
          value={data.microwave.connected ? data.microwave.address : "未连接"}
          hint={data.microwave.connected ? data.microwave.idn || "Connected" : "等待连接"}
        />
      </SimpleGrid>

      <Grid gutter="lg">
        <Grid.Col span={{ base: 12, xl: 7 }}>
          <SectionCard
            title="锁相设备"
            description="先配置 Data Server，再扫描可见设备。选中序列号后可以连接或断开。"
            badge="LabOne Discovery"
          >
            <SimpleGrid cols={{ base: 1, md: 3 }}>
              <TextInput
                label="Server Host"
                value={serverHost}
                onChange={(event) => setServerHost(event.currentTarget.value)}
              />
              <NumberInput
                label="Server Port"
                value={serverPort}
                onChange={(value) => setServerPort(Number(value) || 8004)}
              />
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
              {data.lockin.connected ? (
                <Button color="red" variant="light" onClick={() => act(() => api.disconnectLockin(), "锁相设备已断开")}>
                  断开锁相
                </Button>
              ) : null}
            </Group>

            <Stack mt="lg" gap="lg">
              <DeviceList title="Visible Devices" items={discoverResult.visible} onPick={setSerial} />
              <DeviceList title="Connected Devices" items={discoverResult.connected} onPick={setSerial} />
            </Stack>

            <SimpleGrid cols={{ base: 1, md: 2 }} mt="lg">
              <TextInput label="Serial" value={serial} onChange={(event) => setSerial(event.currentTarget.value)} />
              <TextInput
                label="Interface"
                value={iface}
                onChange={(event) => setIface(event.currentTarget.value)}
                placeholder="1GbE / USB"
              />
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
            description="先扫描 VISA 资源，再选地址连接。连接后可以直接在这里断开，不必回命令行处理。"
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
              {data.microwave.connected ? (
                <Button color="red" variant="light" onClick={() => act(() => api.disconnectMicrowave(), "微波源已断开")}>
                  断开微波源
                </Button>
              ) : null}
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
              <TextInput label="VISA Address" value={address} onChange={(event) => setAddress(event.currentTarget.value)} />
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
              连接成功后，锁相页和微波页会直接读取这里的连接状态。当前后端入口是 <Code>/api/instruments/dashboard</Code>。
            </Alert>
          </SectionCard>
        </Grid.Col>
      </Grid>

      <SectionCard title="系统日志" description="设备发现、连接、断开与报错统一记录。">
        <LogPanel logs={data.logs} />
      </SectionCard>
    </Stack>
  );
}
