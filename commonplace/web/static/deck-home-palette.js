// Product navigation uses the shell's existing destinations and authorized roster.
export function deckPaletteItems({ board, bots = [] } = {}) {
  return [
    { id: 'new-thread', label: 'New thread', group: 'Actions', href: '#/', key: '/' },
    ...[['today', 'Today'], ['bots', 'Bots'], ['compute', 'Compute'], ['memory', 'Memory'], ['board', 'Board'], ['settings', 'Settings']].map(([id, label]) => ({ id, label, group: 'Go to', href: id === 'board' ? board?.slug ? `#/board/${encodeURIComponent(board.slug)}` : '#/boards' : `#/${id}` })),
    ...bots.filter(bot => !bot.frozen && !bot.lifecycle).map(bot => ({ id: `bot-${bot.id}`, label: bot.name, group: 'Your bots', href: `#/bots/${encodeURIComponent(bot.id)}` })),
  ];
}
const styles = `
:root[data-ui="deck"] .workbench-palette .gh { color:var(--ink2); font-size:13px; font-weight:500; letter-spacing:0; text-transform:none; }
:root[data-ui="deck"] .workbench-palette .pin:focus-within { box-shadow:inset 0 0 0 1px #4a4a4a; outline:0; }
:root[data-ui="deck"] .workbench-palette .opt[aria-selected="true"] { background:var(--s2); box-shadow:none; outline:0; border:0; }
:root[data-ui="deck"] .workbench-palette .pin input:focus { outline:0; box-shadow:none; }
:root[data-ui="deck"] .workbench-palette .opt .r kbd { display:none; border:0; background:none; box-shadow:none; padding:0; }
:root[data-ui="deck"] .workbench-palette .opt[aria-selected="true"] .r kbd { display:inline; }
:root[data-ui="deck"] .workbench-palette .pfoot { font-size:12px; color:var(--ink3); }
:root[data-ui="deck"] .workbench-palette .pfoot kbd { border:0; box-shadow:none; background:none; padding:0; min-width:0; height:auto; font:inherit; color:inherit; }
:root[data-ui="deck"] .workbench-palette .palette-close { display:none; }
@media(max-width:699px) {
  :root[data-ui="deck"] .workbench-palette .pfoot { display:none; }
  :root[data-ui="deck"] .workbench-palette .palette-close { display:grid; place-items:center; flex:none; width:44px; height:44px; padding:0; border:0; background:none; color:var(--ink2); }
}
`;
if (globalThis.document?.adoptedStyleSheets) {
  const sheet = new CSSStyleSheet(); sheet.replaceSync(styles);
  document.adoptedStyleSheets = [...document.adoptedStyleSheets, sheet];
}
