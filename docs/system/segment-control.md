### Segment control

- The board filter and the Needs me filter render one control, `seg.js` (`segHTML`), and take their base styles from the same sheet; the Deck look restyles both through one set of `deck-board.css` rules.

- A view keeps only the placement of the control (margins, width); type, colour, gap, border and the 32px desktop / 44px phone height belong to the shared rules, so the two filters cannot drift apart.

- A future filter of the same kind uses `segHTML` instead of its own buttons and styles.
