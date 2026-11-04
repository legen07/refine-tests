export function summarize(rows) {
  return rows.map((r) => `${r.name}: ${r.value}`).join("\n");
}
