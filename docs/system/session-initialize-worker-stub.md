### Session initialize tests model the supervised worker

- The HTTP request path reads the worker's generation id to tell a proven recycling refusal from an ordinary failure. The deadline test in the session initialize suite used a stub worker without a generation and failed with an attribute error, although it was not the code under test.
- Decision: the stub is fixed, not the product. Every worker the gateway can hold is a supervised worker that always carries a generation, so the request path keeps requiring it rather than defaulting to a made-up value for objects that cannot occur.
- Not changed: the gateway, the restart contract and the test's intent (the deadline re-arms on each read and the normal worker timeout returns afterwards).
