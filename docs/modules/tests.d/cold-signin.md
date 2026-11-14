- **Tests:**

  - `test_commonplace_public_surface.py` reproduces cold asset reads followed by email sign-in, pins the independent shell budget and shared credential limits, and checks readable navigation throttling without public-route changes. `commonplace_signin_render.mjs` covers pending/sent, duplicate submission, throttle/failure retry and expired-link states through the shell handler. `test_commonplace_email_auth.py` pins token-free expired-link recovery and unchanged provisioning and throttling protections.
