- **Tests:** `tests/test_admin_instance_api.py::test_commit_falls_back_to_the_environment_and_names_its_source`
  and `tests/test_admin_instance_ui.py::test_running_commit_names_its_source` pin the commit source
  in the deploy payload and the Admin page; the new deploy-script cases in
  `tests/test_commonplace_deploy.py` pin where the build commit comes from.
