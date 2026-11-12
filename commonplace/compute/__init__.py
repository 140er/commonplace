"""Compute management plus explicit trusted, default-off service configuration.

The host may pass ExecutionConfig to ControlDB at service startup. Configuration
is per workspace, never request input or environment discovery. Only already
provisioned workspace/custody files are accepted; no private key is initialized
or read by imports, management requests, or lifecycle installation.
"""
