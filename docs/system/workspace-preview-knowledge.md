### Workspace preview knowledge

A start preview distinguishes a quiet saved or stopped workspace from an active
phase and an uncertain one. Missing or stale reports, and a withdrawn claim whose
machine became unreachable, never establish that no work is running. The preview
states that a start request does not stop existing work. A refused start on a
fresh active phase also avoids claiming that the workspace is empty.

Action metadata and preview refusal text use the same Unknown presentation. The
existing machine-readable refusal reasons, lifecycle permissions, confirmation
tokens, request-only behavior and idempotent repeats stay unchanged. These
projections neither execute an operation nor certify current machine activity.
