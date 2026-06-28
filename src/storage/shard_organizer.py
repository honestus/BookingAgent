from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class ShardOrganizer(ABC):

    @property
    @abstractmethod
    def files(self) -> tuple[Path, ...]:
        """All known shard files, sorted."""
        raise NotImplementedError

    @property
    @abstractmethod
    def last_file(self) -> Path | None:
        """Last shard file, or None if no shard exists."""
        raise NotImplementedError

    @abstractmethod
    def contains(self, filepath: Path) -> bool:
        """Return True if filepath is among this shard's files."""
        raise NotImplementedError

    @abstractmethod
    def get_files_after(self, filepath: Path, inclusive: bool = True) -> tuple[Path, ...]:
        """Return all shard files after filepath."""
        raise NotImplementedError

    @abstractmethod
    def get_files_before(self, filepath: Path, inclusive: bool = True) -> tuple[Path, ...]:
        """Return all shard files before filepath."""
        raise NotImplementedError

    @abstractmethod
    def create_next_file(self) -> Path:
        """
        Create the next shard file on disk, update internal state,
        and return its path.
        """
        raise NotImplementedError

    @abstractmethod
    def build_state_from_disk(self) -> None:
        """
        Rebuild internal state from disk.
        """
        raise NotImplementedError
        
    @abstractmethod
    def _get_file_idx(self, filepath: Path) -> int:
        """
        Returns the index of the input filepath among the shards' files
        """
        raise NotImplementedError
        
    def get_files_after(self, filepath: Path, inclusive: bool = True) -> tuple[Path, ...]:
        idx = self._get_file_idx(filepath)
        if not inclusive:
            idx += 1

        return tuple(self._files[idx:])

    def get_files_before(self, filepath: Path, inclusive: bool = True) -> tuple[Path, ...]:
        idx = self._get_file_idx(filepath)
        if inclusive:
            idx += 1

        return tuple(self._files[:idx])    
    


class IntShardOrganizer(ShardOrganizer):

    def __init__(self, dirpath: Path, file_stem: str, suffix: str) -> None:
        import re

        self._dirpath = Path(dirpath)
        self._file_stem = file_stem
        self._suffix = suffix
        self._files: list[Path] = []
        self._normalized_files_to_idx: dict[Path, int] = {}
        self._last_file: Path | None = None
        self._last_shard_n: int = -1
        self._regex = re.compile(
            rf"^{re.escape(file_stem)}_(\d+){re.escape(suffix)}$"
        )

        self.build_state_from_disk()

    @property
    def files(self) -> tuple[Path, ...]:
        return tuple(self._files)

    @property
    def last_file(self) -> Path | None:
        return self._last_file

    def contains(self, filepath: Path) -> bool:
        return self._normalize_filepath(filepath) in self._normalized_files_to_idx

    

    def create_next_file(self) -> Path:
        """
        Create the next shard file and update in-memory state.
        """

        next_n = self._last_shard_n + 1
        filepath = self._dirpath / f"{self._file_stem}_{next_n}{self._suffix}"

        if filepath.exists():
            raise FileExistsError(f"Shard already exists: {filepath}")

        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.touch()

        self._files.append(filepath)
        self._normalized_files_to_idx[self._normalize_filepath(filepath)] = len(self._files) - 1

        self._last_file = filepath
        self._last_shard_n = next_n

        return filepath

    def build_state_from_disk(self) -> None:
        """
        Recompute shard state from disk.
        """

        self._dirpath.mkdir(parents=True, exist_ok=True)
        files: list[tuple[int, Path]] = []

        for filepath in self._dirpath.iterdir():
            if not filepath.is_file():
                continue

            match = self._regex.match(filepath.name)
            if match is None:
                continue

            shard_n = int(match.group(1))
            files.append((shard_n, filepath))

        files.sort(key=lambda x: x[0])
        self._files = [filepath for _, filepath in files]
        self._normalized_files_to_idx = {
            self._normalize_filepath(filepath): idx
            for idx, filepath in enumerate(self._files)
        }

        if files:
            self._last_shard_n = files[-1][0]
            self._last_file = files[-1][1]
        else:
            self._last_shard_n = -1
            self._last_file = None

    def _normalize_filepath(self, filepath: Path) -> Path:
        return Path(filepath).resolve()

    def _get_file_idx(self, filepath: Path) -> int:
        normalized_filepath = self._normalize_filepath(filepath)
        try:
            return self._normalized_files_to_idx[normalized_filepath]
        except KeyError:
            raise ValueError(f"File '{filepath}' is not managed by this organizer.") from None