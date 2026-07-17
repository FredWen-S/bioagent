from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@dataclass
class FakeElement:
    text: str = ""
    bbox: dict[str, float] | None = None
    visible: bool = True
    attrs: dict[str, str] = field(default_factory=dict)
    children: dict[str, list["FakeElement"]] = field(default_factory=dict)
    has_thumbnail: bool = False
    filled_value: str | None = None
    clicked: bool = False


class FakeLocator:
    def __init__(self, elements: list[FakeElement] | None = None) -> None:
        self.elements = elements or []

    def count(self) -> int:
        return len(self.elements)

    def nth(self, index: int) -> "FakeLocator":
        return FakeLocator([self.elements[index]]) if index < len(self.elements) else FakeLocator()

    @property
    def first(self) -> "FakeLocator":
        return self.nth(0)

    def is_visible(self) -> bool:
        return bool(self.elements and self.elements[0].visible)

    def bounding_box(self) -> dict[str, float] | None:
        return self.elements[0].bbox if self.elements else None

    def inner_text(self, timeout: int | None = None) -> str:
        del timeout
        return self.elements[0].text if self.elements else ""

    def get_attribute(self, name: str) -> str | None:
        return self.elements[0].attrs.get(name) if self.elements else None

    def click(self, timeout: int | None = None) -> None:
        del timeout
        if self.elements:
            self.elements[0].clicked = True

    def fill(self, value: str) -> None:
        if self.elements:
            self.elements[0].filled_value = value

    def scroll_into_view_if_needed(self) -> None:
        return None

    def locator(self, selector: str) -> "FakeLocator":
        matches: list[FakeElement] = []
        for element in self.elements:
            matches.extend(element.children.get(selector, []))
            if selector == "img, svg, canvas" and element.has_thumbnail:
                matches.append(FakeElement(bbox={"x": 0, "y": 0, "width": 10, "height": 10}))
        return FakeLocator(matches)

    def screenshot(self, path: str) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ONE_PIXEL_PNG)


class FakeMouse:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def move(self, x: float, y: float, steps: int | None = None) -> None:
        self.events.append(("move", x, y, steps))

    def down(self) -> None:
        self.events.append(("down",))

    def up(self) -> None:
        self.events.append(("up",))


class FakePage:
    def __init__(
        self,
        *,
        selector_map: dict[str, list[FakeElement]] | None = None,
        url: str = "https://app.biorender.com/editor/fixture",
    ) -> None:
        self.selector_map = selector_map or {}
        self.url = url
        self.viewport_size = {"width": 1440, "height": 1000}
        self.mouse = FakeMouse()
        self.goto_calls: list[str] = []

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self.selector_map.get(selector, []))

    def get_by_role(self, role: str, name: Any = None) -> FakeLocator:
        matches: list[FakeElement] = []
        for elements in self.selector_map.values():
            for element in elements:
                if element.attrs.get("role") != role:
                    continue
                accessible_name = element.attrs.get("accessible_name", element.text)
                if name is None or name.search(accessible_name):
                    matches.append(element)
        return FakeLocator(_unique(matches))

    def get_by_label(self, name: Any) -> FakeLocator:
        matches = [
            element
            for elements in self.selector_map.values()
            for element in elements
            if name.search(element.attrs.get("label", ""))
        ]
        return FakeLocator(_unique(matches))

    def get_by_text(self, name: Any) -> FakeLocator:
        matches = [
            element
            for elements in self.selector_map.values()
            for element in elements
            if name.search(element.text)
        ]
        return FakeLocator(_unique(matches))

    def screenshot(self, path: str, full_page: bool = False) -> None:
        del full_page
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(ONE_PIXEL_PNG)

    def wait_for_timeout(self, milliseconds: int) -> None:
        del milliseconds

    def goto(self, url: str, **kwargs: Any) -> None:
        del kwargs
        self.goto_calls.append(url)
        self.url = url

    def title(self) -> str:
        return "BioRender Fixture"


def _unique(elements: list[FakeElement]) -> list[FakeElement]:
    seen: set[int] = set()
    result: list[FakeElement] = []
    for element in elements:
        identity = id(element)
        if identity not in seen:
            seen.add(identity)
            result.append(element)
    return result
