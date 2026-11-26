### The live-sync import fixture approves its OAuth client as the owner

The import-then-live sync tests obtain an OAuth import token through the real
registration, consent and token routes. Since a workspace owner must approve a
new client, the fixture ticks the same **Approve this client for workspace**
checkbox a real owner ticks on the consent page; the approval requirement itself
is unchanged.
