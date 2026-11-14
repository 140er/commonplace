### Compute operator recovery component

`compute/operator.py` gives a trusted operator fenced recovery for controller
execution and discovery, and for helper tunnels and passive reservations that stayed
uncertain. It requires the existing persisted guards before attaching, writes a
private audit note for each action and has no HTTP route. Recovery never implies that
a remote process terminated.
