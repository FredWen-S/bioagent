from __future__ import annotations

import re
from typing import Literal
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

FORBIDDEN_INPUT = re.compile(
    r"\b(?:biorender\s+ai|ai\s+(?:generate|edit|credits?)|create\s+with\s+ai|"
    r"generate\s+figure|upgrade|subscribe|purchase|export|download|share|publish)\b",
    re.IGNORECASE,
)


def _clean_text(value: str) -> str:
    return " ".join(value.strip().split())


def _reject_forbidden(value: str) -> str:
    cleaned = _clean_text(value)
    if FORBIDDEN_INPUT.search(cleaned):
        raise ValueError("AI、付费、导出或分享指令不允许进入绘图任务")
    return cleaned


class CustomAssetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    display_name: str = Field(min_length=1, max_length=80)
    search_term: str = Field(min_length=1, max_length=80)
    fallback_terms: list[str] = Field(default_factory=list, max_length=5)
    label_text: str | None = Field(default=None, max_length=80)

    @field_validator("display_name", "search_term")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _reject_forbidden(value)

    @field_validator("label_text")
    @classmethod
    def validate_optional_text(cls, value: str | None) -> str | None:
        return _reject_forbidden(value) if value else None

    @field_validator("fallback_terms")
    @classmethod
    def validate_fallbacks(cls, values: list[str]) -> list[str]:
        cleaned = [_reject_forbidden(value) for value in values]
        if any(len(value) > 80 for value in cleaned):
            raise ValueError("备用搜索词不能超过 80 个字符")
        return list(dict.fromkeys(cleaned))


class CustomRelationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    target_id: str = Field(pattern=r"^[a-z][a-z0-9_]{1,31}$")
    type: Literal["line", "arrow", "inhibition"]

    @model_validator(mode="after")
    def reject_self_relation(self) -> CustomRelationInput:
        if self.source_id == self.target_id:
            raise ValueError("连接关系的起点和终点不能相同")
        return self


class CustomFigureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=120)
    research_topic: str = Field(min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=1000)
    assets: list[CustomAssetInput] = Field(min_length=2, max_length=15)
    relations: list[CustomRelationInput] = Field(min_length=1, max_length=30)
    layout: Literal["auto"] = "auto"

    @field_validator("title", "research_topic")
    @classmethod
    def validate_required_text(cls, value: str) -> str:
        return _reject_forbidden(value)

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, value: str | None) -> str | None:
        return _reject_forbidden(value) if value else None

    @model_validator(mode="after")
    def validate_graph(self) -> CustomFigureInput:
        asset_ids = [asset.id for asset in self.assets]
        if len(asset_ids) != len(set(asset_ids)):
            raise ValueError("素材 ID 必须唯一")
        known = set(asset_ids)
        connected: set[str] = set()
        relation_keys: set[tuple[str, str, str]] = set()
        for relation in self.relations:
            if relation.source_id not in known or relation.target_id not in known:
                raise ValueError("连接关系引用了不存在的素材")
            key = (relation.source_id, relation.target_id, relation.type)
            if key in relation_keys:
                raise ValueError("不能添加完全重复的连接关系")
            relation_keys.add(key)
            connected.update((relation.source_id, relation.target_id))
        isolated = known - connected
        if isolated:
            raise ValueError(f"所有素材都必须参与连接关系：{sorted(isolated)}")
        return self


class UiTaskInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["preset", "prompt", "custom"]
    preset_id: Literal["pd1"] | None = None
    prompt: str | None = Field(default=None, min_length=3, max_length=3000)
    custom: CustomFigureInput | None = None

    @field_validator("prompt")
    @classmethod
    def validate_prompt(cls, value: str | None) -> str | None:
        return _reject_forbidden(value) if value else None

    @model_validator(mode="after")
    def validate_mode_payload(self) -> UiTaskInput:
        if self.mode == "preset" and self.preset_id != "pd1":
            raise ValueError("当前只支持 PD-1 / PD-L1 预设")
        if self.mode == "preset" and self.custom is not None:
            raise ValueError("预设模式不能同时提交自定义图形")
        if self.mode == "preset" and self.prompt is not None:
            raise ValueError("预设模式不能同时提交 Prompt")
        if self.mode == "prompt" and self.prompt is None:
            raise ValueError("Prompt 模式需要填写绘图需求")
        if self.mode == "prompt" and (
            self.preset_id is not None or self.custom is not None
        ):
            raise ValueError("Prompt 模式不能同时提交预设或结构化图形")
        if self.mode == "custom" and self.custom is None:
            raise ValueError("自定义模式需要素材和连接关系")
        if self.mode == "custom" and (
            self.preset_id is not None or self.prompt is not None
        ):
            raise ValueError("自定义模式不能同时选择预设或 Prompt")
        return self


class UiPlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: UiTaskInput


class UiDryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: UiTaskInput
    plan_id: str = Field(pattern=r"^figure_[a-zA-Z0-9_-]+$")


class SafeEditorUrlMixin(BaseModel):
    editor_url: str = Field(min_length=12, max_length=2048)

    @field_validator("editor_url")
    @classmethod
    def validate_editor_url(cls, value: str) -> str:
        parsed = urlparse(value.strip())
        hostname = (parsed.hostname or "").casefold()
        if parsed.scheme != "https":
            raise ValueError("BioRender Figure URL 必须使用 HTTPS")
        if hostname != "biorender.com" and not hostname.endswith(".biorender.com"):
            raise ValueError("只允许 BioRender 官方域名的 Figure URL")
        if parsed.username or parsed.password:
            raise ValueError("Figure URL 不能包含账号或密码")
        return value.strip()


class UiEditorUrlRequest(SafeEditorUrlMixin):
    model_config = ConfigDict(extra="forbid")


class UiCanvasCheckRequest(SafeEditorUrlMixin):
    model_config = ConfigDict(extra="forbid")

    confirmed_blank: bool


class UiCalibrationRequest(SafeEditorUrlMixin):
    model_config = ConfigDict(extra="forbid")

    confirmed_disposable: bool
    confirm_live: bool
    enable_biorender_ai: Literal[False] = False


class UiLiveRunRequest(SafeEditorUrlMixin):
    model_config = ConfigDict(extra="forbid")

    task: UiTaskInput
    plan_id: str | None = Field(default=None, pattern=r"^figure_[a-zA-Z0-9_-]+$")
    dry_run_id: str = Field(pattern=r"^figure_[a-zA-Z0-9_-]+$")
    confirmed_disposable: bool
    confirm_live: bool
    enable_biorender_ai: Literal[False] = False


class UiResumeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmed_disposable: bool
    confirm_live: bool
    enable_biorender_ai: Literal[False] = False


class UiLoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_manual_login: bool


class UiError(BaseModel):
    error_code: str
    message: str
    diagnostic_hint: str | None = None
    details: dict[str, object] | list[object] | None = None
