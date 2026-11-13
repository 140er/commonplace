- **Owns:**

  - `map-add.js`: the Compute screen's "Add machine" and "Add site" buttons post to `/api/topology/add-machine` and `/api/topology/add-site`, write the answer under the actions (a started run, or that no agent is available and the prompt was copied, with the short command), and copy the prompt to the clipboard (shown in the notice when the browser refuses). "Add site" asks only for a domain name. `deck-compute.js` binds both buttons with `data-compute-action="add-machine"` and `"add-site"`; a member sees the screen's refusal and no buttons.
