import {
  AppShell,
  Badge,
  Box,
  Burger,
  Divider,
  Group,
  Stack,
  Text,
  ThemeIcon,
  Title,
  UnstyledButton,
} from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import {
  IconAdjustmentsBolt,
  IconAntennaBars5,
  IconChartLine,
  IconCpu,
} from "@tabler/icons-react";
import { useLocation, useNavigate } from "react-router-dom";

const navItems = [
  { to: "/device", label: "设备连接", subtitle: "发现并连接仪器", icon: IconCpu },
  { to: "/lockin", label: "锁相设置", subtitle: "通道参数和实时曲线", icon: IconAdjustmentsBolt },
  { to: "/microwave", label: "微波设置", subtitle: "CW / Sweep / FM", icon: IconAntennaBars5 },
  { to: "/odmr", label: "ODMR 扫描", subtitle: "两种扫描方式", icon: IconChartLine },
];

function Sidebar({ onNavigate }) {
  const location = useLocation();
  const navigate = useNavigate();

  return (
    <Stack gap="lg" h="100%">
      <Box className="glass-panel brand-panel">
        <Text className="eyebrow">NV Measurement Suite</Text>
        <Title order={2} mt={8}>
          LabOne-Inspired Console
        </Title>
        <Text c="dimmed" mt="sm" size="sm">
          基于 React、Mantine 和 Plotly 的仪器前端，按实验工作流拆成独立页面。
        </Text>
      </Box>

      <Stack gap="sm">
        {navItems.map((item) => {
          const active = location.pathname === item.to;
          return (
            <UnstyledButton
              key={item.to}
              className={`shell-nav-item ${active ? "is-active" : ""}`}
              onClick={() => {
                navigate(item.to);
                onNavigate?.();
              }}
            >
              <Group align="center" gap="md" wrap="nowrap">
                <ThemeIcon
                  size={42}
                  radius="xl"
                  variant={active ? "filled" : "light"}
                  color={active ? "cyan" : "gray"}
                >
                  <item.icon size={20} stroke={1.8} />
                </ThemeIcon>
                <div className="nav-copy">
                  <Text className="nav-title">{item.label}</Text>
                  <Text className="nav-subtitle">{item.subtitle}</Text>
                </div>
              </Group>
            </UnstyledButton>
          );
        })}
      </Stack>

      <Divider color="rgba(122,167,255,0.16)" />

      <Box className="glass-panel tip-panel">
        <Group justify="space-between" align="flex-start" wrap="nowrap">
          <div>
            <Text fw={700} size="sm">
              Workflow
            </Text>
            <Text c="dimmed" mt={6} size="sm">
              先连接设备，再配置锁相和微波参数，最后执行 ODMR。
            </Text>
          </div>
          <Badge variant="light" color="cyan" size="lg">
            4 Pages
          </Badge>
        </Group>
      </Box>
    </Stack>
  );
}

export function AppFrame({ children }) {
  const [opened, { toggle, close }] = useDisclosure();

  return (
    <AppShell
      header={{ height: 74 }}
      navbar={{ width: 340, breakpoint: "md", collapsed: { mobile: !opened } }}
      padding="xl"
    >
      <AppShell.Header className="topbar">
        <Group justify="space-between" h="100%" px="xl" wrap="nowrap">
          <Group gap="md" wrap="nowrap">
            <Burger opened={opened} onClick={toggle} hiddenFrom="md" size="sm" />
            <div>
              <Text className="eyebrow">Quantum Control Frontend</Text>
              <Title order={3}>NV Measurement Console</Title>
            </div>
          </Group>
          <Badge variant="gradient" gradient={{ from: "cyan", to: "teal" }} size="lg">
            Vite + React + Mantine + Plotly
          </Badge>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar className="sidebar-shell" p="md">
        <Sidebar onNavigate={close} />
      </AppShell.Navbar>

      <AppShell.Main>
        <Box className="page-shell">{children}</Box>
      </AppShell.Main>
    </AppShell>
  );
}
