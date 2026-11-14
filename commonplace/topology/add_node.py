"""The map's "Add machine" and "Add site": one button each, no form.

Each is the discovery start (``discovery.start``) with a different piece of work. When a
runner machine can take the work, a normal card start begins it and the answer says
``started``. When none can, the answer is ``prompt``: the same work as a ready, generic prompt
(and, for a machine, the short command) to paste into an agent on the machine the user is
sitting at. Either way what the agent finds reaches the owner as a proposal through
``topology_propose`` (or ``topology_discover_site``), and nothing becomes part of the map
before the owner approves it in the app.

The only input is what cannot be discovered: nothing for a machine, a domain name for a site.
Nothing here reads or changes anything on a machine.
"""

from __future__ import annotations

from commonplace import urls
from commonplace.extensions import ToolError
from commonplace.runner.tools import person_owner

from . import discovery, site_dns, store
from .model import TopologyError

COMMAND = "python3 -m commonplace.topology.collector"

MACHINE_PROMPT = """\
Add this machine to the Compute map. This is read-only work: you collect facts about THIS machine and about how it reaches the hub and other machines, and you propose them to the topology. You change nothing on this machine.

1. Run exactly this one command and read its JSON output:
   {command}
   If Python or the module is missing, install nothing: say so and stop.
2. Call topology_propose with the "propose" object of that output exactly as it is (its items and its evidence). Then, when "propose_peers" is not null, call topology_propose once more with that object exactly as it is. Do not add, edit or drop facts, and add nothing else from this machine.
3. Say what the output's "summary" line says, one line per entry of "unreachable" (what could not be reached, and why), and the answers of topology_propose. The workspace owner approves the proposal in the app; nothing joins the map before that.

To let this machine also run agent work, the workspace owner enrols it as a runner machine in the app; do not create, read or copy any token or key yourself. Run no other command. Do not open, read or copy key files, passwords or any other credential. Do not change any configuration. If topology_propose refuses something, say so and stop.
"""

SITE_PROMPT = """\
Trace the route of the website {site} for the Compute map. This is read-only work: you find how the name reaches the app that serves it and you propose that route to the topology. You change nothing on any machine.

1. If this machine runs a reverse proxy (Caddy, Nginx or Nginx Proxy Manager) that may serve {site}, read its configuration file as it is, without changing anything. Never open key files, passwords or any other credential.
2. Call topology_discover_site with site "{site}", the configuration text of each proxy you read as "proxies" (its node, its kind and the text unchanged), and, when you saw them, the public addresses of the machines in front as "machines" and the containers or apps you saw running as "apps". The tool looks the name up in public DNS and derives the route itself; do not add anything it did not ask for.
3. Say the tool's answer: the route, its notes, and where the owner's decision waits. The workspace owner approves it in the app; nothing joins the map before that.

Run no other command. Do not change any configuration. If the tool refuses something, say so and stop.
"""


class InvalidSite(ToolError):
    """The domain is not one that can be traced; nothing was started."""


def machine_command(hub_url=None) -> str:
    """The short command for the new machine, with the hub's address when there is one."""
    return COMMAND + (f" --hub-url {hub_url}" if hub_url else "")


def machine_prompt(hub_url=None) -> str:
    return MACHINE_PROMPT.format(command=machine_command(hub_url))


def site_prompt(site) -> str:
    return SITE_PROMPT.format(site=site)


def _owner(ctx) -> None:
    if not person_owner(ctx.principal):
        raise ToolError("no: a signed-in workspace owner is required")


def _on_map(ctx) -> set:
    """The runner ids the map already has a node for."""
    topo = store.open_topology(ctx.gateway, ctx.principal.workspace_id)
    return {
        node["fields"]["runner_id"]["value"]
        for node in store.read_graph(topo)["nodes"]
        if "runner_id" in node["fields"]
    }


def _answer(kind, result, prompt, command=None) -> dict:
    """``started`` when a machine took the work, else ``prompt`` with what to paste."""
    taken = [m for m in result["machines"] if m["outcome"] in ("started", "running")]
    if taken:
        return {"kind": kind, "mode": "started", "machines": result["machines"], "prompt": None}
    return {
        "kind": kind,
        "mode": "prompt",
        "machines": result["machines"],
        "prompt": prompt,
        "command": command,
    }


def add_machine(ctx, project=None, topic=None, hub_url=None) -> dict:
    """Start the machine's discovery on a runner that is not on the map yet, or hand out the prompt.

    Returns ``{"kind", "mode", "machines", "prompt", ["command"]}``; ``machines`` holds the
    discovery start's rows (what each machine did and why)."""
    _owner(ctx)
    hub_url = hub_url if hub_url is not None else urls.AGENT_URL
    known = _on_map(ctx)
    work = discovery.Work(
        kind="add-machine",
        title=lambda label: f"Add machine {label} to the map",
        only=lambda machine: machine["id"] not in known,
    )
    result = discovery.start(ctx, project, topic, hub_url, work)
    return _answer("machine", result, machine_prompt(hub_url), machine_command(hub_url))


def add_site(ctx, site=None, project=None, topic=None, hub_url=None) -> dict:
    """Start the route trace of one domain on one runner machine, or hand out the prompt."""
    _owner(ctx)
    try:
        name = site_dns.public_name(site)
    except TopologyError as exc:
        raise InvalidSite(str(exc)) from None
    work = discovery.Work(
        kind="add-site",
        title=lambda label: f"Trace the route of {name} on {label}",
        body=lambda machine_id, hub: site_prompt(name),
        subject=name,
        first_only=True,
    )
    result = discovery.start(ctx, project, topic, hub_url, work)
    return _answer("site", result, site_prompt(name))
