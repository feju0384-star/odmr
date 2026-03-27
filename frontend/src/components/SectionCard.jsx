import { Badge, Group, Paper, Stack, Text, Title } from "@mantine/core";

export function SectionCard({ title, description, badge, children, minHeight }) {
  return (
    <Paper className="glass-panel section-card" p="lg" radius="xl" style={{ minHeight }}>
      <Group justify="space-between" align="flex-start" mb="md">
        <Stack gap={4}>
          <Title order={3}>{title}</Title>
          {description ? (
            <Text c="dimmed" maw={720}>
              {description}
            </Text>
          ) : null}
        </Stack>
        {badge ? (
          <Badge variant="light" color="cyan">
            {badge}
          </Badge>
        ) : null}
      </Group>
      {children}
    </Paper>
  );
}
