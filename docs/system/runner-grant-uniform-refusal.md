### A wing grant by another workspace's owner leaks no wing names

- `PATCH /api/runners/{id}` used to validate the wing names in the body before it looked up the machine, so an owner of one workspace who named another workspace's machine got 400 for a wing name outside their own workspace and 404 for one inside it. The route now looks up the machine in the caller's workspace first: a machine that is not theirs, or does not exist, always answers 404 `machine not found`, whatever the body names. Wing validation (400) only runs for the caller's own machine. Card #415.
