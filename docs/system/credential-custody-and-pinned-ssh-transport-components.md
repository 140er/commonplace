### Credential custody and pinned SSH transport components

`compute/credentials.py` holds encrypted SSH key custody: strict protected wrapping
keys and binding-authenticated ciphertext, with only public enrollment data
returned. `compute/transport.py` and `compute/script_transport.py` open the pinned
two-hop SSH channel for the fixed probe and confined helper command, and
`compute/fence.py` guards authority writes with a cross-process fence. All of them
are inert until trusted configuration enables them, deny on host-key mismatch,
replay or unknown operations, and use no live endpoints in tests.
