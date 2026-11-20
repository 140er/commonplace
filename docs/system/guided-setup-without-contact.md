### Checked guided state without connection telemetry

Overview uses completed guided setup before the generic missing-connection status. A setup-only service does not provide runner heartbeats, and rotating its credential can clear connection-use telemetry without undoing the completed checks. Neither condition turns a checked guided machine into an unconnected developer runner or supplies a developer-token recovery step.

Run-capacity summaries and detail facts stay unused while setup-only admission blocks work, including when the owner disables that machine. This projection changes no lease, account or machine authority and adds no runnable capability. Ordinary developer machines retain their existing status and recorded usage/resource facts.
