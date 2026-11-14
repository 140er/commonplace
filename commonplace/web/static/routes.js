/** The board screen a bare visit used to land on: the last visited readable board, else the
 * first one, else the first-run screen. Shared card links and "back from admin" still need a
 * project screen behind them, so they keep using this.
 */
export function boardRoute({ boards = [], last } = {}) {
  const board = boards.find((board) => board.slug === last) || boards[0];
  return board ? `#/board/${encodeURIComponent(board.slug)}` : "#/start";
}

/** Resolve a hash without changing browser state or reading saved preferences.
 * boards is the readable board list; last is a previously visited board slug.
 * A person who has a project lands on their bots; someone with none sees the first-run screen.
 * Old chat links keep opening the project chat. In the Deck look (`deck`) the Compute map is
 * the Compute screen, so its older address resolves there. Return a hash string for the app dispatcher.
 */
export function resolveRoute(hash, { boards = [], last, deck = false } = {}) {
  const route = hash || "#/";
  if (route === "#/board") return boardRoute({ boards, last });
  if (route === "#/") return boards.length ? "#/bots" : "#/start";
  if (route === "#/compute-map" && deck) return "#/compute";
  if (route === "#/compute" && !deck) return "#/compute/management";
  if (route === "#/coworkers") return "#/bots/-/coworkers";
  const coworkers = /^#\/coworkers\/([^/?]+)(\?message=[^&#]+)?$/.exec(route);
  if (coworkers) return `#/bots/-/coworkers/conversations/${coworkers[1]}${coworkers[2] || ""}`;
  const chat = /^#\/chat\/([^/?]+)(\?.*)?$/.exec(route);
  if (chat) return `#/board/${chat[1]}/chat${chat[2] || ""}`;
  return route;
}

/** Nested workspace pages, separate from the existing single-segment bot IDs. */
export function workspaceRoute(hash = "") {
  if (hash === "#/compute" || hash === "#/compute/management") return { screen: "compute", management: hash.endsWith("/management") };
  const match = /^#\/bots\/([A-Za-z0-9_-]+)\/coworkers(?:\/(conversations)(?:\/([^/?]+)(?:\?message=([^&#]+))?)?)?$/.exec(hash);
  if (!match || (match[2] && match[1] !== "-")) return null;
  try {
    return { screen: "coworkers", bot: match[1] === "-" ? null : match[1], conversations: !!match[2], conversation: match[3] ? decodeURIComponent(match[3]) : null, message: match[4] ? decodeURIComponent(match[4]) : null };
  } catch { return null; }
}
