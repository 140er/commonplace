### Memory import keeps picks across the rules load

- The import form opens at once and reads `GET /api/memory/import/rules` in the background. When the rules arrive the form is redrawn with the note element, and the redraw carries over the file type, the destination, the chosen file and the focused control, then applies the rules to that pair. Only a destination the picked file type cannot use is changed, with the note; the server still refuses an incompatible pair after Preview.
