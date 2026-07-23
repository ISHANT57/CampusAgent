"""Tool package.

Importing this module registers every built-in tool as a side effect. That is
why it exists: the registry is populated by import, so `from app.tools import
registry` is enough — no discovery, no plugin scan, no config file.

Adding a tool = one new module + one import line here.

Note there is no `final_answer` tool. The loop (M23) recognises completion when
the model stops requesting tools and returns substantive text, rather than
requiring a terminal tool call. See app/agent/loop.py for how "stopped
requesting tools" is distinguished from M0's NO_CALL failure class.
"""

from app.tools.registry import registry

# Imported for side effects: each module's @registry.register decorator runs.
from app.tools import calculator  # noqa: F401,E402
from app.tools import knowledge  # noqa: F401,E402
from app.tools import web_read  # noqa: F401,E402
from app.tools import web_search  # noqa: F401,E402

__all__ = ["registry"]
