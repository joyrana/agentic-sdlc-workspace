import { makeStyles, tokens } from "@fluentui/react-components";

const useStyles = makeStyles({
  pre: {
    fontFamily: tokens.fontFamilyMonospace,
    fontSize: tokens.fontSizeBase200,
    lineHeight: tokens.lineHeightBase200,
    margin: 0,
    padding: tokens.spacingHorizontalM,
    overflowX: "auto",
    maxHeight: "480px",
    backgroundColor: tokens.colorNeutralBackground3,
    borderRadius: tokens.borderRadiusMedium,
  },
  add: { backgroundColor: tokens.colorPaletteGreenBackground1, color: tokens.colorPaletteGreenForeground1 },
  del: { backgroundColor: tokens.colorPaletteRedBackground1, color: tokens.colorPaletteRedForeground1 },
  hunk: { color: tokens.colorBrandForeground1 },
  file: { fontWeight: tokens.fontWeightSemibold },
});

export function Diff({ text }: { text: string }) {
  const s = useStyles();
  return (
    <pre className={s.pre} aria-label="Code diff">
      {text.split("\n").map((line, i) => {
        const cls = line.startsWith("diff --git")
          ? s.file
          : line.startsWith("@@")
            ? s.hunk
            : line.startsWith("+") && !line.startsWith("+++")
              ? s.add
              : line.startsWith("-") && !line.startsWith("---")
                ? s.del
                : undefined;
        return (
          <div key={i} className={cls}>
            {line || " "}
          </div>
        );
      })}
    </pre>
  );
}

export function LogView({ text }: { text: string }) {
  const s = useStyles();
  return (
    <pre className={s.pre} aria-label="Log output">
      {text}
    </pre>
  );
}
