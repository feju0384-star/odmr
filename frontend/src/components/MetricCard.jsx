import { Paper, Text, Title } from "@mantine/core";

export function MetricCard({ label, value, hint }) {
  return (
    <Paper className="glass-panel metric-card" p="lg" radius="xl">
      <Text className="metric-label">{label}</Text>
      <Title order={2} mt="md">
        {value}
      </Title>
      {hint ? (
        <Text c="dimmed" size="sm" mt="xs">
          {hint}
        </Text>
      ) : null}
    </Paper>
  );
}
