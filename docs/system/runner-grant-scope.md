### Runner grant scope is a report

- A machine's grant scope is a report, not evidence: the owner submits the record printed offline by `runner check`, the hub stores it with its digest and marks it `verified:false`. T3 access fields stay null (unknown) because local settings do not show effective grants; `provider_instance_id` is known only with exactly one configured instance. Runner limits are not T3 access.

- A report never binds. A first or changed canonical record resets the binding and increments the epoch once; an identical record does not. After a report, attest must quote the displayed digest and epoch; a mismatch returns 409 and writes nothing, and the three-key attest is refused. Records are bounded, rejected on duplicate keys, nonfinite values and credential-shaped text, and shown only to the workspace owner.
