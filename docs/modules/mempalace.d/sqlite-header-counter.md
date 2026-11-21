- **Promises:**

  - The capacity fingerprint reads SQLite header bytes 24–27 from an opened regular file. It opens nonblocking where supported and validates the descriptor before reading, so a FIFO appearing at open cannot block status. Ordinary counters and regular-file symlinks retain their behavior.

- **How it fails:**

  - Missing, short, unreadable and non-regular files yield no counter. An open failure never triggers a blocking retry, and every opened descriptor is closed even when validation or reading fails.
