- **Promises:**

  - When no build commit is configured, the deploy script takes the full commit from the staged source tree (the file the archive stager writes beside the source, else a checkout's HEAD), passes it as the image build argument and attests it; the archive stager records the commit a git archive carries. The host compose example also hands the commit to the container environment as a second source.

- **How it fails:**

  - A source tree with no recorded commit still deploys, prints `build commit: unknown`, and Admin shows Running commit unknown.

- **Tests:**

  - `tests/test_commonplace_deploy.py` covers the derived commit, an explicit commit winning, the unknown case and the archive stager.
