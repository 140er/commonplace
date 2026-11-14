### Runner launch log

- The launch bridge's exit code and labeled stdout/stderr tails go to `<journal_dir>/<job_id>.launch.log` (0600), capped at 4,000 characters after redaction. Known credentials, long token-like strings and the shared storage redactor's secret patterns are removed before any truncation or persistence.
- Failed launches also keep this sanitized output in the existing job row and its result-replay journal. Owner status exposes it as the `detail` of a `launch_unknown` reason. `GET /api/card-starts/<job>/launch-output` returns the reason read-only to signed-in people who can read the card's board; connection tokens and another workspace receive 404. A deleted job has no output to retrieve; workspace export and deletion include the existing job store. No additional table or independent retention policy is introduced.
- Successful launches store no additional job output. The outcome, uncertain-state handling and capacity reservation remain unchanged.
