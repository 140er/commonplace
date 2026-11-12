### Meetings store: literal table names in destructive statements

- The meeting lifecycle writer deleted a discarded meeting's segments and participants in a loop over table names, which the A4 eval guard flags as a dynamic table name (a destructive statement the guard cannot tell from a thread-store delete). It now runs two literal `DELETE` statements with the same behaviour. New code in `commonplace/meetings/` keeps the rule: name the table in the statement, and loop over values, not table names.
