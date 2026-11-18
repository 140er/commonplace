import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { pathToFileURL } from 'node:url';

const dir = process.env.STATIC_DIR || new URL('../commonplace/web/static', import.meta.url).pathname;
const app = readFileSync(dir + '/app.js', 'utf8');
const slice = (start, end) => app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));
const mode = process.argv[2];
const board = { slug: 'project', name: 'Project' };
const state = { me: {actor:'user:fixture',can_admin:true}, base:'#/board/project',route:'#/board/project',screen:1,refreshRequest:0,boards:[board],cards:[],chatTimes:new Map() };
const content = {innerHTML:'',children:[],querySelector: selector => selector === '.workbench-board' && content.innerHTML ? {} : null,querySelectorAll:()=>[]};
const panel = {hidden:true};
const context = {state, api(){}, cardAdded(){}, cancelCard(){}, enc:encodeURIComponent, e:String, root:{querySelector: selector => selector === '#card-panel' ? panel : selector === '#content' ? content : null}, title(){}, currentBoard:()=>board, boardDispose:null, bindBoard:()=>()=>{}, cardId:()=>'', phone:()=>false, markOpenCard(){}, fillChatTimes(){}, mountProjectPanel(){}};

if (mode === 'render-count') {
  let renders = 0;
  state.cards = Array.from({length:1000},(_,number)=>({number,title:`Card ${number}`,status:'todo'}));
  Object.assign(context,{boardCards:()=>state.cards, renderBoard(){renders++;return '<div class="workbench-board">cards</div>';}});
  runInNewContext(slice('function boardView()', '// The form closes'), context);
  context.boardView(); context.boardView();
  assert.equal(renders,1,'An unchanged 1000-card board must reuse its mounted tree');
} else if (mode === 'api-wave') {
  const requests = [], atPaint = [];
  const paint = () => atPaint.push(...requests);
  Object.assign(context, {requestAnimationFrame(){}, remember:()=>({}),restore(){},capture:()=>()=>{},navigation(){},showError(){},waitingTotal:()=>0,rememberDeckBots(){},boardView:paint,mountWorkbenchChat(){},
    deckNeedsLedger:{apply:value=>value},api:async path=>{requests.push(path);return path.includes('/cards')?{cards:[],linked:[]} : path.includes('/boards')?{boards:[board]}:{count:0,bots:[]};}});
  runInNewContext(slice('async function refresh(', 'function decideStart('),context);
  await context.refresh();
  assert.ok(atPaint.length<=2,`First paint issued ${atPaint.length} API reads: ${atPaint}`);
  assert.ok(!atPaint.some(path=>/needs-me|rules|\/bots/.test(path)),'Badges must wait for paint');
} else if (mode === 'bare-board') {
  const {boardRoute,resolveRoute}=await import(pathToFileURL(dir+'/routes.js'));
  Object.assign(state,{boards:[],route:'#/',base:'#/',screen:0,chatVisible:new Set(),deck:{on:()=>true,owns:hash=>hash==='#/compute',leave(){}}});
  Object.assign(context,{boardRoute,resolveRoute,location:{hash:'#/board'},history:{state:null,replaceState(_a,_b,hash){context.location.hash=hash;}},localStorage:{getItem:()=>null,setItem(){}},boardPreference:()=>'',
    remember(){},api:async()=>({boards:[board]}),prepareRoute:async()=>{},clearAgentSecret(){},voice:{stop(){}},speech:{stop(){}},decideStart(){},detailsUI:null,botsUI:null,consoleUI:null,showLoading(){},showError(){},refresh:async()=>{},markOpenCard(){}});
  runInNewContext(slice('async function route()', 'function openingCard('), context);
  await context.route();
  assert.equal(context.location.hash,'#/board/project','Cold bare board must discover the default board before resolving');
} else throw new Error('Unknown case');
