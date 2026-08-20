"""Sentences split utilities.

Bracket protection, CJK punctuation handling, and markdown code/table detection.
"""
import re
from typing import List

# CJK sentence-ending punctuation + ~ + newline as sentence boundary
SENTENCE_DELIMITERS = set('。！？!?；;~\n～')

# Bracket pairs that protect inner sentence delimiters from splitting
_BRACKET_PAIRS = {
    '「': '」', '『': '』',
    '《': '》', '【': '】',
    '（': '）',               # fullwidth CJK parentheses
    '(': ')', '[': ']', '{': '}',
}
_OPEN_BRACKETS = set(_BRACKET_PAIRS.keys())
_CLOSE_BRACKETS = set(_BRACKET_PAIRS.values())

# Characters absorbed into the preceding sentence after a delimiter.
# CJK close brackets + fullwidth paren + ASCII close brackets + markdown formatting
_CN_CLOSE_BRACKETS = set('）」』》】）)]}*_')

# Regex for markdown code fences: ```...``` or ~~~...~~~
_CODE_FENCE = re.compile(r'```[^\n]*\n.*?```|~~~[^\n]*\n.*?~~~', re.DOTALL)
# Regex for inline code: `...`
_INLINE_CODE = re.compile(r'`[^`]+`')
# Regex for table rows (lines starting/ending with |)
_TABLE_ROW = re.compile(r'^\s*\|.+\|\s*$')


def split_full_text(text: str) -> List[str]:
    """Split full text into sentences, respecting bracket nesting.

    Code blocks (```), tables (|...|), and inline code (`...`) are kept intact
    as atomic sentences and prefixed with [MD] marker for TTS suppression.
    """
    # Phase 1: extract protected blocks → placeholders
    protected: List[str] = []
    placeholder = '\x00'

    def _protect(match):
        protected.append(match.group(0))
        return f'\n{placeholder}{len(protected) - 1}\n'

    text = _CODE_FENCE.sub(_protect, text)
    # Table detection: consecutive table rows
    lines = text.split('\n')
    new_lines = []
    i = 0
    while i < len(lines):
        if _TABLE_ROW.match(lines[i]):
            start = i
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                i += 1
            table_block = '\n'.join(lines[start:i])
            protected.append(table_block)
            new_lines.append(f'{placeholder}{len(protected) - 1}')
        else:
            new_lines.append(lines[i])
            i += 1
    text = '\n'.join(new_lines)

    # Phase 2: split remaining text
    sentences: List[str] = []
    last = 0
    depth = 0
    i = 0
    while i < len(text):
        ch = text[i]
        if ch in _OPEN_BRACKETS:
            depth += 1
        elif ch in _CLOSE_BRACKETS:
            depth = max(0, depth - 1)
        elif ch in SENTENCE_DELIMITERS and depth <= 0:
            # Don't split on placeholder lines
            if i > 0 and text[i - 1] == placeholder:
                i += 1
                continue
            end = i + 1
            while end < len(text) and text[end] in _CN_CLOSE_BRACKETS:
                end += 1
            raw = text[last:end].rstrip()
            if raw:
                sentences.append(raw)
            last = end
            i = end - 1
        i += 1
    remaining = text[last:].rstrip()
    if remaining:
        sentences.append(remaining)

    # Phase 3: restore protected blocks, prefix with [MD] marker
    result = []
    for sent in sentences:
        # Check if sentence is a placeholder
        m = re.match(r'^\x00(\d+)$', sent.strip())
        if m:
            idx = int(m.group(1))
            result.append(f'[MD]{protected[idx]}')
        else:
            # May contain placeholders inline
            def _restore(m2):
                idx2 = int(m2.group(1))
                return protected[idx2]
            sent = re.sub(r'\x00(\d+)', _restore, sent)
            result.append(sent)
    return result
