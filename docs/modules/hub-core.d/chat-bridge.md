- **Promises:**

  - `/api/bridge/in/` is a public route prefix (no session): the chat platform's signature is the credential, and `POST .../<install>/events` and `.../interact` go to the intake pipeline (see chat-bridge), every other path and every unknown or off install is answered 404; the intake has a 256 KiB body cap and a per-install token bucket. `commonplace.chat_bridge` is in the extension module list.
