- **Promises:**

  - Gateway shutdown stops the answer-hold worker and the chat front worker first, before the HTTP server closes, the handoff service closes or the worker supervisor closes, so neither can settle a hold or claim a chat job in the gap before the control database closes.
