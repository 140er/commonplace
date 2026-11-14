/* The text of a Compute map label when it is drawn at a constant size: a name or a second line wraps to
   at most two lines inside its plate (breaking after a space, dot, hyphen, slash or middle dot) before
   anything is cut, and only text that still does not fit in two lines ends in an ellipsis. Widths are
   estimates that err wide (capitals, wide letters and non-Latin glyphs count more), so a line that
   passes always fits. No DOM, no measured fonts. */
const glyph = (char, em) => (/[^\x20-\x7e]/.test(char) ? 1.1 : /[MW@%]/.test(char) ? 0.95 : /[A-Z0-9mw]/.test(char) ? 0.72 : /[il.,:;'|!tfIjr]/.test(char) ? 0.4 : 0.62) * em;
export const textWidth = (text, em) => Array.from(text).reduce((sum, char) => sum + glyph(char, em), 0);
const PIECES = /[^ .\-_/·]*[ .\-_/·]*/g;

/** `text` as at most `lines` lines no wider than `width` at `em` pixels. */
export function wrapLines(text, width, em, lines = 2) {
  const full = String(text ?? "").replace(/\s+/g, " ").trim();
  if (textWidth(full, em) <= width) return [full];
  const out = [];
  let line = "";
  const push = () => { out.push(line.trimEnd()); line = ""; };
  for (let piece of full.match(PIECES).filter(Boolean)) {
    if (textWidth(line + piece, em) <= width || (!line && textWidth(piece.trimEnd(), em) <= width)) { line += piece; continue; }
    if (line) push();
    // A piece wider than a whole line is cut by characters.
    while (textWidth(piece.trimEnd(), em) > width) {
      const chars = Array.from(piece);
      let n = 1;
      while (n < chars.length && textWidth(chars.slice(0, n + 1).join(""), em) <= width) n++;
      out.push(chars.slice(0, n).join("")); piece = chars.slice(n).join("");
    }
    line = piece.trimStart();
  }
  if (line) push();
  if (out.length <= lines) return out;
  const kept = out.slice(0, lines);
  let last = Array.from(`${kept[lines - 1]}${out.slice(lines).join(" ")}`);
  while (last.length && textWidth(`${last.join("")}…`, em) > width) last.pop();
  kept[lines - 1] = `${last.join("").trimEnd()}…`;
  return kept;
}

/** What a label draws: its lines and its height in screen pixels (15 a line, a little under the last). */
export function labelBox(name, sub, labels) {
  const room = labels - 16, nameLines = wrapLines(name, room, 12), roleLines = sub ? wrapLines(sub, room, 11) : [];
  return { nameLines, roleLines, height: 15 * (nameLines.length + roleLines.length) + (roleLines.length ? 8 : 7) };
}
