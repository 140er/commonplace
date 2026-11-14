### Compute control startup configuration

`ControlDB` takes an optional `compute_config` at service startup. When present it
registers the control database with the cross-process authority coordinator, attaches
persisted write guards and wraps the database lock so each transaction holds the
coordinator for its whole duration; shutdown signals every service before joining
it. Without the argument nothing changes, and no request, import or environment
variable can supply the configuration or enable execution. The owner-session
management routes and script control path are installed through the normal
extension list and perform no remote effect until the trusted configuration enables
them.
