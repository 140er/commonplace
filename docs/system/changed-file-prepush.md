### Changed-file pre-push checks

Pre-push adds real Python typechecking with the existing development mypy dependency,
normal project configuration and imported dependencies. Untyped function bodies are
checked too. Ruff lint/format, Rust formatting, documentation checks and mapped fast
tests remain. The nine-second elapsed target is an alarm, never a cutoff or a reason
to skip checks. Missing tools and inputs that cannot be validated block the push.
Type errors in an existing dependency are reported rather than ignored or baselined.

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
