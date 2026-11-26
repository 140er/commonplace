### An open Drop reservation form is not rebuilt for a card that did not change

- Symptom: on a busy workspace the Classic drawer replaced the open Drop reservation form again and again, so a click on its button or the reason being typed was lost to a node that had just been replaced. The form was only protected from a refresh that was still a background one; a poll that overlapped a foreground refresh is drawn as a foreground refresh, and a foreground read drew the whole drawer over the form.
- Decision: the drawer does not draw again while an open removal form was drawn from the card version the read returned. A different version, a closed card or a route change still draws as before. Cancel removes the form before it asks for the redraw, because Cancel relies on the same card being drawn again.
- Same pattern as the sidebar, which is left in place when its content is unchanged.
- Not changed: the form's fields, the request it sends, the refusal messages, and the Deck look.
