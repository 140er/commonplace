### Memory search query contract

The Memory form field and controller state both use `query`. Form submission and filter changes pass that same field to search, and redraws retain its value and focus. The HTTP search parameter remains `q`. Empty input still reports “Use 1–8 words, up to 200 characters.” Source selection and server permission checks are unchanged.
