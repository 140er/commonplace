- **Owns:**

  - What the Memory page (classic and Deck, desktop and phone) does when the notes search answers a 503 with `retry_after_s`: the plain status line, the automatic retries and the fallback.

- **Promises:**

  - The page shows one line, "Memory is warming up, retrying in N s" when the answer says `warming_up` and "Memory is busy, retrying in N s" otherwise, counting down every second. It retries after the stated wait (at most 10 s per wait, at least 1 s) up to 3 times, so a search makes at most 4 requests, one at a time. The typed query and the results already on screen stay while it waits, and the first successful answer replaces them. A new query, a click on Try again, or leaving Memory ends the pending retry.

- **How it fails:**

  - After the third retry the page shows the existing "Memory could not be searched just now. Try again." message (or the partial-results warning) with its Try again button, and nothing retries on its own. A 503 without `retry_after_s`, or any other failure, is never retried and keeps the existing message.

- **Tests:**

  - `tests/memory_warming.mjs` (run by `tests/test_memory_warming.py`) drives both looks with a fake 503 and checks the line text for both states, the countdown, the retry count, the cap, the fallback with its Retry button, cancelling on a new query, and that at most one request is in flight.
