/** Display only: callers keep the original wing id for storage and requests. */
export function wingLabel(wing, name) {
  if (name?.trim() && name !== wing) return name;
  if (wing === "shared") return "Workspace notes";
  return String(wing || "").replace(/^project_/, "").replaceAll("_", " ").toLowerCase().replace(/\b\w/g, letter => letter.toUpperCase());
}
