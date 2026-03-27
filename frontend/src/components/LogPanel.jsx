import { ScrollArea, Stack, Text } from "@mantine/core";

export function LogPanel({ logs = [] }) {
  return (
    <ScrollArea h={320} offsetScrollbars>
      <Stack gap="sm">
        {logs.map((item, index) => (
          <div key={`${item.timestamp}-${index}`} className="log-item">
            <Text fw={600} size="sm">
              {item.timestamp}
            </Text>
            <Text c="dimmed" size="sm" mt={4}>
              {item.message}
            </Text>
          </div>
        ))}
      </Stack>
    </ScrollArea>
  );
}
