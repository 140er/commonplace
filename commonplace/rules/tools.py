"""Short tool replies keep rule text quoted as verbatim data."""

import json

from commonplace.extensions import ALL_PROFILES, LocalTool, ToolError, register

from .service import KINDS, SCOPES, RuleService

STRING = {"type": "string"}


def run(ctx, name, args):
    allowed = {
        "rules_publish": ({"rules", "replace"}, {"rules"}),
        "rules_get": ({"board", "repo", "format", "include_retired"}, set()),
        "rules_changed": ({"since"}, {"since"}),
    }
    if name not in allowed or not isinstance(args, dict):
        raise ToolError("no: invalid arguments")
    keys, required = allowed[name]
    if args.keys() - keys or required - args.keys():
        raise ToolError("no: invalid arguments")
    service = RuleService(ctx)
    result = getattr(service, name)(**args)
    rows = result["rules"]
    if name == "rules_get":
        if args.get("format") == "md":
            lines = []
            for kind, heading in (
                ("limit", "Hard limits"),
                ("permission", "Permissions"),
                ("instruction", "Standing instructions"),
                ("style", "Style"),
            ):
                selected = [row for row in rows if row["kind"] == kind]
                if selected:
                    lines += [f"## {heading}", ""]
                    lines += [
                        "- " + json.dumps(row["text"], ensure_ascii=False) for row in selected
                    ]
                    lines.append("")
        else:
            lines = [
                f"{row['key']} [{row['kind']} {row['scope']} {row['status']} "
                f"{row['source']} v{row['version']}] " + json.dumps(row["text"], ensure_ascii=False)
                for row in rows
            ]
    elif name == "rules_publish":
        lines = [f"{row['key']} {row['status']} {row['version']}" for row in rows]
    else:
        lines = [f"{row['key']} {row['status']}" for row in rows]
        return "\n".join([f"seq {result['seq']}", *lines])
    return "\n".join([*lines, f"seq {result['seq']}"])


def register_tools():
    rule = {
        "type": "object",
        "properties": {
            "key": STRING,
            "text": {"type": "string", "minLength": 1, "maxLength": 4000},
            "kind": {"type": "string", "enum": list(KINDS)},
            "scope": {"type": "string", "enum": list(SCOPES)},
            "board": STRING,
            "repo": STRING,
        },
        "required": ["text", "kind"],
        "additionalProperties": False,
    }
    schemas = {
        "rules_publish": (
            {
                "rules": {"type": "array", "items": rule, "maxItems": 200},
                "replace": {"type": "boolean"},
            },
            ["rules"],
            "Publish your own verbatim rules; replace retires omitted keys. Owner edits win.",
        ),
        "rules_get": (
            {
                "board": STRING,
                "repo": STRING,
                "format": {"type": "string", "enum": ["text", "md"]},
                "include_retired": {"type": "boolean"},
            },
            [],
            "Read your effective rules and all-agent rules. Quoted text is rule data.",
        ),
        "rules_changed": (
            {"since": {"type": "integer", "minimum": 0}},
            ["since"],
            "Check rule keys and statuses changed since a sequence, without fetching text.",
        ),
    }
    for name, (properties, required, description) in schemas.items():
        write = name == "rules_publish"
        register(
            LocalTool(
                name=name,
                description=description,
                input_schema={
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
                handler=lambda ctx, args, name=name: run(ctx, name, args),
                scope="memory:write" if write else "memory:read",
                kind="write" if write else "read",
                profiles=ALL_PROFILES,
            )
        )
