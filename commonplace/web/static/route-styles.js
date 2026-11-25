// Reuse document styles in their original cascade order; missing route CSS still gates content.
const styles = {
  settings: ['deck-settings-capy.css'],
  board: ['deck-board.css', 'chat.css', 'chatdock.css'],
  space: ['chat.css', 'chatdock.css', 'deck-board.css'],
  chat: ['chat.css', 'chatdock.css', 'deck-board.css'],
  card: ['deck-board.css', 'decision-blocks.css'],
  needs: ['deck-board.css', 'chat.css', 'chatdock.css', 'decision-blocks.css'],
  memory: ['map.css', 'chat.css', 'chatdock.css'],
  bots: ['bots.css', 'deck-bots.css', 'deck-coworkers.css'],
  home: ['bots.css', 'deck-bots.css', 'deck-coworkers.css'],
  today: ['deck-board.css'],
  coworkers: ['bots.css', 'deck-bots.css', 'deck-coworkers.css', 'topology.css', 'topology-detail.css'],
  compute: ['topology.css', 'topology-detail.css', 'map.css'],
  'compute-map': ['topology.css', 'topology-detail.css', 'map.css'],
  console: ['console.css'], rules: ['rules.css'], decide: ['decide.css', 'decision-blocks.css'],
};
const pending = new Map();
export function loadRouteStyles(hash = '', doc = globalThis.document) {
  if (globalThis.commonplaceAssetManifest?.styles) return Promise.resolve();
  const name = hash.replace(/^#\//, '').split(/[/?]/)[0] || 'home';
  return Promise.all((Object.hasOwn(styles, name) ? styles[name] : []).map(file => {
    const existing = doc.querySelector?.('link[rel="stylesheet"][href="/app/' + file + '"]');
    if (existing?.sheet) return Promise.resolve();
    if (!pending.has(file)) pending.set(file, new Promise((resolve, reject) => {
      const link = existing || doc.createElement('link');
      const finish = error => {
        clearTimeout(timer); link.onload = link.onerror = null;
        if (error) { pending.delete(file); link.remove(); reject(error); } else resolve();
      };
      const timer = setTimeout(() => finish(new Error('Screen styles timed out. Retry to load this screen.')), 10000);
      link.rel = 'stylesheet'; link.href = '/app/' + file;
      link.onload = () => finish();
      link.onerror = () => finish(new Error('Could not load screen styles. Retry to load this screen.'));
      if (!existing) doc.head.append(link);
    }));
    return pending.get(file);
  }));
}
