### Tunnel labels and machine-token deny order

- A tunnel label is free text: format characters such as an emoji joiner are removed before the `mcp:` source-ID check, so a label like "50% ready" with an emoji stays readable while an ID split by a joiner is still hidden.
- A runner machine credential is refused with 404 on every route outside `/api/runner/`, including `/api/instance-admin/`; that default-deny runs before the instance console's flag check and denial audit.
