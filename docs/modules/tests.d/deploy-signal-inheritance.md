- **Tests:**

  - `tests/test_commonplace_deploy.py::test_deploy_sh_releases_reference_on_signal` checks HUP=129, INT=130 and TERM=143 with default and ignored parent SIGINT dispositions, preserving reference release and refusal to reach Docker. The fixture resets SIGINT before Bash starts, restores the test's parent handler and cleans up only its private process group.
