"""Источник OpenDota: pull-режим с пагинацией по match_seq_num (Гл. 3.3)."""
from __future__ import annotations

from typing import Iterable

import requests

from . import MatchRef


class OpenDotaSource:
    name = "opendota"

    def __init__(self, base_url: str = "https://api.opendota.com/api",
                 page_size: int = 100, timeout: float = 10.0) -> None:
        self._base = base_url.rstrip("/")
        self._page_size = page_size
        self._timeout = timeout

    def fetch_new(self, after_cursor: str | None) -> Iterable[MatchRef]:
        params: dict[str, str] = {}
        if after_cursor:
            params["greater_than_match_id"] = after_cursor
        resp = requests.get(f"{self._base}/proMatches", params=params,
                            timeout=self._timeout)
        resp.raise_for_status()
        rows = sorted(resp.json(), key=lambda r: r["match_id"])
        for row in rows[: self._page_size]:
            match_id = int(row["match_id"])
            # URL реплея восстанавливается по cluster/replay_salt, когда они
            # доступны; иначе матч пропускается до появления salt.
            cluster = row.get("cluster")
            salt = row.get("replay_salt")
            if not cluster or not salt:
                continue
            yield MatchRef(
                match_id=match_id,
                replay_url=(
                    f"http://replay{cluster}.valve.net/570/"
                    f"{match_id}_{salt}.dem.bz2"
                ),
                tier="Professional",
                source_cursor=str(match_id),
            )

    def download_replay(self, ref: MatchRef) -> bytes:
        resp = requests.get(ref.replay_url, timeout=60.0)
        resp.raise_for_status()
        return resp.content
