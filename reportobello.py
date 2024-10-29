from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Self, overload
from urllib.parse import quote
import os

from httpx import ASGITransport, AsyncClient, Response


DEFAULT_HOST = "https://reportobello.com"


class ReportobelloException(Exception):
    pass

class ReportobelloMissingApiKey(ReportobelloException):
    def __str__(self) -> str:
        return "REPORTOBELLO_API_KEY env var is not set. Set it, or pass API key directly"

class ReportobelloMissingTemplateName(ReportobelloException):
    pass

class ReportobelloReportBuildFailure(ReportobelloException):
    error: str

    def __init__(self, error: str) -> None:
        self.error = error

class ReportobelloTemplateNotFound(ReportobelloException):
    error: str

    def __init__(self, error: str) -> None:
        self.error = error

class ReportobelloFileTooBig(ReportobelloException):
    pass

class ReportobelloUnauthorized(ReportobelloException):
    error: str

    def __init__(self, error: str) -> None:
        self.error = error

class ReportobelloServerError(ReportobelloException):
    error: str

    def __init__(self, error: str) -> None:
        self.error = error


@dataclass(kw_only=True)
class Template:
    name: str = ""
    content: str | None = None
    file: Path | str | None = None
    version: int = -1

    # TODO: require exactly either content or file

    def __post_init__(self) -> None:
        if not self.name:
            self.name = self.__class__.name

        self.file = self.file or self.__class__.file
        self.content = self.content or self.__class__.content

        if not self.name:
            if self.__class__.__name__ == "Template":
                msg = 'Template name must be set. Use `Template(name="name here", ...)` to set it.'
            else:
                msg = 'Template name must be set. Add `name = "name here"` to your class to set it.'

            raise ReportobelloMissingTemplateName(msg)

    @classmethod
    def from_json(cls, data: Any) -> Self:
        return cls(content=data.pop("template"), **data)


class LazyPdf:
    url: str
    _api: ReportobelloApi

    def __init__(self, api: ReportobelloApi, url: str) -> None:
        self._api = api
        self.url = url

    async def save_to(self, path: Path | str) -> None:
        with open(path, "wb+") as f:
            resp = await self._api.get(self.url)

            f.write(await resp.aread())

    async def as_blob(self) -> bytes:
        resp = await self._api.get(self.url)

        return await resp.aread()


@dataclass(kw_only=True)
class Report:
    filename: str | None
    requested_version: int
    actual_version: int
    template_name: str
    started_at: datetime
    finished_at: datetime
    error_message: str | None = None

    @property
    def was_successful(self) -> bool:
        return self.error_message is None

    @classmethod
    def from_json(cls, data: Any) -> Self:
        started_at = datetime.fromisoformat(data.pop("started_at"))
        finished_at = datetime.fromisoformat(data.pop("finished_at"))

        field_names = [f.name for f in fields(cls)]

        return cls(
            started_at=started_at,
            finished_at=finished_at,
            **{k: v for k, v in data.items() if k in field_names},
        )


class ReportobelloApi:
    client: AsyncClient

    def __init__(self, api_key: str | None = None, host: str | None = None, app = None) -> None:
        # TODO: close client when class goes out of scope

        if api_key is None:
            api_key = os.getenv("REPORTOBELLO_API_KEY")

            if not api_key:
                raise ReportobelloMissingApiKey

        if host is None:
            host = os.getenv("REPORTOBELLO_HOST", os.getenv("REPORTOBELLO_DOMAIN", DEFAULT_HOST))

        self.client = AsyncClient(
            transport=ASGITransport(app=app) if app else None,
            base_url=host,
            headers={"Authorization": f"Bearer {api_key}"},
        )

    async def get_env_vars(self) -> dict[str, str]:
        resp = await self.get("/api/v1/env")

        return resp.json()

    async def update_env_vars(self, env_vars: Mapping[str, str]) -> None:
        await self.post("/api/v1/env", json=env_vars)

    async def delete_env_vars(self, keys: list[str]) -> None:
        escaped = [quote(k) for k in keys]

        await self.delete("/api/v1/env", params={"keys": ",".join(escaped)})

    async def create_or_update_template(self, template: Template) -> Template:
        url = f"/api/v1/template/{quote(template.name, safe="")}"

        if template.content is not None:
            content = template.content
        elif template.file:
            content = Path(template.file).read_text()
        else:
            assert False

        resp = await self.post(url, content=content, headers={"Content-Type": "application/x-typst"})

        return Template.from_json(resp.json())

    async def upload_data_files(self, template: Template | str, *files: Path | str) -> None:  # type: ignore
        if not files:
            return

        template_name = template.name if isinstance(template, Template) else template

        url = f"/api/v1/template/{quote(template_name, safe="")}/files"

        files: list[Path] = [Path(file) for file in files]

        resp = await self.post(url, files={file.name: (file.name, file.read_text()) for file in files})

        if resp.status_code == 400:
            raise ReportobelloException(resp.text)

        if resp.status_code == 404:
            raise ReportobelloTemplateNotFound(resp.text)

        if resp.status_code == 413:
            raise ReportobelloFileTooBig(resp.text)

    async def get_recent_builds(self, template: Template | str, before: datetime | None = None) -> list[Report]:
        template_name = template.name if isinstance(template, Template) else template

        url = f"/api/v1/template/{quote(template_name, safe="")}/recent"

        if before is not None:
            url += f"?before={quote(before.isoformat())}"

        resp = await self.get(url)

        if resp.status_code == 404:
            raise ReportobelloTemplateNotFound(resp.text)

        return [Report.from_json(r) for r in resp.json()]

    async def get_templates(self) -> list[Template]:
        url = "/api/v1/templates"

        resp = await self.get(url)

        return [Template.from_json(r) for r in resp.json()]

    async def get_template_versions(self, template: Template | str) -> list[Template]:
        template_name = template.name if isinstance(template, Template) else template

        url = f"/api/v1/template/{quote(template_name, safe="")}"

        resp = await self.get(url)

        if resp.status_code == 404:
            raise ReportobelloTemplateNotFound(resp.text)

        return [Template.from_json(r) for r in resp.json()]

    @overload
    async def build_template(self, template: Template) -> LazyPdf:
        pass

    @overload
    async def build_template(
        self,
        template: Template | str,
        data: Mapping[str, Any] | Any,
    ) -> LazyPdf:
        pass

    # TODO: support pydantic models
    async def build_template(
        self,
        template: Template | str,
        data: Mapping[str, Any] | Any | None = None,
    ) -> LazyPdf:
        template_name = template.name if isinstance(template, Template) else template

        url = f"/api/v1/template/{quote(template_name, safe="")}/build?justUrl"

        if data is None:
            assert isinstance(template, Template), "if data is unset, template must be a Template"

            data = {}

            template_field_names = {f.name for f in fields(Template)}

            for f in fields(template):
                if f.name not in template_field_names:
                    data[f.name] = getattr(template, f.name)

        elif is_dataclass(data):
            data = asdict(data)

        data = {
            "data": data,
            "content_type": "application/json",
        }

        resp = await self.post(url, json=data, follow_redirects=False)

        if resp.status_code == 400:
            raise ReportobelloReportBuildFailure(resp.text)

        if resp.status_code == 404:
            raise ReportobelloTemplateNotFound(resp.text)

        assert resp.status_code == 200

        return LazyPdf(self, resp.text)

    async def delete_template(self, template: Template | str) -> None:
        template_name = template.name if isinstance(template, Template) else template

        await self.delete(f"/api/v1/template/{quote(template_name, safe="")}")

    async def get(self, *args, **kwargs) -> Response:
        resp = await self.client.get(*args, **kwargs)

        self._handle_common_error_codes(resp)

        return resp

    async def post(self, *args, **kwargs) -> Response:
        resp = await self.client.post(*args, **kwargs)

        self._handle_common_error_codes(resp)

        return resp

    async def delete(self, *args, **kwargs) -> Response:
        resp = await self.client.delete(*args, **kwargs)

        self._handle_common_error_codes(resp)

        return resp

    @staticmethod
    def _handle_common_error_codes(resp: Response) -> None:
        if resp.status_code == 401:
            raise ReportobelloUnauthorized(resp.text)

        if resp.status_code >= 500:
            raise ReportobelloServerError(resp.reason_phrase)
