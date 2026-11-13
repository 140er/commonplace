### Disposable sandbox verification stays opt-in

The existing positive script fixture also checks SC-30 namespace and route
isolation. Normal selection and its absent-fixture skip reason stay unchanged;
configured failures fail. [The verification record](../compute/disposable-sandbox-proof.md)
names approval and records the actual launch failure rather than treating
preflight or synthetic SSH success as a confinement pass. No product capability
is enabled by the test or this evidence.
