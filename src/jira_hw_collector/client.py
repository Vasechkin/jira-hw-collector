"""REST-клиент Jira с полным и устойчивым обходом объектов оборудования."""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

from .config import JiraConfig


class JiraClient:
    def __init__(self, config: JiraConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"Authorization": f"Bearer {config.token}", "Accept": "application/json"})
        if config.verify is False:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        if config.proxy:
            self.session.proxies.update({"http": config.proxy, "https": config.proxy})

    def _url(self, path: str) -> str:
        return f"{self.config.base_url}{self.config.api_path}/{path.lstrip('/')}"

    def request(self, path: str, *, params: dict[str, Any] | None = None, allow: tuple[int, ...] = ()) -> requests.Response:
        last_error: Exception | None = None
        for attempt in range(4):
            try:
                response = self.session.get(
                    self._url(path), params=params, timeout=self.config.timeout_seconds, verify=self.config.verify
                )
                if response.status_code in allow:
                    return response
                if response.status_code == 429 or response.status_code >= 500:
                    response.raise_for_status()
                response.raise_for_status()
                return response
            except requests.RequestException as error:
                last_error = error
                if attempt == 3:
                    raise
                time.sleep(min(2 ** attempt, 5))
        assert last_error is not None
        raise last_error

    def json(self, path: str, *, params: dict[str, Any] | None = None, allow: tuple[int, ...] = ()) -> Any:
        response = self.request(path, params=params, allow=allow)
        if response.status_code in allow:
            return {"_unavailable": response.status_code}
        return response.json()

    def metadata(self, project_key: str) -> dict[str, Any]:
        return {
            "server_info": self.json("serverInfo"),
            "project": self.json(f"project/{project_key}"),
            "fields": self.json("field"),
            "statuses": self.json(f"project/{project_key}/statuses", allow=(403, 404)),
            "components": self.json(f"project/{project_key}/components", allow=(403, 404)),
            "versions": self.json(f"project/{project_key}/versions", allow=(403, 404)),
        }

    def iter_issue_keys(self, jql: str) -> Iterator[str]:
        start = 0
        while True:
            payload = self.json(
                "search",
                params={"jql": jql, "startAt": start, "maxResults": self.config.page_size, "fields": "key"},
            )
            issues = payload.get("issues") if isinstance(payload, dict) else None
            if not isinstance(issues, list):
                raise ValueError("Ответ Jira не содержит список issues")
            for issue in issues:
                if issue.get("key"):
                    yield str(issue["key"])
            start += len(issues)
            if not issues or start >= int(payload.get("total", start)):
                break

    def issue(self, key: str) -> dict[str, Any]:
        return self.json(
            f"issue/{key}",
            params={"fields": "*all", "expand": "names,schema,renderedFields,changelog,operations,editmeta"},
        )

    def paged_values(self, path: str, collection: str) -> list[dict[str, Any]] | dict[str, int]:
        start = 0
        values: list[dict[str, Any]] = []
        while True:
            payload = self.json(path, params={"startAt": start, "maxResults": self.config.page_size}, allow=(403, 404))
            if isinstance(payload, dict) and "_unavailable" in payload:
                return payload
            page = payload.get(collection, []) if isinstance(payload, dict) else []
            if not isinstance(page, list):
                return []
            values.extend(item for item in page if isinstance(item, dict))
            start += len(page)
            total = int(payload.get("total", start)) if isinstance(payload, dict) else start
            if not page or start >= total:
                return values

    def related(self, key: str, *, comments: bool, worklogs: bool, changelog: bool,
                remote_links: bool, properties: bool, watchers: bool, votes: bool) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if comments:
            result["comments"] = self.paged_values(f"issue/{key}/comment", "comments")
        if worklogs:
            result["worklogs"] = self.paged_values(f"issue/{key}/worklog", "worklogs")
        if changelog:
            result["changelog"] = self.paged_values(f"issue/{key}/changelog", "values")
        if remote_links:
            result["remote_links"] = self.json(f"issue/{key}/remotelink", allow=(403, 404))
        if properties:
            listing = self.json(f"issue/{key}/properties", allow=(403, 404))
            result["properties"] = listing
            if isinstance(listing, dict) and isinstance(listing.get("keys"), list):
                values = {}
                for item in listing["keys"]:
                    name = item.get("key") if isinstance(item, dict) else None
                    if name:
                        values[name] = self.json(f"issue/{key}/properties/{name}", allow=(403, 404))
                result["property_values"] = values
        if watchers:
            result["watchers"] = self.json(f"issue/{key}/watchers", allow=(403, 404))
        if votes:
            result["votes"] = self.json(f"issue/{key}/votes", allow=(403, 404))
        return result

    def download(self, url: str, target, limit: int) -> tuple[int, str]:
        if urlparse(url).netloc != urlparse(self.config.base_url).netloc:
            raise ValueError("Вложение размещено вне доверенного узла Jira")
        response = self.session.get(url, stream=True, timeout=self.config.timeout_seconds, verify=self.config.verify)
        response.raise_for_status()
        expected = int(response.headers.get("Content-Length", 0) or 0)
        if limit and expected > limit:
            raise ValueError(f"размер вложения {expected} превышает ограничение {limit}")
        written = 0
        import hashlib
        digest = hashlib.sha256()
        with target.open("wb") as handle:
            for block in response.iter_content(1024 * 1024):
                if not block:
                    continue
                written += len(block)
                if limit and written > limit:
                    raise ValueError(f"размер вложения превышает ограничение {limit}")
                handle.write(block)
                digest.update(block)
        target.chmod(0o600)
        return written, digest.hexdigest()
