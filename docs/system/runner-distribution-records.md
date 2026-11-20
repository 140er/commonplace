### Canonical runner distribution layout

Program archives contain `site/<relative-library-or-metadata-path>` entries.
Installed dependency library paths and their `.dist-info` metadata retain their
original bytes and must match the wheel RECORD SHA256. Absolute paths and parent
segments never enter the archive. Exact dependency pins, applicable markers and
the complete installed-prefix inventory still determine acceptance.

CFFI 2.1.1 also records a generated `../../../bin/cffi-gen-src` launcher outside
site-packages. It is a build tool and is not needed to import or run the bundled
libraries. The builder omits only that exact record when CFFI declares the single
console entry point `cffi-gen-src = cffi._cffi_gen_src:run`, the file is in the
current interpreter's scripts directory, its ownership, permissions and link
count are safe, and its RECORD hash matches. Other external records refuse the
build. The omission installs no console command or startup hook.

The real-archive test fixture follows this same library-only layout after
checking every included or omitted file's actual RECORD hash. Tests still reject
an archive containing the external launcher path. Python 3.10's conditional
typing-extensions dependency and Debian's whole-prefix startup refusal remain
required controls. This compatibility change grants no binding, readiness or
execution authority.
