"""Needs renders only locally reported connection state and safe PR links."""

import subprocess
from pathlib import Path


def test_needs_github_states():
    result = subprocess.run(
        ["node", str(Path(__file__).with_name("github_render.mjs"))],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_needs_real_dom_switch_and_logout_clear(tmp_path):
    import os
    import shutil

    static = Path(__file__).resolve().parents[1] / "commonplace/web/static"
    assert "githubControls" in (static / "needs.js").read_text()
    chrome = (
        shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    assert chrome, "Chrome is required for the DOM connection lifecycle proof"
    html = tmp_path / "connection.html"
    html.write_text(
        '''<!doctype html><html><body><main></main><aside></aside><script type="module">
import {createNeeds} from "'''
        + (static / "needs.js").as_uri()
        + """";
const assert = (value, text) => {if (!value) throw new Error(text);};
try {
 const mount=document.querySelector('main'), detailMount=document.querySelector('aside');
 let deferred, reads=0;
 const card={number:7,version:1,board:'work',title:'Review',need:'merge',actions:[],pr_url:'https://github.com/example/project/pull/1'};
 const api=async path => {
  if(path.startsWith('/api/cards/')) return {...card,number:Number(path.split('/').at(-1))};
  if(path==='/api/github') {
   reads++;
   if(reads===1 || reads===4) return new Promise(resolve=>{deferred=resolve;});
   return {state:reads===2?'connected':'absent',csrf:'synthetic-csrf',login:'synthetic-person'};
  }
  return {holds:[]};
 };
 const ui=createNeeds({mount,detailMount,api,setInterval:()=>1,clearInterval(){}});
 const first=ui.select(7); await new Promise(resolve=>setTimeout(resolve,0));
 assert(detailMount.querySelector('button[disabled]')?.textContent==='Loading GitHub…','loading disabled');
 await ui.select(8); assert(detailMount.textContent.includes('Open PR'),'positive connected DOM');
 const link=[...detailMount.querySelectorAll('a')].find(a=>a.textContent==='Open PR');
 assert(link.href===card.pr_url && link.rel==='noopener noreferrer','safe real DOM link');
 await ui.select(9); assert(!detailMount.textContent.includes('Open PR'),'switch clears connected state');
 deferred({state:'connected',login:'stale-person'}); await first;
 assert(!detailMount.textContent.includes('Open PR'),'late response cannot restore connection');
 const pending=ui.select(10); await new Promise(resolve=>setTimeout(resolve,0));
 ui.stop(); assert(detailMount.innerHTML==='', 'logout clears DOM cache'); const before=detailMount.innerHTML;
 deferred({state:'connected',login:'logged-out-person'}); await pending;
 assert(detailMount.innerHTML===before && !detailMount.textContent.includes('Open PR'),'logout blocks late cache restoration');
 document.body.dataset.result='PASS';
} catch(error) {document.body.dataset.result='FAIL';document.body.dataset.error=error.message;}
</script></body></html>"""
    )
    profile = tmp_path / "profile"
    result = subprocess.run(
        [
            chrome,
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--disable-background-networking",
            "--no-first-run",
            "--host-resolver-rules=MAP * ~NOTFOUND",
            "--allow-file-access-from-files",
            f"--user-data-dir={profile}",
            "--dump-dom",
            "--virtual-time-budget=3000",
            html.as_uri(),
        ],
        capture_output=True,
        text=True,
        timeout=15,
        env={**os.environ},
    )
    assert result.returncode == 0, result.stderr
    assert 'data-result="PASS"' in result.stdout, result.stdout
