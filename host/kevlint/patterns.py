"""Group violations that involve the same waiter, holder and middle thread."""
from dataclasses import dataclass


@dataclass
class Pattern:
    waiter: tuple
    holder: tuple
    middle: tuple
    occurrences: list

    @property
    def worst(self):
        return max(self.occurrences, key=lambda v: v.duration_cycles)


def group_patterns(violations):
    """Return patterns, worst block first; occurrences stay in trace order."""
    groups = {}
    for violation in violations:
        key = (violation.waiter, violation.holder, violation.middle)
        groups.setdefault(key, []).append(violation)
    patterns = [Pattern(*key, occurrences) for key, occurrences in groups.items()]
    return sorted(patterns, key=lambda p: p.worst.duration_cycles, reverse=True)
