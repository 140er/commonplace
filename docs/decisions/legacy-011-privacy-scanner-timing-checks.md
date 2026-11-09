### Privacy scanner timing checks

- Large-input and delimiter-heavy near-miss correctness checks run in the behaviour suite without measuring time.

- Each input also retains a separate `timing`-marked check of the 20 ms average scan budget over 20 iterations.

- The duration assertion is outside loops so a slow host produces an informational timing failure while incorrect flags remain blocking.

