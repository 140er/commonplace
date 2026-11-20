### Card-start refusals survive optional reminder storage failures

Configured and manual card starts decide the account before recording a missing-connection reminder. If that optional write raises an I/O, SQLite or timeout error, the caller logs it with the existing safe failure logger and preserves the original `connection_missing` refusal and its next step. No job is queued and no reminder is fabricated. Account resolution, requester identity, machine grants and admission checks retain their existing failure behavior.

The regression selection uses real dropped and shared-cache locked reminder tables in both paths. Healthy reminders, successful personal-account starts and unreadable account settings provide controls.
