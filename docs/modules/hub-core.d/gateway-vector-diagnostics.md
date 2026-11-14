- **Owns:**

  - Worker responses replace vector-disabled reasons with one fixed diagnostic (workspace owners reading every wing keep the raw reason) and omit reasons without a true disabled flag; protocol error data and internal fan-out calls share this boundary. Status uses the same public vector diagnostic normalization.
