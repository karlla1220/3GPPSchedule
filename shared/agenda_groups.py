"""The legend group of a cell: its top agenda item (CLAUDE.md "셀 구조").

Every WG but RAN1 colours a cell by the top item of its first agenda item;
RAN1 keeps the schedule's own group heads.
"""


def group_label(item: str | None, descriptions: dict) -> str:
    """"NR Rel-20" for 8.2 when the agenda says "8 | NR Rel-20 - ..."; "AI 8" without one."""
    if not item:
        return ''
    top = item.split('.')[0]
    description = descriptions.get(top)
    return description.split(' - ')[0].strip() if description else f'AI {top}'
