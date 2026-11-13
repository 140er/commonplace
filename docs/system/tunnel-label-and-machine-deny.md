### Tunnel labels and the machine default-deny test

- A tunnel label is free text: format characters such as an emoji joiner are removed before the `mcp:` source-ID check, so a label like "50% ready" with an emoji stays readable while an ID split by a joiner is still hidden.
- A runner machine credential gets 404 on every route outside `/api/runner/`, except `/api/instance-admin/`: the instance console answers every non-session credential, machine included, with an audited 403 `Instance administrator access required` (`tests/test_instance_admin_read.py`). The default-deny test expects exactly that.
