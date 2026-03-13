from __future__ import annotations

import json
from pathlib import Path

import nonebot_plugin_localstore as store


class AliasError(ValueError):
    pass


class AliasValidationError(AliasError):
    pass


class AliasConflictError(AliasError):
    def __init__(self, alias: str, owner_qq: int):
        self.alias = alias
        self.owner_qq = owner_qq
        super().__init__(f"Alias already in use: {alias}")


class AliasNotFoundError(AliasError):
    def __init__(self, alias: str):
        self.alias = alias
        super().__init__(f"Alias not found: {alias}")


def get_data_file() -> Path:
    return store.get_data_file(plugin_name="auto_ping", filename="aliases.json")


def normalize_alias(alias: str) -> str:
    normalized = str(alias).strip().casefold()
    if not normalized:
        raise AliasValidationError("Alias must not be empty.")
    if any(char.isspace() for char in normalized):
        raise AliasValidationError("Alias must be a single token.")
    return normalized


class AliasRegistry:
    def __init__(self, file_path: Path | None = None):
        self.file_path = file_path or get_data_file()
        self._alias_to_qq: dict[str, int] = {}
        self._aliases_by_qq: dict[int, tuple[str, ...]] = {}
        self.load()

    def load(self) -> None:
        alias_to_qq: dict[str, int] = {}
        aliases_by_qq: dict[int, tuple[str, ...]] = {}

        if self.file_path.exists():
            try:
                with self.file_path.open("r", encoding="utf-8") as handle:
                    data = json.load(handle)
            except Exception:
                data = {}
            alias_to_qq, aliases_by_qq = self._parse_data(data)

        self._alias_to_qq = alias_to_qq
        self._aliases_by_qq = aliases_by_qq

    def _parse_data(self, data: object) -> tuple[dict[str, int], dict[int, tuple[str, ...]]]:
        alias_to_qq: dict[str, int] = {}
        aliases_by_qq: dict[int, tuple[str, ...]] = {}

        if not isinstance(data, dict):
            return alias_to_qq, aliases_by_qq

        targets = data.get("targets")
        if not isinstance(targets, dict):
            return alias_to_qq, aliases_by_qq

        for qq_text, aliases in targets.items():
            try:
                qq = int(str(qq_text))
            except (TypeError, ValueError):
                continue

            if not isinstance(aliases, list):
                continue

            normalized_aliases: list[str] = []
            for alias in aliases:
                if not isinstance(alias, str):
                    continue
                try:
                    normalized = normalize_alias(alias)
                except AliasValidationError:
                    continue
                if normalized in alias_to_qq:
                    continue
                alias_to_qq[normalized] = qq
                normalized_aliases.append(normalized)

            if normalized_aliases:
                aliases_by_qq[qq] = tuple(sorted(set(normalized_aliases)))

        return alias_to_qq, aliases_by_qq

    def _save(self) -> None:
        payload = {
            "targets": {
                str(qq): list(aliases)
                for qq, aliases in sorted(self._aliases_by_qq.items())
            }
        }
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        with self.file_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)

    def all_targets(self) -> dict[int, tuple[str, ...]]:
        return dict(self._aliases_by_qq)

    def iter_targets(self) -> list[tuple[int, tuple[str, ...]]]:
        return list(sorted(self._aliases_by_qq.items()))

    def get_alias_owner(self, alias: str) -> int | None:
        try:
            normalized = normalize_alias(alias)
        except AliasValidationError:
            return None
        return self._alias_to_qq.get(normalized)

    def get_aliases(self, qq: int) -> tuple[str, ...]:
        return self._aliases_by_qq.get(int(qq), tuple())

    def add_alias(self, qq: int, alias: str) -> None:
        normalized = normalize_alias(alias)
        owner_qq = self._alias_to_qq.get(normalized)
        if owner_qq is not None:
            raise AliasConflictError(normalized, owner_qq)

        qq = int(qq)
        aliases_by_qq = dict(self._aliases_by_qq)
        existing_aliases = set(aliases_by_qq.get(qq, tuple()))
        existing_aliases.add(normalized)
        aliases_by_qq[qq] = tuple(sorted(existing_aliases))

        alias_to_qq = dict(self._alias_to_qq)
        alias_to_qq[normalized] = qq

        self._aliases_by_qq = aliases_by_qq
        self._alias_to_qq = alias_to_qq
        self._save()

    def remove_alias(self, alias: str) -> int:
        normalized = normalize_alias(alias)
        owner_qq = self._alias_to_qq.get(normalized)
        if owner_qq is None:
            raise AliasNotFoundError(normalized)

        aliases_by_qq = dict(self._aliases_by_qq)
        remaining_aliases = set(aliases_by_qq.get(owner_qq, tuple()))
        remaining_aliases.discard(normalized)
        if remaining_aliases:
            aliases_by_qq[owner_qq] = tuple(sorted(remaining_aliases))
        else:
            aliases_by_qq.pop(owner_qq, None)

        alias_to_qq = dict(self._alias_to_qq)
        alias_to_qq.pop(normalized, None)

        self._aliases_by_qq = aliases_by_qq
        self._alias_to_qq = alias_to_qq
        self._save()
        return owner_qq

    def match_targets(self, plain_text: str) -> set[int]:
        if not plain_text or not self._alias_to_qq:
            return set()

        normalized_text = plain_text.casefold()
        return {
            qq
            for alias, qq in self._alias_to_qq.items()
            if alias and alias in normalized_text
        }
