- **Owns:**

  - The spend and budgets page (`web/static/budgets.html`, `budgets.js`, `budgets-page.js`, `budgets.css`, with the shared wording in `budget-format.js`), linked from Settings in Classic and Deck, and the Needs me row for a cost budget (`budgetItems` in `needs.js`). It only calls the existing routes: `GET /api/costs`, `GET|PUT|DELETE /api/settings/budgets` and, for names, `GET /api/connections`; it adds no route and no tool.
- **Promises:**

  - Spend is shown per scope (workspace, agent connection, project) for the stated window, such as "November 2026 (UTC)", per currency and never converted. A run without a cost is said so ("N runs without a cost, not counted"), never shown as zero.
  - Only the workspace owner gets an editor. A budget route that answers 404 shows "Only the workspace owner can see budgets" and no control; the spend stays readable for the projects the person can read. Setting, changing or clearing a budget always goes through a confirm that states what a hard stop means ("New starts are refused; running work finishes"); nothing is written before it. Each budget shows ok, warning or over budget with its percent.
  - The Needs me budget warning reads as a sentence (scope, percent, amounts, window, running work) with a link to this page, owner only and only after the server raised it.
- **How it fails:**

  - Every empty, unknown or failed read says why and offers a retry; only a real 401 reads as signed out. A failed write keeps the old budget and the typed values on screen and says "Not saved. The budget is unchanged."; a write whose answer never arrived says the change may or may not have happened. A late answer to an earlier read cannot replace a newer one.
