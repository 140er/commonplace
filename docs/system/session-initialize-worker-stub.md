### Session initialize tests model the supervised worker

The deadline test failed because its stub worker lacked the generation id that the HTTP request path reads since the supervised restart contract. Every worker the gateway can hold carries one, so the stub is what changed; the gateway keeps requiring the field instead of defaulting a value for objects that cannot occur.
