- **Tests:**

  - `tests/test_commonplace_retention.py` pins the post-deploy retention script (kept and removed images, release directories, dry run, invalid settings and the deploy lock) against a fake `docker` and a temporary release directory; Linux only.
