"""Persistent shell session tool.

Adapted from strands-agents/devtools. Provides a long-lived shell process that
preserves state (cwd, env vars, background processes) across invocations —
unlike the default strands_tools.shell which spawns a fresh subprocess each time.
"""

import os
import subprocess
import time
import threading
import weakref
from strands import tool
from strands.types.tools import ToolContext


# Module-level session registry with automatic cleanup when agents are GC'd
_sessions = weakref.WeakKeyDictionary()


class ShellSession:
    """Manages a persistent shell process using plain pipes.

    Architecture:
    - One long-lived shell process per session
    - stderr merged into stdout for simplified stream handling
    - Single long-lived reader thread (not per-command threads)
    - Binary mode with manual decode to avoid text buffering issues
    - Buffer offset tracking for clean per-command output extraction
    - Single-flight execution with lock to prevent command interleaving
    """

    def __init__(self, timeout: int = 30):
        self._timeout = timeout
        self._process = None
        self._alive = False

        # Single-flight execution lock
        self._run_lock = threading.Lock()

        # Shared output buffer with synchronization
        self._output_buffer = bytearray()
        self._buffer_lock = threading.Lock()
        self._buffer_condition = threading.Condition(self._buffer_lock)

        # Reader thread
        self._reader_thread = None
        self._stop_reader = False

        self._start_process()

    def __del__(self):
        """Ensure OS processes and threads are cleaned up if the object is garbage collected."""
        try:
            self.stop()
        except Exception:
            pass

    def _start_process(self):
        """Start the shell process with clean configuration."""
        # default to bash
        shell = os.environ.get("SHELL", "/bin/bash")

        # Configure shell for clean startup (no rc files)
        if shell.endswith("bash"):
            argv = [shell, "--noprofile", "--norc"]
        elif shell.endswith("zsh"):
            argv = [shell, "-f"]
        else:
            argv = [shell]

        # Start process with merged stderr->stdout, binary mode
        self._process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Merge stderr into stdout
            env={**os.environ, "PS1": "", "PS2": "", "PROMPT": ""},
        )

        self._alive = True
        self._stop_reader = False

        # Start long-lived reader thread
        self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader_thread.start()

    def _reader_loop(self):
        """Long-lived reader thread that continuously reads from stdout."""
        READ_CHUNK_SIZE = 4096
        try:
            fd = self._process.stdout.fileno()
            while not self._stop_reader and self._process and self._process.poll() is None:
                chunk = os.read(fd, READ_CHUNK_SIZE)
                if not chunk:
                    break

                with self._buffer_condition:
                    self._output_buffer.extend(chunk)
                    self._buffer_condition.notify_all()
        except Exception:
            pass
        finally:
            with self._buffer_condition:
                self._alive = False
                self._buffer_condition.notify_all()

    def run(self, command: str, timeout: int | None = None) -> str:
        """Execute a command in the persistent session."""
        with self._run_lock:
            if not self._alive or not self._process or self._process.poll() is not None:
                raise Exception("Shell session is not running")

            effective_timeout = timeout if timeout is not None else self._timeout

            # Generate unique sentinel hash
            hash = f"{time.time_ns()}_{os.urandom(4).hex()}"
            sentinel = f"__CMD_DONE__:{hash}:"

            with self._buffer_lock:
                start_offset = len(self._output_buffer)

            try:
                wrapped_command = (
                    f"{command}\n"
                    f"__EXIT_CODE=$?\n"
                    f"printf '\\n{sentinel}%s\\n' \"$__EXIT_CODE\"\n"
                )
                self._process.stdin.write(wrapped_command.encode("utf-8"))
                self._process.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                self._alive = False
                raise Exception(f"Failed to write to shell: {e}")

            deadline = time.time() + effective_timeout
            sentinel_bytes = sentinel.encode("utf-8")

            while True:
                with self._buffer_condition:
                    buffer_view = bytes(self._output_buffer[start_offset:])
                    if sentinel_bytes in buffer_view:
                        output = buffer_view.decode("utf-8", errors="replace")
                        break

                    remaining = deadline - time.time()
                    if remaining <= 0:
                        self.stop()
                        raise TimeoutError(
                            f"Command timed out after {effective_timeout} seconds"
                        )

                    if not self._alive:
                        raise Exception("Shell process died unexpectedly")

                    self._buffer_condition.wait(timeout=min(remaining, 0.1))

            # Prune buffer to prevent memory leaks
            with self._buffer_lock:
                sentinel_idx = self._output_buffer.find(sentinel_bytes, start_offset)
                if sentinel_idx != -1:
                    nl_idx = self._output_buffer.find(b"\n", sentinel_idx)
                    if nl_idx != -1:
                        del self._output_buffer[: nl_idx + 1]
                    else:
                        del self._output_buffer[: sentinel_idx + len(sentinel_bytes)]

            # Parse output and extract exit code
            exit_code = -1
            lines = output.split("\n")
            filtered_lines = []

            for line in lines:
                if sentinel in line:
                    parts = line.split(":")
                    if len(parts) >= 3:
                        try:
                            exit_code = int(parts[2])
                        except ValueError:
                            pass
                    continue
                filtered_lines.append(line)

            output = "\n".join(filtered_lines).strip()

            if exit_code != 0:
                output += f"\n\nExit code: {exit_code}"

            return output

    def stop(self):
        """Stop the shell process and reader thread."""
        self._stop_reader = True
        self._alive = False

        if self._process:
            self._process.terminate()
            try:
                self._process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self._process = None

        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1)

    def restart(self):
        """Restart the shell session."""
        self.stop()
        self._output_buffer.clear()
        self._start_process()


@tool(context=True)
def shell_tool(
    command: str,
    timeout: int | None = None,
    restart: bool = False,
    tool_context: ToolContext = None,
) -> str:
    """Execute a shell command in a persistent shell session.

    The shell session preserves state across commands:
    - Working directory (cd persists)
    - Exported environment variables
    - Background processes (& jobs stay alive between calls)

    Args:
        command: The shell command to execute.
        timeout: Optional timeout in seconds (default: 30).
        restart: If True, restart the shell session before running the command.

    Returns:
        The command output, with exit code appended if non-zero.
    """
    agent = tool_context.agent

    if restart and (not command or command.strip() == ""):
        if agent in _sessions:
            _sessions[agent].stop()
        _sessions[agent] = ShellSession()
        return "Shell session restarted"

    if restart:
        if agent in _sessions:
            _sessions[agent].stop()
        _sessions[agent] = ShellSession()

    if agent not in _sessions:
        _sessions[agent] = ShellSession()

    session = _sessions[agent]

    try:
        return session.run(command, timeout=timeout)
    except TimeoutError as e:
        return f"Error: {str(e)}"
    except Exception as e:
        if session._process is None or session._process.poll() is not None:
            session.stop()
            _sessions[agent] = ShellSession()
        return f"Error: {str(e)}"
