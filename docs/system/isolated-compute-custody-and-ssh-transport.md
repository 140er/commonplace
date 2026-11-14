### Isolated Compute custody and SSH transport

Compute is an opt-in dependency extra (Paramiko >=5.1,<6); installation enables
no routes, listeners or remote effects. The isolated credential store uses
service-private POSIX safe-open checks, AES-256-GCM wrapped workspace keys and
metadata-bound encrypted Ed25519 credentials. Explicit wrapping-key initialization
is separate from database initialization/open. Missing material never regenerates
a key over ciphertext; only public enrollment records leave custody. Pending keys
require explicit activation; exact-bound authority callbacks, expiry and local
revocation are checked again when a transport handle yields private bytes.
Python memory zeroization and remote authorized-key removal are not guaranteed.
Offline wrapping-key rotation/recovery and the OS worker/service isolation remain
integration prerequisites, not implemented release claims.

The helper/transport modules default disabled and accept only protected enrolled
numeric routes. Full outer gateway and nested target host keys are compared
before their separate public-key authentications. No ambient SSH config, agent,
password, generic forwarding API or direct-target fallback is used. Only the
literal system.info.v1 probe is compiled. Bounded exact JSON control frames
(maximum 8KiB), five-second admission challenges and durable replay consumption
precede route opening. Defaults/ceilings: 30-second runtime, five-second connect,
64KiB combined output, 1MiB tunnel bytes and two active tunnels per gateway.
Failed opens and successful target.close release helper concurrency, including
after abnormal relay endings, while retaining replay tombstones. Close failures
and crash-orphaned consumed rows stay counted. Named helper tunnel/passive rows
have operator recovery under the same SQLite admission fence, with private audit
notes and operator_closed state. Late completion cannot re-reserve them. Closure
alone does not prove target process termination.
Remote output is raw bounded bytes and must pass redaction/audience checks before
publication. Passive observation sources and scripts are deliberately unavailable.

Synthetic socketpair fixtures exercise actual Paramiko handshakes and encrypted
custody keys through two SSH hops, successful fixed-probe output, and independent
wrong-pin refusal before the affected authentication. They use no network/device
endpoint. Public enrollment/approval APIs, controller authority fencing,
background execution/outbox integration and deployment prerequisites are still
required before enabling this transport for users. The pure callbacks do not
claim atomic cross-store revocation. Custody filesystem tests require native
POSIX ownership semantics. Environments that remap ownership must fail the
safe-open checks.
