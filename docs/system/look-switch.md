### Look switch: top of Settings, never a freeze

The Look choice (Deck or Classic) moved from the bottom of Settings, next to Sign out, to the top, as two plain buttons. It appears first in both looks, so Classic shows an equally visible way back to Deck. Switching redraws the current route in place.

The freeze came from the Deck canvases (the page stage, the Appearance preview and its scene thumbnails). Their size comes from CSS scoped to the Deck mark on the page; removing the mark left a canvas as wide as its own bitmap, so each resize callback doubled it until memory ran out. The switch now stops those renderers first, and the renderer never draws larger than the window, so either guard alone ends the loop.

Rotate and Revoke stay on the same buttons and routes but sit in a collapsed "Access tokens" group on each connection, apart from the everyday toggles.
