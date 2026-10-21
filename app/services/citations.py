"""Citation clean-up: whatever style the model writes, the user sees plain numbers like [1] [2].

Why this exists: models trained with a browsing tool (gpt-oss among them) tend to write citations such as
`【1†L1-L4】`, `[F ^1 L1-L4]` or `[Source 2]`. A prompt asking for "[1]" reduces that but cannot guarantee it,
so every answer passes through this filter on the server:

* every recognised citation style is rewritten to the canonical `[n]`
* numbers that do not match a real source are dropped (no invented references)
* the numbers that really appear are recorded, so the UI can mark which sources were used
* code blocks and inline code are left alone (`arr[1]` must stay `arr[1]`)

`CitationFilter` works on a token stream: it holds back a few characters when a citation may still be
arriving (e.g. the text so far ends in `[1†L`) and releases them as soon as the bracket closes.
"""
import re

OPEN = "[【"
CLOSE = {"[": "]", "【": "】"}
MAX_CITATION_LEN = 40  # longest bracket content we treat as a possible citation

_WORDS = r"(?:sources?|src|refs?|references?|docs?|documents?|passages?|context|files?|excerpts?|results?|web|f)"
_LINES = r"(?:\s*L\d+(?:\s*[-–]\s*L?\d+)?)*"  # "L1-L4" line ranges that browsing-style citations carry

# Each pattern is tried against the text *inside* the brackets.
_LIST = re.compile(r"^\s*(\d{1,3})(?:\s*(?:,|;|&|and)\s*(\d{1,3}))*\s*$", re.I)
_RANGE = re.compile(r"^\s*(\d{1,3})\s*[-–]\s*(\d{1,3})\s*$")
_DAGGER = re.compile(r"^\s*\^?\s*(\d{1,3})\s*†.*$")                        # 1†L1-L4
_LABELLED = re.compile(rf"^\s*{_WORDS}\s*[:#.]?\s*\^?\s*(\d{{1,3}}){_LINES}\s*$", re.I)  # Source 2, F ^1 L1-L4
_CARET = re.compile(rf"^\s*\^\s*(\d{{1,3}}){_LINES}\s*$")                  # ^1
_NUM_LINES = re.compile(rf"^\s*(\d{{1,3}}){_LINES}\s*$")                   # 1 L1-L4  /  plain 1
_ALL_NUMS = re.compile(r"\d{1,3}")


def _parse(content: str, bracket: str) -> list[int] | None:
    """Return the source numbers a bracket group refers to, or None if it is not a citation."""
    m = _RANGE.match(content)
    if m:
        a, b = int(m.group(1)), int(m.group(2))
        return list(range(a, b + 1)) if 0 < a <= b and b - a < 10 else None
    if _LIST.match(content):
        return [int(x) for x in _ALL_NUMS.findall(content)]
    for pat in (_DAGGER, _LABELLED, _CARET, _NUM_LINES):
        m = pat.match(content)
        if m:
            return [int(m.group(1))]
    if bracket == "【":  # this bracket is used by the browsing-style format and nowhere else
        nums = _ALL_NUMS.findall(content.split("†")[0])
        return [int(nums[0])] if nums else []
    return None


class CitationFilter:
    """Incrementally rewrite citations. Use `feed()` for each streamed piece and `flush()` at the end."""

    def __init__(self, n_sources: int):
        self.n = n_sources
        self.cited: list[int] = []  # valid source numbers in order of first use
        self.dropped = False        # True once an invalid citation was removed
        self._buf = ""
        self._fence = False   # inside ``` ... ```
        self._inline = False  # inside `...`

    # ---- public ----
    def feed(self, text: str) -> str:
        self._buf += text
        return self._process(final=False)

    def flush(self) -> str:
        out = self._process(final=True)
        self._buf = ""
        return out

    # ---- internals ----
    def _emit_citation(self, nums: list[int]) -> str:
        out = []
        for n in dict.fromkeys(nums):  # de-duplicate, keep order
            if 1 <= n <= self.n:
                out.append(f"[{n}]")
                if n not in self.cited:
                    self.cited.append(n)
            else:
                self.dropped = True
        return "".join(out)

    def _process(self, final: bool) -> str:
        b, out, i, L = self._buf, [], 0, len(self._buf)
        while i < L:
            ch = b[i]
            if ch == "`":
                run = len(b) - i - len(b[i:].lstrip("`"))  # length of this backtick run
                if run == len(b) - i and not final and run < 3:
                    break  # a run at the very end could still grow into a ``` fence
                if run >= 3:
                    self._fence = not self._fence
                    self._inline = False
                elif not self._fence:
                    self._inline = not self._inline
                out.append(b[i : i + run]); i += run
                continue
            if ch == "\n":
                self._inline = False  # inline code never spans lines
            if self._fence or self._inline or ch not in OPEN:
                out.append(ch); i += 1
                continue
            # ch is "[" or "【": find where it closes
            close = b.find(CLOSE[ch], i + 1, i + 2 + MAX_CITATION_LEN)
            nxt_open = min([p for p in (b.find("[", i + 1), b.find("【", i + 1)) if p != -1] or [L])
            if close == -1 or close > nxt_open:
                tail = b[i + 1 :]
                waiting = (len(tail) <= MAX_CITATION_LEN and "\n" not in tail and nxt_open == L)
                if waiting and not final:
                    break  # the bracket may still be closed by the next token
                if ch == "【" and final:
                    i = L  # drop a dangling, never-closed 【… fragment
                    break
                out.append(ch); i += 1
                continue
            nums = _parse(b[i + 1 : close], ch)
            if nums is None:
                out.append(ch); i += 1  # an ordinary bracket, e.g. a markdown link label
            else:
                out.append(self._emit_citation(nums)); i = close + 1
        self._buf = b[i:]
        return "".join(out)


def sanitize_citations(text: str, n_sources: int) -> tuple[str, list[int]]:
    """One-shot version for complete answers. Returns (clean_text, cited_numbers)."""
    f = CitationFilter(n_sources)
    clean = f.feed(text) + f.flush()
    return (_tidy(clean) if f.dropped else clean), f.cited


_SPACE_BEFORE_PUNCT = re.compile(r"[ \t]+([.,;:!?])")


def _tidy(text: str) -> str:
    """Remove the stray space a deleted citation can leave behind ("word 【9】." -> "word.")."""
    return _SPACE_BEFORE_PUNCT.sub(r"\1", text)


_STRIP = re.compile(r"\s?\[\d{1,3}\](?:\s?\[\d{1,3}\])*")


def strip_citations(text: str) -> str:
    """Remove [n] markers. Used on past answers before they go back to the model as history:
    the numbers belonged to that turn's sources and would be wrong for the next turn."""
    return _STRIP.sub("", text)