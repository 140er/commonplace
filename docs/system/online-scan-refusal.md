### Online backup refusals name the writer

When an online backup stops because another process uses the data directory, the message now carries that process's redacted command line, the variable or argument that matched and its state, so a one-off refusal under load can be traced to a process instead of guessed at. The matching rule is unchanged: whole path components inside the exact data directory, never a name prefix.
