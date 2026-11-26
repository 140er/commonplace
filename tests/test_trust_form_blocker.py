"""With no machine registered, the start-permission form says what blocks it instead of staying unfinishable."""

import shutil
import subprocess
from pathlib import Path

SCRIPT = r"""
import assert from "node:assert/strict";
import {mountOrchestratorTrust} from "./commonplace/web/static/projects.js";
const e = s => String(s).replaceAll("<", "&lt;");
class Host {
  isConnected = true;
  set innerHTML(html) {
    this.html = html;
    this.nodes = [...html.matchAll(/<(input|button|a)\b([^>]*)>/g)].map(([, tag, attrs]) => ({
      tag, a: Object.fromEntries([...attrs.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(m => [m[1], m[2] ?? ""])), onclick: null,
    }));
  }
  querySelector(selector) {
    const name = selector.match(/^\[([\w-]+)\]$/)?.[1];
    return name ? this.nodes.find(n => name in n.a) || null : null;
  }
  querySelectorAll(selector) { const n = this.querySelector(selector); return n ? [n] : []; }
  replaceChildren() { this.innerHTML = ""; }
}
const project = (machines, trusted = false) => ({board: "atlas", can_configure: true, main: {connected: true}, orchestration: {trusted, default_max_agents: 3, machines}});
const mount = (p, deck = false) => {
  globalThis.document = {documentElement: {dataset: deck ? {ui: "deck"} : {}}};
  const host = new Host();
  mountOrchestratorTrust(host, {project: p, e, api: async () => assert.fail("render must not save")});
  return host;
};

// No machine: the blocker and a link to add one replace the form.
let host = mount(project([]));
assert.match(host.html, /No machine is registered yet/);
assert.match(host.html, /<a [^>]*href="#\/compute"[^>]*>Add a machine<\/a>/);
for (const control of ["data-trust-grant", "data-trust-confirm", "data-trust-machine"]) assert.equal(host.querySelector(`[${control}]`), null);
assert.doesNotMatch(host.html, /Select machines/);
// The deck look keeps its own route for the same page.
assert.match(mount(project([]), true).html, /href="#\/compute\/management"/);
// Trust that was granted earlier can still be removed.
host = mount(project([], true));
assert.match(host.html, /No machine is registered yet/);
assert.ok(host.querySelector("[data-trust-remove]"));
// With a machine the form is unchanged.
host = mount(project([{id: "one", label: "Runner"}]));
assert.ok(host.querySelector("[data-trust-grant]") && host.querySelector("[data-trust-machine]"));
assert.doesNotMatch(host.html, /No machine is registered yet/);
"""


def test_no_machine_replaces_the_start_permission_form_with_its_blocker():
    result = subprocess.run(
        [shutil.which("node"), "--input-type=module"],
        input=SCRIPT,
        text=True,
        capture_output=True,
        cwd=Path(__file__).resolve().parents[1],
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
