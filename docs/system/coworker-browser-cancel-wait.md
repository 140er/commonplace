### Wait for cancel responses in the coworker browser journey

The coworker browser test cancels the queued response first and then the blocked one, and waits for each cancel request's 2xx response before it reads job state. Cancel requests are sent asynchronously by the page, so reading the store right after a click could race the request under load; cancelling the blocked response first would also let the worker start the queued one. The assertions and the product behaviour are unchanged.
