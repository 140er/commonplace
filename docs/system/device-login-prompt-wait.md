### Device-login prompt in tests

This belongs under the runner setup tests. A device-login CLI may print its URL and its code in separate writes, so the setup check waits until the whole prompt is presented rather than for one read; the test makes the split happen on every run, so a single fixed wait cannot pass it.
