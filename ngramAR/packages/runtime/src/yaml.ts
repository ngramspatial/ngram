// ─── Minimal YAML Parser ────────────────────────────────────────────────────
// Handles: scalars, quoted strings, nested objects (via indentation),
// arrays (`- item`), multiline literal blocks (`|`), and folded blocks (`>`).

export type YamlValue = string | number | boolean | null | YamlValue[] | YamlMap;
export interface YamlMap {
  [key: string]: YamlValue;
}

interface Line {
  indent: number;
  raw: string;
  text: string;
}

function tokenize(source: string): Line[] {
  const lines: Line[] = [];
  for (const raw of source.split("\n")) {
    const stripped = raw.replace(/\r$/, "");
    if (stripped.trim() === "" || stripped.trimStart().startsWith("#")) continue;
    const indent = stripped.length - stripped.trimStart().length;
    lines.push({ indent, raw: stripped, text: stripped.trimStart() });
  }
  return lines;
}

function parseScalar(value: string): string | number | boolean | null {
  if (value === "null" || value === "~") return null;
  if (value === "true") return true;
  if (value === "false") return false;
  if (/^-?\d+(\.\d+)?$/.test(value)) return Number(value);
  if (
    (value.startsWith('"') && value.endsWith('"')) ||
    (value.startsWith("'") && value.endsWith("'"))
  ) {
    return value.slice(1, -1);
  }
  return value;
}

function parseBlock(lines: Line[], start: number, baseIndent: number): [YamlMap, number] {
  const result: YamlMap = {};
  let i = start;

  while (i < lines.length && lines[i].indent >= baseIndent) {
    const line = lines[i];
    if (line.indent < baseIndent) break;
    if (line.indent > baseIndent) {
      i++;
      continue;
    }

    if (line.text.startsWith("- ")) {
      i++;
      continue;
    }

    const colonIdx = line.text.indexOf(":");
    if (colonIdx === -1) {
      i++;
      continue;
    }

    const key = line.text.slice(0, colonIdx).trim();
    const afterColon = line.text.slice(colonIdx + 1).trim();

    if (afterColon === "|" || afterColon === ">") {
      const fold = afterColon === ">";
      i++;
      const blockLines: string[] = [];
      while (i < lines.length && lines[i].indent > baseIndent) {
        blockLines.push(lines[i].raw.slice(baseIndent + 2));
        i++;
      }
      result[key] = fold
        ? blockLines.join(" ").replace(/\s+/g, " ").trim()
        : blockLines.join("\n");
    } else if (afterColon === "") {
      i++;
      if (i < lines.length && lines[i].indent > baseIndent) {
        const childIndent = lines[i].indent;
        if (lines[i].text.startsWith("- ")) {
          const arr = parseArray(lines, i, childIndent);
          result[key] = arr[0];
          i = arr[1];
        } else {
          const nested = parseBlock(lines, i, childIndent);
          result[key] = nested[0];
          i = nested[1];
        }
      } else {
        result[key] = "";
      }
    } else {
      result[key] = parseScalar(afterColon);
      i++;
    }
  }

  return [result, i];
}

function parseArray(lines: Line[], start: number, baseIndent: number): [YamlValue[], number] {
  const result: YamlValue[] = [];
  let i = start;

  while (i < lines.length && lines[i].indent >= baseIndent) {
    const line = lines[i];
    if (line.indent < baseIndent) break;

    if (line.text.startsWith("- ")) {
      const value = line.text.slice(2).trim();
      if (value === "" || value.endsWith(":")) {
        i++;
        if (i < lines.length && lines[i].indent > baseIndent) {
          const nested = parseBlock(lines, i, lines[i].indent);
          result.push(nested[0]);
          i = nested[1];
        } else {
          result.push(parseScalar(value));
        }
      } else {
        result.push(parseScalar(value));
        i++;
      }
    } else {
      break;
    }
  }

  return [result, i];
}

export function parseYaml(source: string): YamlMap {
  const lines = tokenize(source);
  if (lines.length === 0) return {};
  return parseBlock(lines, 0, 0)[0];
}
