### Needs me keeps a closed detail panel closed

- When the user closes the Needs me detail panel, a background refresh no longer opens it again. Before, a changed payload (for example a new item arriving) re-read the current selection and its select callback showed the panel. The refresh still updates the list and the highlighted item; the panel opens again when the user selects an item (card #421).
