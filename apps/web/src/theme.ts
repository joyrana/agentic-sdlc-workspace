import {
  createDarkTheme,
  createLightTheme,
  type BrandVariants,
  type Theme,
} from "@fluentui/react-components";

/**
 * Brand ramp built around "signal teal" (#1F6F78, step 80). Used through Fluent tokens only;
 * components never hardcode colors.
 */
export const brand: BrandVariants = {
  10: "#020707",
  20: "#0B1C1E",
  30: "#0D2E32",
  40: "#0E3B40",
  50: "#10484E",
  60: "#13565D",
  70: "#18636B",
  80: "#1F6F78",
  90: "#2E7D86",
  100: "#418A93",
  110: "#5598A0",
  120: "#6AA6AD",
  130: "#80B4BA",
  140: "#97C2C7",
  150: "#AED0D4",
  160: "#C6DFE2",
};

export const lightTheme: Theme = createLightTheme(brand);
export const darkTheme: Theme = {
  ...createDarkTheme(brand),
  colorBrandForeground1: brand[120],
  colorBrandForeground2: brand[130],
};
