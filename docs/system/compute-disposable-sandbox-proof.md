### Disposable sandbox verification stays opt-in

The existing positive script fixture also checks SC-30 namespace and route
isolation. Normal selection and its absent-fixture skip reason stay unchanged;
configured failures fail. [The verification record](../compute/disposable-sandbox-proof.md)
names approval and records both the initial launch failure and the successful
rerun inside a transient delegated systemd scope. The opt-in launcher matches the
runner/fixture cgroup layout and leaves persistent machine settings unchanged.
No product capability is enabled by the test or this evidence.
