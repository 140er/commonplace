### Typed helper protocol and script runtime components

`compute/protocol.py` carries the strict, bounded wire shared by the controller
and the target helper, including chunked script uploads and the typed passive
cache request. `compute/scripts.py` and `compute/script_sandbox.py` hold immutable
script bundles and a default-off confined Python runtime, and
`compute/target_helper.py` is the only target-side entry. These modules are inert
until trusted configuration enables them and perform no remote effect on import.
