// Prints the Deck's Move to... options for a card in each status, as the server tests read them.
const { moveOptions } = await import(new URL('../commonplace/web/static/cardmove.js', import.meta.url));
const me = 'user:me', out = {};
for (const status of ['backlog', 'todo', 'doing', 'done']) out[status] = moveOptions({ number: 1, status, version: 1 }, me);
out.review = moveOptions({ number: 1, status: 'review', review_kind: 'moved', ask: 'Moved to review.', version: 1 }, me);
out.asked = moveOptions({ number: 1, status: 'review', review_kind: 'decision', ask: 'Ship it?', ask_to: me, version: 1 }, me);
console.log(JSON.stringify(out));
