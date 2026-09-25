"""Replay a raw PTY capture onto a virtual screen, so the layout can be read as text.

This is a **reader** for the evidence, not part of the product: it takes the byte log a real
session wrote and applies the escape sequences the way a terminal would (CUP, CUU/CUD,
erase-to-end, SGR stripped, CR/LF, and scrolling at the bottom row), then prints the screen.
What you see is what the terminal showed.

Usage:
    .venv/bin/python tools/chat_screen_replay.py outputs/chat-ux/c1-first-paint-80x24.raw 80 24
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

CSI = re.compile(r"\x1b\[([0-9;]*)([A-Za-z])")


class Screen:
    """The smallest terminal that can show a frame: rows, a cursor, and scrolling."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.rows = [[" "] * width for _ in range(height)]
        self.row = 0
        self.column = 0

    def scroll(self) -> None:
        self.rows.pop(0)
        self.rows.append([" "] * self.width)

    def newline(self) -> None:
        if self.row >= self.height - 1:
            self.scroll()
        else:
            self.row += 1

    def put(self, char: str) -> None:
        if self.column >= self.width:
            self.column = 0
            self.newline()
        self.rows[self.row][self.column] = char
        self.column += 1

    def erase_below(self) -> None:
        for x in range(self.column, self.width):
            self.rows[self.row][x] = " "
        for y in range(self.row + 1, self.height):
            self.rows[y] = [" "] * self.width

    def erase_all(self) -> None:
        self.rows = [[" "] * self.width for _ in range(self.height)]

    def lines(self) -> list[str]:
        return ["".join(line).rstrip() for line in self.rows]


def replay(data: bytes, width: int, height: int) -> list[str]:
    """Apply the capture to a screen of the given geometry and return its rows."""
    screen = Screen(width, height)
    text = data.decode("utf-8", "replace")
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\x1b":
            match = CSI.match(text, index)
            if match:
                params = [int(p) if p else 0 for p in match.group(1).split(";") if p != ""]
                verb = match.group(2)
                first = params[0] if params else 0
                if verb == "F":
                    for _ in range(max(1, first)):
                        if screen.row > 0:
                            screen.row -= 1
                    screen.column = 0
                elif verb == "A":
                    screen.row = max(0, screen.row - max(1, first))
                elif verb == "B":
                    screen.row = min(height - 1, screen.row + max(1, first))
                elif verb == "C":
                    screen.column = min(width - 1, screen.column + max(1, first))
                elif verb == "H":
                    screen.row = max(0, min(height - 1, (params[0] if params else 1) - 1))
                    screen.column = max(0, min(width - 1,
                                               (params[1] if len(params) > 1 else 1) - 1))
                elif verb == "J":
                    screen.erase_all() if first == 2 else screen.erase_below()
                elif verb == "K":
                    for x in range(screen.column, width):
                        screen.rows[screen.row][x] = " "
                index = match.end()
                continue
            index += 1
            continue
        if char == "\r":
            screen.column = 0
        elif char == "\n":
            screen.newline()
        elif char == "\x08":
            screen.column = max(0, screen.column - 1)
        elif ord(char) > 31:
            screen.put(char)
        index += 1
    return screen.lines()


def main(argv: list[str]) -> int:
    path = Path(argv[1])
    width = int(argv[2]) if len(argv) > 2 else 80
    height = int(argv[3]) if len(argv) > 3 else 24
    print(f"--- {path.name} replayed at {width}x{height} ---")
    for line in replay(path.read_bytes(), width, height):
        print("|" + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
