// Prints what the Add card form sends when the user types a title and changes nothing else.
// argv: <path to board.js> <person ref> [explicit Who value to pick instead of the default]
import { pathToFileURL } from 'node:url';
const [, , boardJs, me, pick] = process.argv;
const b = await import(pathToFileURL(boardJs).href);
const html = b.addCardForm({ me, people: [{ ref: me, name: 'Maya' }, { ref: 'user:leon', name: 'Leon' }], sheet: false });
const select = html.match(/<select name="assignee"[^>]*>([\s\S]*?)<\/select>/)[1];
const options = [...select.matchAll(/<option value="([^"]*)"([^>]*)>([^<]*)<\/option>/g)].map(([, value, attrs, label]) => ({ value, label, selected: /\bselected\b/.test(attrs) }));
// A browser submits the selected option, or the first enabled one when none is marked selected.
const chosen = pick !== undefined ? pick : (options.find((o) => o.selected) || options[0]).value;
let body;
await b.submitCard({ board: { slug: 'atlas' }, me, values: { title: 'Buy milk', assignee: chosen }, api: async (url, init) => { body = init.body; return { number: 1 }; } });
const hint = html.match(/data-who-hint[^>]*>([^<]*)</)?.[1] ?? null;
console.log(JSON.stringify({ options, chosen, body, hint }));
