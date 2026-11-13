import { boardView, cardTags } from '../commonplace/web/static/board.js';
import { workingHTML } from '../commonplace/web/static/deck-today.js';
import { escape as e } from '../commonplace/web/static/api.js';
const line = '3 agents busy (limit 3). 4 cards wait for a free slot, 2 cards wait for another card.';
const taskLine = '3 agents busy (limit 3). 4 tasks wait for a free agent, 2 wait for another task, 1 waits for a person.';
const board = { slug: 'demo', name: 'Project', agents_working: 3, waiting_summary: line, waiting_task_summary: taskLine };
console.log(JSON.stringify({
  board: boardView({ board, cards: [] }).includes(line),
  header: workingHTML([], [board], e).includes(taskLine) && workingHTML([], [board], e).includes('<h2>Working now <span>3</span></h2>'),
  taskCopy: !/\b(?:cards?|slots?)\b/i.test(workingHTML([], [board], e)),
  working: cardTags({ need: 'working', assignee: 'conn:worker' }, { deck: true }),
  workingTimed: cardTags({ need: 'working', assignee: 'conn:worker', updated_at: new Date(Date.now() - 120000).toISOString() }, { deck: true }),
  escaped: !boardView({ board: { ...board, waiting_summary: '<script>bad</script>' }, cards: [] }).includes('<script>bad'),
  absent: !boardView({ board: { slug: 'demo' }, cards: [] }).includes('undefined'),
}));
