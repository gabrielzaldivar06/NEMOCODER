from __future__ import annotations

import re

COMPACTION_THRESHOLD_CHARS = 12_000  # ~3K tokens
TOOL_OUTPUT_MAX_CHARS = 2_000


def estimate_token_count(text: str) -> int:
    """Rough 4-chars-per-token estimate."""
    return len(text) // 4


def should_compact(context: str, threshold: int = COMPACTION_THRESHOLD_CHARS) -> bool:
    """Return True when context exceeds the character threshold."""
    return len(context) > threshold


def prune_tool_output(output: str, max_chars: int = TOOL_OUTPUT_MAX_CHARS) -> str:
    """Truncate a single tool output, preserving head and tail."""
    if len(output) <= max_chars:
        return output
    
    half = max_chars // 2
    return f"{output[:half]}\n... [truncated {len(output) - max_chars} chars] ...\n{output[-half:]}"


def compact_context(context: str, *, max_chars: int = COMPACTION_THRESHOLD_CHARS) -> str:
    """
    Mechanically truncate context while preserving important sections.
    
    Strategy:
    1. Identify sections separated by double newlines or headers.
    2. Keep the first section (Objective/Context header) intact.
    3. Keep the most recent section (Latest validation/evidence) intact.
    4. Truncate middle sections (older validation attempts) if necessary.
    """
    if len(context) <= max_chars:
        return context

    # Split by common headers in NEMOCODE context
    sections = re.split(r'\n(?=# )', context)
    if len(sections) < 2:
        # Fallback to simple truncation if no headers found
        return context[:max_chars//2] + "\n... [context compacted] ...\n" + context[-max_chars//2:]

    # We want to keep the first section and the last section as priorities
    first = sections[0]
    last = sections[-1]
    middle = sections[1:-1]
    
    # Target size for middle section is what's left
    target_middle_size = max_chars - len(first) - len(last) - 100 # buffer
    
    if target_middle_size <= 0:
        # Even first and last don't fit well, prioritize last and start of first
        return first[:500] + "\n... [context compacted] ...\n" + last[-max_chars+500:]

    # Prune middle sections
    new_middle = []
    current_size = 0
    
    # Go backwards through middle sections to keep more recent ones
    for section in reversed(middle):
        pruned_section = section
        if "# Repair Evidence" in section or "# Previous Validation Output" in section:
             pruned_section = prune_tool_output(section, TOOL_OUTPUT_MAX_CHARS)
        
        if current_size + len(pruned_section) < target_middle_size:
            new_middle.insert(0, pruned_section)
            current_size += len(pruned_section)
        else:
            # Skip older sections if we run out of space
            continue
            
    return "\n".join([first] + new_middle + [last])
