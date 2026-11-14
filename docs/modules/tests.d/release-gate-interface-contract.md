- **Tests:**

  - `tests/test_commonplace_release_gate.py::test_release_journey_browser_labels_exist_in_the_shipped_interface` checks that every visible name the journey looks for still exists in the shipped interface; it failed for the old space, Search memories and Create names.

  - `tests/test_commonplace_release_gate.py::test_failure_report_names_each_error_inside_sdk_task_groups` and `::test_api_helper_takes_a_longer_deadline_for_the_cold_worker_write` check leaf-error reporting and the cold-worker deadline; `::test_email_login_waits_through_stale_app_until_signed_in_board` now requires the board only for a new signup.
