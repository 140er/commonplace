### Changed-file pre-push checks

Pre-push adds real Python typechecking with the existing development mypy dependency,
normal project configuration and imported dependencies. Untyped function bodies are
checked too. Ruff lint/format, Rust formatting, documentation checks and mapped fast
tests remain. The nine-second elapsed target is an alarm, never a cutoff or a reason
to skip checks. Missing tools and inputs that cannot be validated block the push.
Type errors in an existing dependency are reported rather than ignored or baselined.
The hook passes explicit package bases with the repository as its working directory.
This gives namespace modules such as tests.helpers the same identity when supplied as
changed roots and reached through imports. All roots and untyped-body checks remain;
no test-package marker, ignore, exclusion or strictness reduction is introduced.
When both an implementation and its matching stub change, they remain checked as
explicit roots in separate mypy invocations, avoiding a duplicate module while
retaining diagnostics and push refusal for a type error in either file.

The hook validates clean tracked files at the pushed HEAD; another pushed commit or
dirty tracked input requires validation from the correct checkout. Deleted files do
not reach file-based tools. New branches and multiple updates use Git's pre-push
input rather than assuming the current branch's upstream. Different pushed heads
are refused instead of checking one checkout on their behalf.

Hook staging writes a new directory and leaves existing hooks and configuration
untouched. The previous pre-push runs first with identical stdin and arguments;
existing executable hooks execute through their original paths. Absent or disabled
hooks remain absent so Git retains its default handling, including checkout updates
and dirty-receiver refusal for `receive.denyCurrentBranch=updateInstead`. Stage again
when the set of enabled hooks changes. Shared activation is a reviewed step and
rollback restores the prior configuration, retaining all original hooks.

Staging refuses a relative `core.hooksPath` before writing anything: ordinary hooks
and receive hooks resolve it from different directories. The existing configuration
and receive-side refusals remain intact. Default and absolute hook directories are
supported; audit both hook sets before changing a relative configuration.
