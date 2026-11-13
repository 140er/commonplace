import { boardView } from '../commonplace/web/static/board.js';
import { workingHTML } from '../commonplace/web/static/deck-today.js';
import { escape as e } from '../commonplace/web/static/api.js';
const line = '3 agents busy (limit 3). 4 cards wait for a free slot, 2 cards wait for another card.';
const board = { slug: 'demo', name: 'Project', agents_working: 3, waiting_summary: line };
console.log(JSON.stringify({
  board: boardView({ board, cards: [] }).includes(line),
  header: workingHTML([], [board], e).includes(line) && workingHTML([], [board], e).includes('<h2>Working now <span>3</span></h2>'),
  escaped: !boardView({ board: { ...board, waiting_summary: '<script>bad</script>' }, cards: [] }).includes('<script>bad'),
  absent: !boardView({ board: { slug: 'demo' }, cards: [] }).includes('undefined'),
}));
