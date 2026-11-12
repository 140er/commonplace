### OAuth native redirect

- The hub accepts exactly one private-use redirect, `cmmn://auth/callback` (`oauth.NATIVE_REDIRECTS`, RFC 8252 section 7.1), for the native app. It is an exact string match: no other scheme, wildcard, path, query, fragment or case variant registers.
- Native clients are public clients like every other: no client secret, and every authorization requires PKCE S256. The consent page names the full redirect as the code destination and its `form-action` carries the scheme source `cmmn:`.
- A second native app or scheme is a new entry in that set with its own tests, never a pattern.
