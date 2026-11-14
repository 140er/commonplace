### Compute execution component

`compute/executor.py` runs durable execution attempts for approved actions: each
attempt is recorded before any exec, checks exact signed enrollment, current
authority and custody at every stage, consumes its approval once and keeps an
uncertain outcome reserved rather than assuming termination. Published evidence is
bounded and redacted. The owner UI shows the observed exit, or an uncertain
cancellation, and never implies dispatch from approval alone. The executor is
inert until trusted configuration enables it.
