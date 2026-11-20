# Guided runner program delivery

A site operator can supply a pinned, offline-built program package for the
existing **Add a machine** command. A fresh target needs supported Linux,
Python 3.10 or later with `venv`, and `curl`; it needs no installed runner,
`cryptography`, pip, or access to a package registry. The package must match the
target's Python ABI and machine architecture. This release format supports one
such platform per configured site release.

Delivery installs the real runner program and its dependencies. It grants no
runtime authority. The existing enrollment, fingerprint approval, setup checks
and setup-only restrictions still apply. A productive Coworker run additionally
needs the separate native host authority described in
[the owner issuer decision](system/runner-owner-issuer-pin.md).

## Prepare a release

Use a deliberately provisioned build environment with the exact dependency
versions to distribute. Build from the intended source checkout, entirely
offline. For example, substitute the versions actually approved for that build:

```sh
python -m commonplace.runner.delivery \
  --source . --output /private/releases/runner-program.zip \
  --dependency cryptography==48.0.0 \
  --dependency cffi==2.0.0 --dependency pycparser==3.0
```

The builder verifies each dependency against its installed wheel RECORD hash;
it does not install dependencies or select a newer version. It packages tracked
Python source, verified dependencies and a per-file inventory. It refuses to
replace an existing output. Review source provenance and dependency provenance
in the build environment before publishing its output.

The adjacent `runner-program.zip.json` contains the public digest, size, purpose,
Python ABI, platform, and the operator-local archive path. Configure only the
intended site's process with `COMMONPLACE_RUNNER_PROGRAM_FILE` pointing to that
file. Configuration is off by default; malformed, replaced, linked or writable
release files refuse startup. No signing key or native host trust root is created
or distributed by this mechanism.

## Owner and target

The owner opts into machine setup and creates the command through the normal
app flow. The command pins the launcher hash before running it. Its public
envelope pins the program hash too. The pairing and enrollment intent freeze
that release; replacing the site's configured release does not update an
already-issued command to different bytes.

The target obtains only that program through the existing code-authenticated
pairing check route. A wrong workspace, expired, spent, cancelled or revoked code
cannot download it. A bearer credential or session cannot replace the code.
The request accepts no download URL, local filename, signing key or runtime grant.
An unused live code may fetch the same program again to recover an interrupted
download; delivery itself never consumes the single-use enrollment exchange.

Before creating files, the bootstrap verifies the whole digest, purpose,
platform, bounded archive inventory and individual file hashes. It refuses links,
traversal, duplicate entries and startup injection files. It creates a private
venv without pip, installs the bundled files, imports the real installer, and
checks a real cryptographic signature in that interpreter. Only then does it
publish the complete prefix under
`~/.commonplace-runner-programs/<sha256>/`. An existing prefix must pass integrity
and ownership checks; altered, additional or foreign files are preserved and refused.
Bundled library directories are private and read-only (0500), preventing
untracked bytecode from accumulating when the ordinary setup service imports them.
The bootstrap then continues the existing setup program in the delivered
interpreter, passing the code through stdin and a fixed environment.

For a delivery-only installation check, the downloaded launcher also accepts
`--program-only` after its public envelope. That stops before enrollment, service
or linger operations and leaves the code unused. It does not establish a machine
binding or a successful Coworker run. Keep the code on protected stdin, as in the
owner's original command; never put it in an artifact URL or diagnostic output.

## Recovery and limits

A failed download or validation leaves an earlier installed program intact and
does not change a setup service. Retry with the app's current command; after
expiry, cancellation or release replacement, choose **Set up again**.
The program prefix holds code only, not provider credentials or machine tokens.
It is distinct from the existing installer's private enrollment state and service
journal. There is no automatic deletion of older program versions.

An interrupted enrollment may reuse its exact verified program from the original
copied command, even after the code was spent. Reuse checks the archive, installed
files and Python configuration before starting that interpreter; Python's
case-insensitive configuration keys cannot override isolation through duplicates.
The existing enrollment journal, saved key and signed recovery proof still have
to match. A spent code cannot download a missing program, and delivery-only mode
still requires a live unused code.

A clean venv proves dependency delivery and program startup on the tested host.
It shares that host's account, kernel and base Python standard library; it is not
a new VM, an isolated OS principal, sandbox enforcement, native issuer custody,
provider authentication, service persistence, or productive execution proof.
