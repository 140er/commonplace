### Runner shutdown and issuer verification, slice 6

- **M:** [Lifecycle guide](../runner-lifecycle.md): owner Revoke invalidates the
  enrollment, both credentials, grants, binding and provider status atomically.
  A separately supervised four-second signed lease bounds setup independently of
  downloads and sign-in. The unit kills all owned processes; its helper confirms
  termination and clears private credentials before acknowledging. Unrelated work
  survives. Needs me says stop unconfirmed until that acknowledgement arrives.
- **M:** Host verification checks an actual Ed25519 signature against an explicit
  deployment pin, fresh nonce, exact scope and short expiry. No fixture or
  machine-selected issuer key is a fallback. Missing compatible issuer and live
  authorization proofs keep execution disabled.
- **M:** Once the verified runtime and a fresh enrollment are available, the
  owner completes Codex in Needs me: Check this machine, Sign in to Codex, open
  the provider page on any device and enter the displayed short code there.
  For Claude, run `sh "$HOME/.commonplace-runner/sign-in" claude` on the machine,
  open the printed link on any device, then type that page's one-time code into
  the machine. Commonplace never sees this code. The waiting card resolves only
  after a CLI probe and signed machine report; there is no completion checkbox.
- **T:** Live issuer compatibility, direct out-of-grant host refusal and actual
  subscription login require separate live evidence. Cryptographic fixtures
  never count as those proofs; unsupported combinations remain setup-only.
