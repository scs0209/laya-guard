"""Paths from a shell command: which files it plainly names, and what it does to them (read, write, delete).

Best effort, from the text alone. Simple forms only (cat, rm, cp, mv, redirects, ...); anything the shell would still
have to resolve (variables, globs, substitutions) is left out, never guessed. The file activity log records what this
finds, and the protected paths judge it.
"""
from __future__ import annotations

import os
import re
import shlex

MAX_COMMAND_CHARS = 20_000


# --- paths from a Bash command (best effort) ----------------------------------------------------------------------------

_SEPARATORS = {";", "&&", "||", "|", "&", "|&", "(", ")"}
_MOVES = {"cd", "pushd", "popd"}            # after one, a relative path no longer starts from the call's cwd
_WRITE_REDIRECTS = {">", ">>", ">|", "&>", "&>>"}
_SKIP_TARGETS = {"-", "/dev/null", "/dev/stdout", "/dev/stderr", "/dev/stdin", "/dev/tty"}
_WRAPPERS = {"sudo", "doas", "time", "nohup", "command", "exec", "nice", "env"}
_READ = {"cat", "head", "tail", "less", "more", "nl", "wc", "tac", "strings", "xxd", "od", "bat", "file", "stat"}
_GREP = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
_DELETE = {"rm", "rmdir", "unlink", "shred"}
_WRITE = {"touch", "tee", "mkdir", "truncate"}
_ASSIGN = re.compile(r"^[A-Za-z_]\w*=")
# Options whose value is the next word. That word is a count, a mode, a format or a pattern, never a path to record.
# A command not listed here can still have such an option: it is then misread as a path only if it is a literal word.
_VALUE_OPTIONS = {
    "head": {"-n", "-c", "--lines", "--bytes"},
    "tail": {"-n", "-c", "-s", "--lines", "--bytes", "--sleep-interval", "--pid", "--max-unchanged-stats"},
    "less": {"-b", "-h", "-j", "-k", "-o", "-O", "-p", "-P", "-T", "-x", "-y", "-z", "--pattern", "--tag"},
    "more": {"-n", "-p", "-P"},
    "nl": {"-b", "-d", "-f", "-h", "-i", "-l", "-n", "-s", "-v", "-w"},
    "tac": {"-s", "--separator"},
    "strings": {"-n", "-t", "-e", "-T", "--bytes", "--radix", "--encoding", "--target"},
    "xxd": {"-c", "-g", "-l", "-o", "-s", "-n", "-cols", "-groupsize", "-len", "-seek", "-name"},
    "od": {"-A", "-j", "-N", "-t", "-w", "-S", "--address-radix", "--skip-bytes", "--read-bytes", "--format", "--width"},
    "bat": {"-l", "-H", "-r", "-m", "--language", "--highlight-line", "--line-range", "--map-syntax", "--theme",
            "--style", "--tabs", "--wrap", "--color", "--paging", "--pager", "--decorations", "--terminal-width",
            "--italic-text", "--file-name"},
    "file": {"-e", "-f", "-F", "-m", "-P", "--exclude", "--files-from", "--separator", "--magic-file", "--parameter"},
    "stat": {"-c", "-f", "-t", "--format", "--printf"},
    "grep": {"-A", "-B", "-C", "-m", "-d", "-D", "--after-context", "--before-context", "--context", "--max-count",
             "--directories", "--devices", "--include", "--exclude", "--exclude-dir", "--label", "--binary-files", "--color"},
    "rg": {"-A", "-B", "-C", "-m", "-M", "-j", "-g", "-t", "-T", "-E", "-r", "--after-context", "--before-context",
           "--context", "--max-count", "--max-columns", "--threads", "--glob", "--iglob", "--type", "--type-not",
           "--encoding", "--replace", "--max-depth", "--sort", "--sortr", "--type-add", "--pre", "--color", "--colors"},
    "ag": {"-A", "-B", "-C", "-m", "-G", "-g", "--after", "--before", "--context", "--max-count", "--depth", "--ignore",
           "--file-search-regex"},
    "ack": {"-A", "-B", "-C", "-m", "--after-context", "--before-context", "--context", "--max-count", "--type"},
    "mkdir": {"-m", "--mode", "--context"},
    "touch": {"-d", "-r", "-t", "-A", "--date", "--reference"},
    "truncate": {"-s", "-r", "--size", "--reference"},
    "cp": {"-S", "--suffix"},
    "mv": {"-S", "--suffix"},
    "install": {"-m", "-o", "-g", "-S", "--mode", "--owner", "--group", "--suffix"},
    "shred": {"-n", "-s", "--iterations", "--size", "--random-source"},
}
_VALUE_OPTIONS["egrep"] = _VALUE_OPTIONS["fgrep"] = _VALUE_OPTIONS["grep"]


def _literal(token: str) -> bool:
    """A path we can read off the command as written: nothing the shell would still have to expand."""
    return bool(token) and not any(c in token for c in "$`*?[]{}!")


def _tokens(line: str) -> list[str]:
    lex = shlex.shlex(line, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    return list(lex)


def _segments(tokens: list[str]) -> list[list[str]]:
    segments, current = [], []
    for t in tokens:
        if t in _SEPARATORS:
            segments.append(current)
            current = []
        else:
            current.append(t)
    segments.append(current)
    return [s for s in segments if s]


def _redirects(segment: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    """The segment without its redirections, and the (op, path) pairs those redirections name."""
    args, found, i = [], [], 0
    while i < len(segment):
        t = segment[i]
        nxt = segment[i + 1] if i + 1 < len(segment) else ""
        if t.isdigit() and nxt[:1] in (">", "<"):          # a file descriptor in front of the operator: 2> file
            i += 1
        elif t in _WRITE_REDIRECTS or t == "<":
            if nxt and not nxt.startswith("&") and nxt not in _SKIP_TARGETS and _literal(nxt):
                found.append(("read" if t == "<" else "write", nxt))
            i += 2
        elif t in (">&", "<&", "<<", "<<-", "<<<"):         # descriptor copies, heredocs and here-strings name no file
            i += 2
        else:
            args.append(t)
            i += 1
    return args, found


def _flags_and_positionals(args: list[str], takes_value: set[str] = frozenset()) -> tuple[list[str], list[str]]:
    flags, positional, done, i = [], [], False, 0
    while i < len(args):
        t = args[i]
        if not done and t == "--":
            done = True
        elif not done and t.startswith("-") and t != "-":
            flags.append(t)
            if t in takes_value:                                     # `-n 5`: the 5 is the option's, not a path
                i += 1
        else:
            positional.append(t)
        i += 1
    return flags, positional


def _grep_files(args: list[str], takes_value: set[str] = frozenset()) -> list[str]:
    """The files a grep-like command reads. The first word is the pattern unless -e or -f gave it, and the value of
    -e / -f is never a file to read as a path (a -f value is: it is the pattern file)."""
    positional, patterned, done, i = [], False, False, 0
    files: list[str] = []
    while i < len(args):
        t = args[i]
        if not done and t == "--":
            done = True
        elif not done and t in ("-e", "--regexp"):
            patterned = True
            i += 1
        elif not done and t in ("-f", "--file"):
            patterned = True
            files += args[i + 1:i + 2]
            i += 1
        elif not done and t.startswith("--regexp="):
            patterned = True
        elif not done and t.startswith("--file="):
            patterned = True
            files.append(t.split("=", 1)[1])
        elif not done and t.startswith("-") and t != "-":
            if t in takes_value:                                     # `-A 3`, `-g '*.py'`: not the pattern, not a file
                i += 1
        else:
            positional.append(t)
        i += 1
    return files + [t for t in (positional if patterned else positional[1:]) if not t.isdigit()]


def _command_accesses(args: list[str]) -> list[tuple[str, str]]:
    while args and (_ASSIGN.match(args[0]) or args[0] in _WRAPPERS):
        args = args[1:]
        while args and args[0].startswith("-") and args[0] != "-":   # sudo -E, nice -n ...
            args = args[1:]
    if not args:
        return []
    name = os.path.basename(args[0])
    takes_value = _VALUE_OPTIONS.get(name, frozenset())
    flags, pos = _flags_and_positionals(args[1:], takes_value)
    found: list[tuple[str, str]] = []

    def add(op: str, tokens) -> None:
        found.extend((op, t) for t in tokens if _literal(t))

    if name in _READ:
        add("read", [p for p in pos if not (name in ("head", "tail") and p.isdigit())])
    elif name in ("source", "."):
        add("read", pos[:1])
    elif name in _GREP:
        add("read", _grep_files(args[1:], takes_value))
    elif name in _DELETE:
        add("delete", pos)
    elif name in ("cp", "mv", "install"):
        if len(pos) >= 2 and not any(f.startswith(("-t", "--target-directory")) for f in flags):
            add("delete" if name == "mv" else "read", pos[:-1])
            add("write", pos[-1:])
    elif name in _WRITE:
        add("write", [p for p in pos if not (name == "truncate" and p.isdigit())])
    elif name in ("chmod", "chown", "chgrp"):
        add("write", pos[1:])                                        # the first word is the mode or the owner
    return found


def shell_accesses(command: str) -> list[tuple[str, str]]:
    """(op, path) pairs a Bash command plainly names. Best effort: anything unclear is left out, never guessed."""
    return [(op, path) for op, path, _ in shell_accesses_moved(command)]


def _parsed(command: str):
    """(args, accesses) for each command in the text, in the order they run: the words without redirections, and the
    (op, path) pairs the command and its redirections name."""
    for line in command.splitlines():
        try:
            tokens = _tokens(line)
        except ValueError:                                           # an unbalanced quote: not a line we can read
            continue
        for segment in _segments(tokens):
            args, redirected = _redirects(segment)
            yield args, _command_accesses(args) + redirected
        if "<<" in tokens:                                           # what follows is the heredoc body, not commands
            break


def _move_args(args: list[str]) -> list[str] | None:
    """The words of the cd, pushd or popd a command runs, without the `builtin` or `command` bash lets in front of it
    (`builtin cd /etc` is the cd itself), or None when the command doesn't change directory. `command -v cd` only
    prints where cd is."""
    while args and args[0] in ("builtin", "command"):
        prefix, args = args[0], args[1:]
        while prefix == "command" and args and args[0].startswith("-"):
            if args[0] in ("-v", "-V"):
                return None
            args = args[1:]                                          # command -p cd ..., command -- cd ...: still runs it
    return args if args and os.path.basename(args[0]) in _MOVES else None


def _moves(args: list[str]) -> bool:
    return _move_args(args) is not None


def shell_accesses_moved(command: str) -> list[tuple[str, str, bool]]:
    """(op, path, moved) for each path a Bash command plainly names. `moved` is True when a cd, pushd or popd came
    before it, so a relative path no longer starts from the call's working directory."""
    if not isinstance(command, str) or not command.strip() or len(command) > MAX_COMMAND_CHARS:
        return []
    found: list[tuple[str, str, bool]] = []
    moved = False
    for args, accesses in _parsed(command):
        found += [(op, path, moved) for op, path in accesses]
        moved = moved or _moves(args)
    seen: dict[tuple[str, str], bool] = {}
    for op, path, m in found:
        seen.setdefault((op, path), m)
    return [(op, path, m) for (op, path), m in seen.items()]


def _after_move(args: list[str], here: str | None) -> str | None:
    """The directory a cd, or a pushd to a directory, leaves the shell in, or None when that can't be read off the text
    (cd -, cd $X, pushd with no directory or with +N / -n, popd)."""
    args = _move_args(args) or []
    name = os.path.basename(args[0]) if args else ""
    if name == "pushd":
        rest = args[1:]
        if len(rest) != 1 or rest[0].startswith(("+", "-")):
            return None
    elif name == "cd":
        rest = [a for a in args[1:] if a not in ("-L", "-P", "--")]
        if not rest:
            return os.path.expanduser("~")
    else:
        return None
    if len(rest) > 1 or rest[0] == "-" or not _literal(rest[0]):
        return None
    target = os.path.expanduser(rest[0])
    if os.path.isabs(target):
        return os.path.normpath(target)
    return os.path.normpath(os.path.join(here, target)) if here else None


def shell_accesses_in(command: str, cwd: str | None) -> list[tuple[str, str]]:
    """(op, path) for each path a shell command plainly names, a relative path made absolute against the directory it
    is used in: the call's cwd, or where a plain `cd DIR` before it went. After a cd that can't be followed, or with no
    cwd, a relative path stays as written. Unlike shell_accesses_moved there is no length cap: this is for judging,
    where a path past a cap would go unchecked."""
    if not isinstance(command, str) or not command.strip():
        return []
    here = cwd if cwd and os.path.isabs(cwd) else None
    found: list[tuple[str, str]] = []
    for args, accesses in _parsed(command):
        for op, path in accesses:
            p = os.path.expanduser(path)
            if not os.path.isabs(p) and here:
                p = os.path.normpath(os.path.join(here, p))
            found.append((op, p if os.path.isabs(p) else path))
        if _moves(args):
            here = _after_move(args, here)
    return list(dict.fromkeys(found))


# Paths inside other text, like the code of `python -c "open('/home/me/.ssh/id_rsa')"`: an absolute or ~ path that
# starts a word or follows a quote, bracket, `=` or comma, and any quoted string without spaces (a relative path in
# code). A URL's `//` follows a colon, which is not in the list.
_HOME_VAR = re.compile(r"\$(?:\{HOME\}|HOME\b)")
_PATH_IN_TEXT = re.compile(r"""(?:^|(?<=[\s'"`(\[{=,]))(~?/[^\s'"`;|&<>(){}\[\],]+)""")
_QUOTED = re.compile(r"""(['"])([^\s'"]+)\1""")


def path_literals(command: str, max_len: int = 4096) -> list[str]:
    """Every string in a command that may name a file, wherever it sits, as written. What is done to it is unknown, so
    the caller decides what that means. $HOME and ${HOME} read as ~."""
    if not isinstance(command, str):
        return []
    text = _HOME_VAR.sub("~", command)
    found = [m.group(1) for m in _PATH_IN_TEXT.finditer(text)] + [m.group(2) for m in _QUOTED.finditer(text)]
    return list(dict.fromkeys(p for p in found if len(p) <= max_len))
