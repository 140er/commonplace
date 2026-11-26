// The bot tree of the Deck sidebar: bots, their projects underneath, and the keyboard rules.
// Pure helpers only (no DOM): the shell draws the markup and owns the state, so these run in a plain test.

export const COLLAPSED_KEY = 'cmmn.deck.tree.collapsed';
export const UNASSIGNED = 'unassigned';
export const botNode = id => `bot:${id}`;

const rows = value => Array.isArray(value) ? value : [];

/** The nodes a person folded away on this device. Unreadable or malformed storage means nothing is folded. */
export function loadCollapsed(storage) {
  try {
    const saved = JSON.parse(storage?.getItem(COLLAPSED_KEY) || '[]');
    return new Set(rows(saved).filter(id => typeof id === 'string').slice(0, 500));
  } catch { return new Set(); }
}
export function saveCollapsed(storage, collapsed) {
  try { storage?.setItem(COLLAPSED_KEY, JSON.stringify([...collapsed].slice(0, 500))); } catch { /* the fold lasts for this visit */ }
}

/**
 * Where each project sits. A project the person can read shows under the bot that lists it, else under
 * Unassigned; a slug the person cannot read (not in boards) never shows, so nothing leaks through the tree.
 * One project is under one bot: the first bot that lists it wins.
 */
export function projectTree({ bots = [], boards = [] } = {}) {
  const known = new Map(rows(boards).filter(board => board?.slug).map(board => [board.slug, { slug: board.slug, name: board.name || board.slug }]));
  const under = new Map(), placed = new Set();
  for (const bot of rows(bots)) {
    if (bot?.shellKind === 'coworker' || bot?.mine === false) continue;
    const mine = [];
    for (const slug of rows(bot.projects)) {
      if (known.has(slug) && !placed.has(slug)) { placed.add(slug); mine.push(known.get(slug)); }
    }
    under.set(String(bot.id), mine);
  }
  return { under, unassigned: [...known.values()].filter(project => !placed.has(project.slug)) };
}

/** The bot node holding the project the route shows ('' when it is not a project route). */
export function currentNode(hash, tree) {
  const slug = /^#\/board\/([^/?]+)/.exec(hash || '')?.[1];
  if (!slug) return '';
  let name = slug;
  try { name = decodeURIComponent(slug); } catch { /* keep the raw slug */ }
  for (const [id, projects] of tree.under) if (projects.some(project => project.slug === name)) return botNode(id);
  return tree.unassigned.some(project => project.slug === name) ? UNASSIGNED : '';
}

/**
 * What a key does on the visible tree items: items are [{ level, expandable, expanded }] in screen order.
 * Returns { focus: index } or { toggle: true|false } (the item's new expanded state), or null for a key it ignores.
 */
export function treeKey(key, items, at) {
  const item = items[at];
  if (!item) return null;
  const last = items.length - 1;
  if (key === 'ArrowDown') return at < last ? { focus: at + 1 } : null;
  if (key === 'ArrowUp') return at > 0 ? { focus: at - 1 } : null;
  if (key === 'Home') return { focus: 0 };
  if (key === 'End') return { focus: last };
  if (key === 'ArrowRight') {
    if (item.expandable && !item.expanded) return { toggle: true };
    return item.expandable && items[at + 1]?.level > item.level ? { focus: at + 1 } : null;
  }
  if (key === 'ArrowLeft') {
    if (item.expandable && item.expanded) return { toggle: false };
    for (let i = at - 1; i >= 0; i--) if (items[i].level < item.level) return { focus: i };
    return null;
  }
  return null;
}
