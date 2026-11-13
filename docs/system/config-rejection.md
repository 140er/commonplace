### Coworker config decisions

- Project settings offer Changes, Accept and Reject beside a proposed version for its owner. Reject opens a separate confirmation naming the version and explaining that the current config remains active. Cancel makes no write.
- A decision shows its version, result and actor. Rejected versions lose both decision actions and cannot subsequently be accepted through the API. Members can read versions and changes but cannot accept, reject or release work from this section.
- The existing config rejection route uses the same owner checks and audit boundary as acceptance. No new route or wire name is introduced.
- In Deck, config actors use workspace display names or "you" for the viewer; unknown actors keep their identifiers. Diffs wrap within their version, proposal state alone uses amber, and neutral confirmation and result feedback stay next to the affected versions. The styling is scoped to Deck; Classic markup stays unchanged.
