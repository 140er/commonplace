### Remove-assignment checks wait for the drawer

This belongs under the card drawer tests. The drawer renders its remove-assignment form and refusals after the card loads, so the checks wait for each sentence and control to be visible rather than reading visibility once; a loaded machine can no longer catch the drawer mid-render.
