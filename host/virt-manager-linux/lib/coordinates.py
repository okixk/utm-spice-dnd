"""Pure coordinate conversion for a GTK viewport containing a SPICE display."""

from __future__ import annotations

import math
from dataclasses import dataclass


def _finite_positive(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")


def _finite(name: str, value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")


@dataclass(frozen=True)
class DisplayGeometry:
    """Geometry needed to map one GTK drop event into a guest monitor.

    Widget dimensions and event positions are GTK logical pixels. ``scale_factor``
    converts them to host device pixels. Scroll offsets are host device pixels
    into an unscaled or otherwise oversized rendered surface. Framebuffer and
    monitor-origin values are guest pixels.
    """

    widget_width: float
    widget_height: float
    scale_factor: float
    framebuffer_width: float
    framebuffer_height: float
    scaling: bool = True
    only_downscale: bool = False
    scroll_x: float = 0.0
    scroll_y: float = 0.0
    display: int = 0
    monitor_origin_x: float = 0.0
    monitor_origin_y: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "widget_width",
            "widget_height",
            "scale_factor",
            "framebuffer_width",
            "framebuffer_height",
        ):
            _finite_positive(name, getattr(self, name))
        for name in ("scroll_x", "scroll_y", "monitor_origin_x", "monitor_origin_y"):
            _finite(name, getattr(self, name))
        if not isinstance(self.scaling, bool) or not isinstance(self.only_downscale, bool):
            raise ValueError("scaling flags must be boolean")
        if isinstance(self.display, bool) or not isinstance(self.display, int) or not 0 <= self.display <= 31:
            raise ValueError("display must be an integer from 0 through 31")
        if self.scroll_x < 0 or self.scroll_y < 0:
            raise ValueError("scroll offsets cannot be negative")

        rendered_width, rendered_height, _ = self.rendered_size
        viewport_width = self.widget_width * self.scale_factor
        viewport_height = self.widget_height * self.scale_factor
        maximum_x = max(0.0, rendered_width - viewport_width)
        maximum_y = max(0.0, rendered_height - viewport_height)
        if self.scroll_x > maximum_x or self.scroll_y > maximum_y:
            raise ValueError("scroll offset exceeds the rendered surface")

    @property
    def rendered_size(self) -> tuple[float, float, float]:
        viewport_width = self.widget_width * self.scale_factor
        viewport_height = self.widget_height * self.scale_factor
        if self.scaling:
            render_scale = min(
                viewport_width / self.framebuffer_width,
                viewport_height / self.framebuffer_height,
            )
            if self.only_downscale:
                render_scale = min(1.0, render_scale)
        else:
            render_scale = 1.0
        # spice_display_get_scaling() rounds the rendered dimensions to
        # physical device pixels before transform_input() maps input back.
        rendered_width = math.floor(self.framebuffer_width * render_scale + 0.5)
        rendered_height = math.floor(self.framebuffer_height * render_scale + 0.5)
        return rendered_width, rendered_height, render_scale


@dataclass(frozen=True)
class GuestPoint:
    display: int
    x: float
    y: float
    framebuffer_width: float
    framebuffer_height: float
    monitor_origin_x: float
    monitor_origin_y: float
    surface_x: float
    surface_y: float
    render_scale: float


def map_widget_point(
    geometry: DisplayGeometry,
    widget_x: float,
    widget_y: float,
) -> GuestPoint | None:
    """Return monitor-local guest coordinates, or ``None`` for letterbox/outside."""

    _finite("widget_x", widget_x)
    _finite("widget_y", widget_y)

    viewport_width = geometry.widget_width * geometry.scale_factor
    viewport_height = geometry.widget_height * geometry.scale_factor
    rendered_width, rendered_height, render_scale = geometry.rendered_size

    if rendered_width <= viewport_width:
        rendered_origin_x = math.floor((viewport_width - rendered_width) / 2.0)
    else:
        rendered_origin_x = -geometry.scroll_x
    if rendered_height <= viewport_height:
        rendered_origin_y = math.floor((viewport_height - rendered_height) / 2.0)
    else:
        rendered_origin_y = -geometry.scroll_y

    device_x = widget_x * geometry.scale_factor
    device_y = widget_y * geometry.scale_factor
    relative_x = device_x - rendered_origin_x
    relative_y = device_y - rendered_origin_y

    if not (0 <= relative_x < rendered_width and 0 <= relative_y < rendered_height):
        return None

    # This deliberately mirrors spice-gtk transform_input(): endpoints are
    # mapped to endpoints so the final rendered pixel can reach the final
    # guest pixel even when the display is scaled down.
    input_scale = (
        0
        if rendered_width == 1 or geometry.framebuffer_width == 1
        else (geometry.framebuffer_width - 1) / (rendered_width - 1)
    )
    guest_x = math.floor(relative_x * input_scale)
    guest_y = math.floor(relative_y * input_scale)

    if not (
        0 <= guest_x < geometry.framebuffer_width
        and 0 <= guest_y < geometry.framebuffer_height
    ):
        return None

    return GuestPoint(
        display=geometry.display,
        x=guest_x,
        y=guest_y,
        framebuffer_width=geometry.framebuffer_width,
        framebuffer_height=geometry.framebuffer_height,
        monitor_origin_x=geometry.monitor_origin_x,
        monitor_origin_y=geometry.monitor_origin_y,
        surface_x=geometry.monitor_origin_x + guest_x,
        surface_y=geometry.monitor_origin_y + guest_y,
        render_scale=render_scale,
    )
