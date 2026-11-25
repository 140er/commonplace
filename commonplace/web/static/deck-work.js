// Work: every project's cards in status lanes. Placeholder until the Work slice lands.
export const TITLE = 'Work';
export function mount({ root }) {
  root.innerHTML = '<section class="deck-work"><p>Loading work…</p></section>';
  return { refresh() {}, stop() {} };
}
