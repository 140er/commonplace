### Page actions in the desktop title bar

In the Deck look, a page's main creating action sits at the right of the shared
52px title bar on a desktop, as Projects (New project) and Rules (New rule) do:
Bots (New chat, New bot), Cloud workspaces (Add a machine), Board (Add card) and
Memory (Add a note, Import memory files). Add site and Map it with a bot stay in
the Cloud workspaces page, and the board options menu stays in the Board page.

The bar holds the page's own elements, so every action keeps its handler and data
attributes and does the same thing as before. The in-page copy of a moved action
is not drawn on a desktop. On a phone (under 700px) and in the Classic look the
actions stay in the page, where the bar is the phone header. Resizing across the
breakpoint moves them between the two places. Board and Memory keep their project
chat out of the bar; only their own action shows there.
