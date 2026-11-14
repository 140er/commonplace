"""Install selected verified artifacts and derive an execution-disabled cache."""

import json
import re

from .install_files import STATE, digest, encoded
from .install_host import InstallError
from .receipt_cache import CACHE, Cache, compatibility, verified
from .runtime_host import LiveRuntimeHost
from .runtime_manifest import COMPONENTS, MAX_ARTIFACT, facts_valid, validate

PREFIX = STATE + "/runtime"
HOME = PREFIX + "/home"
CHECKOUTS = STATE + "/checkouts"
RUNTIME_CACHE = STATE + "/runtime-config.json"


def owned(path, kind):
    if kind == "file":
        return path == RUNTIME_CACHE or bool(
            re.fullmatch(re.escape(PREFIX) + r"/(host|claude|codex)-[0-9a-f]{64}\.artifact", path)
        )
    return path in (PREFIX, HOME, HOME + "/.codex", HOME + "/.claude", CHECKOUTS) or bool(
        re.fullmatch(re.escape(CHECKOUTS) + r"/[0-9a-f]{64}", path)
    )


class Runtime:
    def __init__(self, installer, config, transport, host=None):
        self.install = installer
        self.fs = installer.fs
        self.cache = Cache(self.fs, installer.journal, config, transport)
        self.transport = transport
        self.host = host if host is not None else LiveRuntimeHost()

    def _receipt(self):
        stored = json.loads(self.fs.read(CACHE))
        receipt = verified(stored["signed"], self.cache.key, self.cache.identity)
        if stored["config"] != compatibility(receipt):
            raise InstallError("approved cache changed")
        validate(receipt["runtime"], receipt["topics"])
        return receipt

    def _write_cache(self, value):
        raw = encoded(value)
        item = next(i for i in self.install.journal["items"] if i["path"] == RUNTIME_CACHE)
        if item["digest"] != digest(raw):
            item["digest"] = digest(raw)
            self.install.flush()
        if self.fs.read(RUNTIME_CACHE) != raw:
            self.fs.write(RUNTIME_CACHE, raw, replace=True)

    def clear(self):
        if self.fs.info(RUNTIME_CACHE) is not None:
            self._write_cache({})

    def sync(self):
        receipt = self._receipt()
        releases = receipt["runtime"]["components"]
        report = {name: {"availability": "missing", "login": "unknown"} for name in releases}
        staged = {}
        try:
            # Verify the complete selection before executing even the first artifact.
            for name in COMPONENTS:
                if name not in releases:
                    continue
                release = releases[name]
                path = PREFIX + "/" + name + "-" + release["sha256"] + ".artifact"
                if self.fs.info(path) is not None:
                    if not any(i["path"] == path for i in self.install.journal["items"]):
                        raise InstallError("runtime prefix is occupied")
                    raw, source = self.fs.read(path), path
                else:
                    candidate = self.host.locate(name)
                    if candidate and digest(candidate["raw"]) == release["sha256"]:
                        raw, source = candidate["raw"], candidate["path"]
                    else:
                        raw, source = self.host.download(release), path
                if (
                    not isinstance(raw, bytes)
                    or not raw
                    or len(raw) > MAX_ARTIFACT
                    or digest(raw) != release["sha256"]
                ):
                    raise InstallError("runtime integrity verification failed")
                staged[name] = (path, source, raw)
            for directory in (PREFIX, HOME, CHECKOUTS):
                self._directory(directory)
            # Even a read-only status command may initialize its private home.
            # Record ownership before invoking either subscription CLI.
            for provider in set(releases) - {"host"}:
                self._directory(HOME + "/." + provider)
            for name, (_, _, raw) in staged.items():
                report[name] = facts_valid(
                    self.host.probe(name, releases[name], raw, self.fs.home / HOME), name
                )
            self._receipt()  # Refuse expiry during download or capability probing.
            # A pin includes the reported version. A mismatched version or missing
            # headless/auth interface is unsupported, never a reason to run work.
            components = {}
            for name, (path, source, raw) in staged.items():
                if report[name]["availability"] == "present" and source == path:
                    self.install.file(path, raw)
                components[name] = {
                    **report[name],
                    "version": releases[name]["version"],
                    "sha256": releases[name]["sha256"],
                    "format": releases[name]["format"],
                    "path": source,
                }
            projects = {}
            for label, project in sorted(receipt["projects"].items()):
                directory = CHECKOUTS + "/" + digest(project["project_id"].encode())
                self._directory(directory)
                projects[label] = {**project, "directory": directory}
            # Neither receipt nor mapping grants execution authority.
            config = {
                "version": 1,
                "mode": "setup-only",
                "agent_ready": False,
                "provider_instance_id": receipt["provider_instance_id"],
                "worker_connection_id": receipt["worker_connection_id"],
                "topic_models": receipt["runtime"]["topic_models"],
                "projects": projects,
                "components": components,
                "authority": {
                    k: receipt[k]
                    for k in (
                        "workspace_id",
                        "machine_id",
                        "installation_id",
                        "manifest_digest",
                        "grant_version",
                        "binding_epoch",
                        "settings_version",
                        "install_settings_version",
                    )
                },
                "runtime_digest": digest(encoded(receipt["runtime"])),
            }
            if self.fs.info(RUNTIME_CACHE) is None:
                self.install.file(RUNTIME_CACHE, encoded({}))
            self._write_cache(config)
            self._report(receipt, report)
            return config
        except Exception:
            self.clear()
            # Only fixed enums can cross the report boundary, even on failure.
            self._report(receipt, report)
            raise InstallError("runtime setup incomplete; owner review required") from None

    def _directory(self, path):
        if self.fs.info(path) is not None and not any(
            i["path"] == path for i in self.install.journal["items"]
        ):
            raise InstallError("runtime directory is occupied")
        self.install.directory(path)

    def _report(self, receipt, facts):
        token = self.fs.read(STATE + "/machine.token").decode()
        self.transport.runtime_status(
            token,
            {
                "settings_version": receipt["settings_version"],
                "grant_version": receipt["grant_version"],
                "binding_epoch": receipt["binding_epoch"],
                "runtime_digest": digest(encoded(receipt["runtime"])),
                "components": facts,
            },
        )

    def read(self):
        receipt = self._receipt()
        raw = self.fs.read(RUNTIME_CACHE)
        item = next(i for i in self.install.journal["items"] if i["path"] == RUNTIME_CACHE)
        if digest(raw) != item["digest"]:
            raise InstallError("runtime cache changed")
        config = json.loads(raw)
        if (
            config.get("runtime_digest") != digest(encoded(receipt["runtime"]))
            or config.get("provider_instance_id") != receipt["provider_instance_id"]
            or config.get("topic_models") != receipt["runtime"]["topic_models"]
            or config.get("agent_ready") is not False
        ):
            raise InstallError("runtime cache changed")
        # This is a cache, never an alternative source of executable pins.
        for name, release in receipt["runtime"]["components"].items():
            component = config["components"][name]
            if any(component[k] != release[k] for k in ("version", "sha256", "format")):
                raise InstallError("runtime cache changed")
        return config
