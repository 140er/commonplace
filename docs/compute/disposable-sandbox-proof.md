# Disposable sandbox and SC-30 verification

Card #370; verification attempted on 13 November 2026. Isolation was approved by
Chief of Staff on Jeffrey's standing approval of 11 November, 11:22 CET. The
approval is recorded in the card's 13 November comment. Provisioning and rollback
remain operator responsibilities; these tests change no machine settings.

## Explicit opt-in

The existing `COMMONPLACE_COMPUTE_SANDBOX_FIXTURE` environment variable names an
operator-provisioned `ScriptTargetConfig` JSON file. Leave it unset for normal
runs and CI: the positive test still skips with exactly
`no explicitly provisioned disposable cgroup/tmpfs/read-only runtime fixture`.
No new test is added to the default selection. The configured workspace must be
`fixture-script-workspace`; a configured but broken fixture fails, never skips.

Run from a trusted checkout inside the disposable fixture as its non-root user.
Set `FIXTURE_CONFIG`, `FIXTURE_TMP` and `FIXTURE_RESULTS` to paths inside that
fixture, and use its existing offline test environment. No dependency downloads
or network changes are needed. All temporary files must stay inside the fixture.

```sh
COMMONPLACE_COMPUTE_SANDBOX_FIXTURE="$FIXTURE_CONFIG" \
  TMPDIR="$FIXTURE_TMP" PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest -v -p no:cacheprovider --basetemp="$FIXTURE_RESULTS" \
  tests/test_compute_script_sandbox.py::test_optional_actual_disposable_confined_script
```

This positive test also checks the SC-30 sandbox boundary: distinct network,
mount, PID, user, UTS and IPC namespaces; only the private loopback interface;
no IPv4 routes or IPv6 routes through other interfaces; no resolver configuration
or host interface filesystem. It performs no network connections or DNS queries.
The existing non-root, hidden/unchanged outside sentinel, literal argument,
cleared agent socket and exact artifact assertions remain required.

The separate synthetic gateway/SSH boundary is exercised with:

```sh
TMPDIR="$FIXTURE_TMP" PYTHONDONTWRITEBYTECODE=1 \
  python -m pytest -v -p no:cacheprovider --basetemp="$FIXTURE_RESULTS" \
  tests/test_compute_ssh_fixture.py::test_encrypted_custody_to_actual_two_hop_ssh_without_network
```

That test uses socketpairs and disposable generated credentials, checks the
gateway-only transport path and independently refuses wrong gateway/target pins.
It is not a live SSH endpoint or disposable network SSH integration proof. A
passing socketpair test cannot substitute for a failing confinement test. The
management SSH connection used to invoke tests is outside the sandbox boundary.

## Observed evidence and limits

Read-only fixture qualification passed: non-root identity and user namespaces;
root-owned runtime and matching read-only bind; private workspace/config;
64 MiB / 4096-inode scratch tmpfs; delegated CPU, memory and process controllers;
writable per-fixture `cgroup.kill`; and `BubblewrapSandbox.check`.
Recomputed `runtime_inventory()` digest:
`3e2c4526fcdd9bc9c3c3f8272961fffee957ff4b96f2d8684e89645114504819`.

The unchanged positive test on base revision
`04356e2bac32148b8cf119f70d6d83be26075365` failed:

```text
FAILED tests/test_compute_script_sandbox.py::test_optional_actual_disposable_confined_script
AssertionError: provisioned sandbox execution failed: denied/sandbox_unavailable
1 failed in 1.07s
```

A diagnostic preserved that denial and located `SubprocessError` in the spawn
boundary before result collection. The invoking session is outside the delegated
fixture subtree, and the common ancestor's process-migration file is not writable
by the fixture user. Cgroup migration permissions are a suspected cause, not a
confirmed diagnosis. No setting was changed and no execution assertion was
relaxed. The new SC-30 assertions require successful launch; failed launch leaves
confinement and full SC-30 acceptance unproven. Operator correction and a new
explicit run are required before claiming a confinement pass or enabling scripts.

The extended positive and the separate socketpair selection were then run in
the same disposable fixture, with these results:

```text
test_optional_actual_disposable_confined_script FAILED
AssertionError: provisioned sandbox execution failed: denied/sandbox_unavailable
test_encrypted_custody_to_actual_two_hop_ssh_without_network[None] PASSED
test_encrypted_custody_to_actual_two_hop_ssh_without_network[gateway] PASSED
test_encrypted_custody_to_actual_two_hop_ssh_without_network[target] PASSED
1 failed, 3 passed in 11.93s
```

The positive and SC-30 confinement assertions did not reach the script body.
The three SSH cases establish synthetic transport evidence only. This is a test
coverage and evidence change, not a sandbox fix or a passing SC-30 verdict.
