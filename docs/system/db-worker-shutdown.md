### Database-polling workers stop first on shutdown

- When the gateway shuts down, the answer-hold worker and the chat front worker are stopped before anything else closes. Before, they ran on until the control database closed, so a due hold could settle while the HTTP server and workers were already closing. A failure to stop one of them is logged and never skips the rest of the shutdown.
