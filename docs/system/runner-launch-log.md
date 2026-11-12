### Runner launch log

- The runner discards nothing the launch bridge says: exit code, stdout and stderr go to `<journal_dir>/<job_id>.launch.log` (0600) with credentials and long token-like strings redacted, capped at 4,000 characters. A `launch_unknown` used to hide why a launch failed; the log is for the owner and never replaces the outcome the runner reports to the hub.
