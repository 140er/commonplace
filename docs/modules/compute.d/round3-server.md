- **Owns:**

  - Review round 3, server side (`pairing.py`, `runner/machine_setup.py`, `runner/installer.py`): the machine's stored credential is only ever sent to the site that issued it; a command for a different site stops before any request or removal; a `request_id` that is present must be a valid string for accept and close; a site error at the exchange is "unavailable", not a refused code.

- **Promises:**

  - A machine credential never leaves the site it was issued for. When the code was spent and nothing could be saved, the window does not point at a Set up again the page does not offer yet: it says the app shows the setup as not finished after 15 minutes.

- **How it fails:**

  - A command for another site than the one this machine already runs for prints that site and "Nothing was changed." (no request carries the old credential, no file or unit changes).

- **Tests:** `tests/test_compute_pairing.py` (null `request_id`, the closed-report status pinned) and `tests/test_machine_setup.py` (other site, spent-code wording, 5xx at the exchange).
