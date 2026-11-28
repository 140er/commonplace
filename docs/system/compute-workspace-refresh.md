### Cloud-workspace controls during background refresh

The app reads its event feed while a cloud-workspace form is open. An unrelated event refreshes the Compute inventories. Replacing the form during those reads closed its native image-help disclosure and lost focus on its summary. Browser geometry and scrolling operations could also finish against a detached control.

The Compute controller now carries the existing workspace controls into the refreshed page, as it already does for other sections. The workspace module keeps the original focused control when it is still connected. An unchanged workspace-list and image-configuration response leaves the controls in place; changed records and configuration still repaint, including stale-record warnings. Changed machine choices use fresh markup. Focus on Refresh is remembered through a subsequent background read as well as the original manual read.

The browser regression opens image help over an unsaved draft, injects one unrelated app event and holds the inventory response. It checks disclosure state, focus and draft values while the read is pending and again after it completes, in Classic and Deck, then checks Refresh focus across another event. If the person resumes editing during a read, focus stays on their field. The existing owner journey and the adjacent browser checks retain their original assertions.
