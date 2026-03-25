from __future__ import annotations

import re
from pathlib import Path

from chaos.models import Role


ROLE_HEADER_RE = re.compile(r"^###\s+(\d{3})\s+(.+)$")
PLAYER_HEADER = "### 001 玩家"


def load_roles(doc_path: str | Path) -> list[Role]:
    path = Path(doc_path)
    lines = path.read_text(encoding="utf-8").splitlines()

    roles: list[Role] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line == PLAYER_HEADER:
            block, index = _collect_block(lines, index + 1)
            roles.append(_parse_player_block(block))
            continue

        match = ROLE_HEADER_RE.match(line)
        if not match:
            index += 1
            continue

        role_id, name = match.groups()
        block, index = _collect_block(lines, index + 1)
        roles.append(_parse_role_block(role_id, name, block))

    roles.sort(key=lambda role: role.role_id)
    return roles


def _collect_block(lines: list[str], start_index: int) -> tuple[list[str], int]:
    block: list[str] = []
    index = start_index
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("### ") or stripped.startswith("## "):
            break
        if stripped:
            block.append(stripped)
        index += 1
    return block, index


def _parse_player_block(block: list[str]) -> Role:
    fields = _parse_bullets(block)
    return Role(
        role_id="001",
        name="玩家",
        age_job="自定义",
        background=fields.get("背景", "背景待定"),
        public_persona="自定义",
        motive=fields.get("默认困境", "重大人生危机"),
        core_trait=fields.get("默认能力", "观察、试探、撒谎、记忆、布局"),
        secret="自定义",
        taboo="自定义",
        is_player=True,
    )


def _parse_role_block(role_id: str, name: str, block: list[str]) -> Role:
    fields = _parse_bullets(block)
    return Role(
        role_id=role_id,
        name=name,
        age_job=fields.get("年龄 / 职业", ""),
        background=fields.get("背景", ""),
        public_persona=fields.get("公开人格", ""),
        motive=fields.get("动机", ""),
        core_trait=fields.get("核心特征", ""),
        secret=fields.get("秘密", ""),
        taboo=fields.get("底线", ""),
    )


def _parse_bullets(block: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in block:
        if not line.startswith("- "):
            continue
        content = line[2:]
        if "：" not in content:
            continue
        key, value = content.split("：", 1)
        result[key.strip()] = value.strip()
    return result
