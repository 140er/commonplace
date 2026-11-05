import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
const source = await readFile(new URL('../commonplace/web/static/rail.js', import.meta.url), 'utf8');
const apiSource = await readFile(new URL('../commonplace/web/static/api.js', import.meta.url), 'utf8');
const apiURL = `data:text/javascript;base64,${Buffer.from(apiSource).toString('base64')}`;
const rail = await import(`data:text/javascript;base64,${Buffer.from(source.replace("'./api.js'", JSON.stringify(apiURL))).toString('base64')}`);
for (const fn of ['renderRail', 'renderPhoneBar', 'renderPhoneTabs', 'projectSubline']) assert.equal(typeof rail[fn], 'function', `${fn} missing`);
const board = { slug: 'atlas', name: 'Atlas', agents_working: 2, counts: { doing: 3, review: 10 } };
const options = { workspace: 'Northwind', user: 'Maya', board, boards: [board], needs: 3, needsByBoard: { atlas: 2 } };
const links = (html) => [...html.matchAll(/<a\b[^>]*>[\s\S]*?<\/a>/g)].map(m => m[0]);
switch (process.argv[2]) {
  case 'phone': {
    const html = rail.renderPhoneTabs({ ...options, unread: 1 });
    assert.equal(links(html).length, 3);
    assert.ok(!html.includes('Memory'));
    assert.match(html, /aria-label="Chat 1 new reply"/);
    assert.match(html, /href="#\/board\/atlas\/chat"/);
    assert.match(html, /role="status">1 new reply in Atlas chat/);
    const needs = rail.renderPhoneTabs({ ...options, view: 'needs', unread: 2 });
    assert.match(needs, /aria-label="Atlas chat 2 new replies"/);
    const harbor = rail.renderPhoneTabs({ board: { slug: 'harbor', name: 'Harbor' }, view: 'needs' });
    assert.match(harbor, /aria-label="Harbor chat"/);
    assert.ok(!harbor.includes('newb'));
    const bar = rail.renderPhoneBar(options);
    assert.match(bar, /Project: Atlas. Switch project/);
    assert.match(bar, /aria-label="Settings"/);
    assert.match(bar, /data-action="palette"/);
    assert.ok(!rail.renderPhoneBar().includes('m-settings'));
    break;
  }
  case 'counts': {
    const html = rail.renderRail(options);
    assert.match(html, /aria-label="3 items">3/);
    assert.match(html, /aria-label="2 need you">2/);
    assert.ok(!html.includes('>10</span>'));
    assert.equal(rail.projectSubline(board), '2 agents working');
    assert.equal(rail.projectSubline({ agents_working: 1 }), '1 agent working');
    assert.equal(rail.projectSubline({ counts: { doing: 4 } }), '4 doing');
    assert.equal(rail.projectSubline({}), 'Idle');
    assert.ok(!rail.renderRail().includes('class="count need"'));
    break;
  }
  case 'current': {
    for (const view of ['board', 'chat', 'settings', 'space', 'needs', 'memory']) {
      const html = rail.renderRail({ ...options, view });
      const active = links(html).filter(a => a.includes('aria-current="page"'));
      assert.equal(active.length, 1);
      assert.ok(active[0].includes(view === 'needs' ? '#/needs' : view === 'memory' ? '#/memory' : '#/board/atlas'));
    }
    for (const view of ['board', 'chat', 'needs']) {
      const active = links(rail.renderPhoneTabs({ ...options, view })).filter(a => a.includes('aria-current="page"'));
      assert.equal(active.length, 1);
      assert.ok(active[0].includes(view === 'needs' ? '#/needs' : view === 'chat' ? '#/board/atlas/chat' : '#/board/atlas"'));
    }
    break;
  }
  case 'menu': {
    const html = rail.renderRail(options);
    for (const route of ['rules', 'console', 'settings']) assert.ok(html.includes(`href="#/${route}"`));
    for (const action of ['logout', 'palette', 'shortcuts', 'new-board', 'switch-workspace']) assert.ok(html.includes(`data-action="${action}"`));
    assert.match(html, /<details class="user-menu"><summary/);
    assert.match(html, /Workspace settings/);
    assert.match(html, /Control\+K Meta\+K/);
    assert.match(html, /Shortcuts <kbd>\?<\/kbd>/);
    assert.match(html, /<style>/);
    assert.match(html, /max-width: 699px/);
    break;
  }
  case 'escaping': {
    const bad = '<img src=x onerror="bad()">';
    const b = { slug: 'a/"<>', name: bad };
    for (const html of [rail.renderRail({ workspace: bad, user: bad, boards: [b] }), rail.renderPhoneBar({ board: b }), rail.renderPhoneTabs({ board: b, view: 'needs', unread: -1 })]) {
      assert.ok(!html.includes('<img'));
      assert.ok(html.includes('&lt;'));
      assert.ok(!html.includes('class="badge newb"'));
    }
    assert.match(rail.renderPhoneTabs({ board: b }), /a%2F%22%3C%3E/);
    assert.match(rail.renderPhoneTabs(), /href="#\/start"/);
    break;
  }
  default: assert.fail('Unknown case');
}
