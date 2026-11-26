### Connect asks the memory question first

- On a project that uses shared memory, Connect agent now asks which memory the agent gets before it sends the connect request, instead of failing first and then asking. The default is the project's own memory section (the least-sharing choice); allowing all shared memory is the explicit alternative.
- Decision: the server's `shared_wing` refusal on connect is unchanged and still the backstop. The page only moved the question ahead of the request; a stale project view that skips it is refused and then asks the same question.
