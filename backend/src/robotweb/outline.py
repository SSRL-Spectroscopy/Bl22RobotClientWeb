"""The steps of a sequence as a tree: sections that hold lines and further
sections.

Used twice:

- Before the start, the sequence is run "dry" (sequence.py): nothing is sent,
  but every section and line it would go through is recorded here. That is the
  plan the operator reads.
- During the run, the same calls tick the planned tree off: a section or line
  the run enters is looked up among the planned ones that follow, and marked
  as running, then done, with its start and end time. What the run does that
  was not planned is inserted where it happens; planned steps the run passes
  over are marked "skipped". So the tree always shows what really happened.

States: planned, running, done, failed, stopped (aborted), skipped.
"""

import time
from collections.abc import Callable
from itertools import count


def _new(node_id: int, kind: str, key: str, title: str) -> dict:
    return {
        "id": node_id, "kind": kind, "key": key, "title": title, "state": "planned",
        "started": None, "finished": None, "children": [], "cursor": 0,
    }


class Outline:
    def __init__(self, changed: Callable[[], None] = lambda: None, clock: Callable[[], float] = time.time) -> None:
        self._changed = changed
        self._clock = clock
        self._ids = count(1)
        self.root = _new(0, "section", "root", "")
        self._stack = [self.root]

    # ---- recording --------------------------------------------------------

    def enter(self, key: str, title: str) -> None:
        """Start a section inside the current one."""
        self._stack.append(self._take("section", key, title))
        self._changed()

    def leave(self, state: str = "done") -> None:
        node = self._stack.pop()
        self._close(node, state)
        self._changed()

    def begin(self, title: str) -> dict:
        """Start a line in the current section. Pass the result to end()."""
        node = self._take("line", title, title)
        self._changed()
        return node

    def end(self, node: dict, state: str = "done") -> None:
        node["state"] = state
        node["finished"] = self._clock()
        self._changed()

    def finish(self) -> None:
        """The run completed: whatever is still planned was not needed."""
        self._close(self.root, "done")
        self._changed()

    def _take(self, kind: str, key: str, title: str) -> dict:
        parent = self._stack[-1]
        children, cursor = parent["children"], parent["cursor"]
        for index in range(cursor, len(children)):
            node = children[index]
            if node["kind"] == kind and node["key"] == key and node["state"] == "planned":
                for passed in children[cursor:index]:
                    self._skip(passed)
                break
        else:  # not planned: it goes where the run is now
            index = cursor
            node = _new(next(self._ids), kind, key, title)
            children.insert(index, node)
        node["title"] = title
        node["state"] = "running"
        node["started"] = self._clock()
        parent["cursor"] = index + 1
        return node

    def _close(self, node: dict, state: str) -> None:
        for child in node["children"]:
            if child["state"] == "running":
                self._close(child, state)
            elif child["state"] == "planned" and state == "done":
                self._skip(child)
        node["state"] = state
        node["finished"] = self._clock()

    def _skip(self, node: dict) -> None:
        if node["state"] == "planned":
            node["state"] = "skipped"
            for child in node["children"]:
                self._skip(child)

    # ---- reading ------------------------------------------------------------

    def planned(self) -> list[dict]:
        """What was recorded, as a plan: every state back to "planned"."""

        def copy(node: dict) -> dict:
            return {**_new(node["id"], node["kind"], node["key"], node["title"]),
                    "children": [copy(child) for child in node["children"]]}

        return [copy(child) for child in self.root["children"]]

    def load(self, plan: list[dict]) -> None:
        """Start from a plan made by planned()."""

        def copy(node: dict) -> dict:
            return {**node, "children": [copy(child) for child in node["children"]]}

        self.root = _new(0, "section", "root", "")
        self.root["children"] = [copy(node) for node in plan]
        self._stack = [self.root]
        self._ids = count(1 + max((n["id"] for n in _walk(self.root["children"])), default=0))
        self._changed()

    def public(self) -> list[dict]:
        return public(self.root["children"])


def public(nodes: list[dict]) -> list[dict]:
    """The tree as sent to the browsers."""
    return [
        {
            "id": node["id"], "kind": node["kind"], "title": node["title"], "state": node["state"],
            "started": node["started"], "finished": node["finished"], "children": public(node["children"]),
        }
        for node in nodes
    ]


def flat(nodes: list[dict], depth: int = 0) -> list[str]:
    """The titles, one per line, indented by two spaces per level."""
    lines = []
    for node in nodes:
        lines.append("  " * depth + node["title"])
        lines += flat(node["children"], depth + 1)
    return lines


def _walk(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from _walk(node["children"])
