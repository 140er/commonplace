- **Owns:**

  - The extension list in `commonplace/extensions.py` loads `commonplace.today_email` after the phone notifications, so the Email for Today route exists on every server and does nothing without outgoing mail.
