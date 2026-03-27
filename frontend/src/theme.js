import { createTheme } from "@mantine/core";

export const theme = createTheme({
  primaryColor: "cyan",
  fontFamily: "Manrope, Segoe UI, sans-serif",
  fontFamilyMonospace: "IBM Plex Mono, Consolas, monospace",
  headings: {
    fontFamily: "Manrope, Segoe UI, sans-serif",
    fontWeight: "800",
  },
  colors: {
    midnight: [
      "#eaf4ff",
      "#c9dbf6",
      "#a8c2ea",
      "#88a9de",
      "#6a92d4",
      "#5684ce",
      "#497ccf",
      "#3966b7",
      "#2d5aa4",
      "#1e4d93",
    ],
  },
  defaultRadius: "xl",
  components: {
    Button: {
      defaultProps: {
        size: "md",
        radius: "xl",
      },
    },
    TextInput: {
      defaultProps: {
        size: "md",
        radius: "xl",
      },
    },
    NumberInput: {
      defaultProps: {
        size: "md",
        radius: "xl",
      },
    },
    Select: {
      defaultProps: {
        size: "md",
        radius: "xl",
      },
    },
    Badge: {
      defaultProps: {
        radius: "xl",
      },
    },
    Tabs: {
      defaultProps: {
        radius: "xl",
      },
    },
  },
});
