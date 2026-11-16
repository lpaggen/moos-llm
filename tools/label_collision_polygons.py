#!/usr/bin/env python3
"""Draw land/collision polygons on a MOOS chart and save planner-ready JSON."""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog

try:
    from PIL import Image, ImageTk
except ImportError as exc:  # pragma: no cover - exercised by users without uv
    raise SystemExit("Pillow is required. Run this utility with `uv run python`.") from exc


INFO_KEYS = {"datum_lat", "datum_lon", "lat_north", "lat_south", "lon_west", "lon_east"}


def read_chart_info(path: Path) -> dict[str, float]:
    values: dict[str, float] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("//", 1)[0].strip()
        match = re.match(r"([A-Za-z_]+)\s*=\s*([-+0-9.eE]+)$", line)
        if match and match.group(1).lower() in INFO_KEYS:
            values[match.group(1).lower()] = float(match.group(2))
    missing = INFO_KEYS - values.keys()
    if missing:
        raise ValueError(f"{path} is missing: {', '.join(sorted(missing))}")
    return values


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Return a counter-clockwise convex hull without repeated end points."""
    unique = sorted(set(points))
    if len(unique) <= 2:
        return unique

    def cross(origin, a, b):
        return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])

    lower: list[tuple[float, float]] = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper: list[tuple[float, float]] = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    return lower[:-1] + upper[:-1]


def moos_polygon_spec(name: str, points: list[tuple[float, float]]) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "obstacle"
    vertices = ":".join(f"{x:.3f},{y:.3f}" for x, y in points)
    return f"pts={{{vertices}}},label={safe_name}"


@dataclass(frozen=True)
class ChartTransform:
    width: int
    height: int
    info: dict[str, float]

    def pixel_to_latlon(self, px: float, py: float) -> tuple[float, float]:
        """Map pixels to the corner coordinates declared in the MOOS .info file."""
        u = px / self.width
        v = py / self.height
        lon = self.info["lon_west"] + u * (self.info["lon_east"] - self.info["lon_west"])
        lat = self.info["lat_north"] - v * (self.info["lat_north"] - self.info["lat_south"])
        return lat, lon

    def latlon_to_pixel(self, lat: float, lon: float) -> tuple[float, float]:
        px = self.width * (lon - self.info["lon_west"]) / (self.info["lon_east"] - self.info["lon_west"])
        py = self.height * (self.info["lat_north"] - lat) / (self.info["lat_north"] - self.info["lat_south"])
        return px, py

    def latlon_to_xy(self, lat: float, lon: float) -> tuple[float, float]:
        """Match pMarineViewer's BackImg pixel-to-local-grid placement.

        BackImg does not independently geodesy-project every image point. It
        computes metre extents from the NW/SE corners and then maps the raster
        affinely around the declared datum. Mirroring that behavior keeps
        VIEW_POLYGON exactly on top of the TIFF.
        """
        north, south = self.info["lat_north"], self.info["lat_south"]
        west, east = self.info["lon_west"], self.info["lon_east"]
        center_lat, center_lon = (north + south) / 2, (west + east) / 2

        def local_grid(point_lat: float, point_lon: float) -> tuple[float, float]:
            semi_major, semi_minor = 6_378_137.0, 6_356_752.0
            lat_rad = math.radians(point_lat)
            tan_sq = math.tan(lat_rad) ** 2
            radius = semi_minor * math.sqrt(1 + tan_sq) / math.sqrt(
                (semi_minor**2 / semi_major**2) + tan_sq
            )
            x = radius * math.sin(math.radians(point_lon - center_lon)) * math.cos(lat_rad)
            y = radius * math.sin(math.radians(point_lat - center_lat))
            return x, y

        nw_x, nw_y = local_grid(north, west)
        se_x, se_y = local_grid(south, east)
        width, height = abs(se_x - nw_x), abs(nw_y - se_y)
        datum_u = (self.info["datum_lon"] - west) / (east - west)
        datum_v_from_bottom = (self.info["datum_lat"] - south) / (north - south)
        u = (lon - west) / (east - west)
        v_from_bottom = (lat - south) / (north - south)
        return (u - datum_u) * width, (v_from_bottom - datum_v_from_bottom) * height


class PolygonEditor:
    COLORS = ("#ff3b30", "#ff9500", "#ffcc00", "#af52de", "#00c7be")

    def __init__(self, root: tk.Tk, chart: Path, output: Path):
        self.root, self.chart, self.output = root, chart.resolve(), output.resolve()
        info_path = chart.with_suffix(".info")
        self.info = read_chart_info(info_path)
        self.source = Image.open(chart).convert("RGB")
        self.transform = ChartTransform(self.source.width, self.source.height, self.info)
        self.polygons: list[dict] = []
        self.current: list[tuple[float, float]] = []
        # Start fitted to a typical window; never upscale these already-large
        # charts in the initial view.
        self.zoom = min(1.0, 1100 / self.source.width, 760 / self.source.height)
        self.photo = None
        self.render_job = None

        root.title(f"Collision polygon editor — {chart.name}")
        toolbar = tk.Frame(root)
        toolbar.pack(fill="x")
        for label, command in (("Finish polygon (Enter)", self.finish), ("Undo point (Backspace)", self.undo),
                               ("Delete last polygon", self.delete_last), ("Save (Ctrl+S)", self.save)):
            tk.Button(toolbar, text=label, command=command).pack(side="left", padx=2, pady=2)
        self.status = tk.StringVar(
            value="Left-click points; Enter closes. Wheel zooms at cursor; middle-drag pans."
        )
        tk.Label(toolbar, textvariable=self.status, anchor="w").pack(side="left", padx=10)
        chart_frame = tk.Frame(root)
        chart_frame.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(chart_frame, bg="#202020", highlightthickness=0)
        xbar = tk.Scrollbar(chart_frame, orient="horizontal", command=self.scroll_x)
        ybar = tk.Scrollbar(chart_frame, orient="vertical", command=self.scroll_y)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        chart_frame.rowconfigure(0, weight=1)
        chart_frame.columnconfigure(0, weight=1)
        self.canvas.bind("<Button-1>", self.add_point)
        self.canvas.bind("<Button-3>", lambda _e: self.undo())
        self.canvas.bind("<MouseWheel>", self.on_zoom)
        self.canvas.bind("<Button-4>", lambda e: self.on_zoom(e, 1))
        self.canvas.bind("<Button-5>", lambda e: self.on_zoom(e, -1))
        self.canvas.bind("<ButtonPress-2>", self.pan_start)
        self.canvas.bind("<B2-Motion>", self.pan_move)
        self.canvas.bind("<ButtonRelease-2>", lambda _e: self.schedule_render())
        self.canvas.bind("<Configure>", lambda _e: self.schedule_render())
        root.bind("<Return>", lambda _e: self.finish())
        root.bind("<BackSpace>", lambda _e: self.undo())
        root.bind("<Control-s>", lambda _e: self.save())
        self.load()
        self.render_background()
        self.redraw()

    def canvas_to_pixel(self, event) -> tuple[float, float]:
        return self.canvas.canvasx(event.x) / self.zoom, self.canvas.canvasy(event.y) / self.zoom

    def add_point(self, event) -> None:
        px, py = self.canvas_to_pixel(event)
        if 0 <= px <= self.source.width and 0 <= py <= self.source.height:
            self.current.append((px, py))
            lat, lon = self.transform.pixel_to_latlon(px, py)
            x, y = self.transform.latlon_to_xy(lat, lon)
            self.status.set(f"point {len(self.current)}: x={x:.1f}, y={y:.1f} m")
            self.redraw()

    def finish(self) -> None:
        if len(self.current) < 3:
            self.status.set("A polygon needs at least three points.")
            return
        default = f"land_{len(self.polygons) + 1}"
        name = simpledialog.askstring("Polygon name", "Name:", initialvalue=default, parent=self.root)
        if name is None:
            return
        hull = convex_hull(self.current)
        removed = len(self.current) - len(hull)
        self.polygons.append({"name": name.strip() or default, "pixels": hull})
        self.current.clear()
        self.redraw()
        if removed:
            self.status.set(f"Closed as a convex hull; {removed} interior/concave points were removed.")

    def undo(self) -> None:
        if self.current:
            self.current.pop()
            self.redraw()

    def delete_last(self) -> None:
        if self.current:
            self.current.clear()
        elif self.polygons:
            self.polygons.pop()
        self.redraw()

    def on_zoom(self, event, direction=None) -> None:
        direction = direction if direction is not None else (1 if event.delta > 0 else -1)
        old = self.zoom
        # Twelve source pixels per chart pixel is enough for very precise
        # placement without ever allocating a twelve-times-full-size bitmap.
        self.zoom = min(12.0, max(0.03, self.zoom * (1.35 if direction > 0 else 1 / 1.35)))
        if self.zoom == old:
            return
        source_x = self.canvas.canvasx(event.x) / old
        source_y = self.canvas.canvasy(event.y) / old
        full_w, full_h = self.source.width * self.zoom, self.source.height * self.zoom
        self.canvas.configure(scrollregion=(0, 0, full_w, full_h))
        target_x = source_x * self.zoom - event.x
        target_y = source_y * self.zoom - event.y
        self.canvas.xview_moveto(max(0, target_x / full_w))
        self.canvas.yview_moveto(max(0, target_y / full_h))
        self.render_background()
        self.redraw()

    def scroll_x(self, *args) -> None:
        self.canvas.xview(*args)
        self.schedule_render()

    def scroll_y(self, *args) -> None:
        self.canvas.yview(*args)
        self.schedule_render()

    def pan_start(self, event) -> None:
        self.canvas.scan_mark(event.x, event.y)

    def pan_move(self, event) -> None:
        self.canvas.scan_dragto(event.x, event.y, gain=1)
        self.schedule_render()

    def schedule_render(self) -> None:
        """Coalesce rapid scrollbar, pan, and resize events."""
        if self.render_job is not None:
            self.root.after_cancel(self.render_job)
        self.render_job = self.root.after(30, self.refresh_view)

    def refresh_view(self) -> None:
        self.render_job = None
        self.render_background()
        self.redraw()

    def render_background(self) -> None:
        """Render only the visible chart tile, allowing fast deep zooming."""
        full_w = max(1, round(self.source.width * self.zoom))
        full_h = max(1, round(self.source.height * self.zoom))
        self.canvas.configure(scrollregion=(0, 0, full_w, full_h))
        view_w = max(2, self.canvas.winfo_width())
        view_h = max(2, self.canvas.winfo_height())
        canvas_x = max(0.0, self.canvas.canvasx(0))
        canvas_y = max(0.0, self.canvas.canvasy(0))
        left = max(0, math.floor(canvas_x / self.zoom) - 2)
        top = max(0, math.floor(canvas_y / self.zoom) - 2)
        right = min(self.source.width, math.ceil((canvas_x + view_w) / self.zoom) + 2)
        bottom = min(self.source.height, math.ceil((canvas_y + view_h) / self.zoom) + 2)
        if right <= left or bottom <= top:
            return
        tile = self.source.crop((left, top, right, bottom))
        size = (max(1, round((right - left) * self.zoom)),
                max(1, round((bottom - top) * self.zoom)))
        preview = tile.resize(size, Image.Resampling.BILINEAR)
        self.photo = ImageTk.PhotoImage(preview)
        self.canvas.delete("background")
        self.canvas.create_image(left * self.zoom, top * self.zoom, anchor="nw",
                                 image=self.photo, tags="background")
        self.canvas.tag_lower("background")

    def redraw(self) -> None:
        """Redraw only vector overlays; this runs on every mouse click."""
        self.canvas.delete("overlay")
        for index, polygon in enumerate(self.polygons):
            self.draw_polygon(polygon["pixels"], self.COLORS[index % len(self.COLORS)], polygon["name"])
        self.draw_polygon(self.current, "#00ff66", "editing", close=False)

    def draw_polygon(self, points, color, label, close=True) -> None:
        if not points:
            return
        coords = [value * self.zoom for point in points for value in point]
        if len(points) >= 2:
            self.canvas.create_line(*coords, fill=color, width=3, tags="overlay")
            if close and len(points) >= 3:
                self.canvas.create_line(*coords[-2:], *coords[:2], fill=color, width=3, tags="overlay")
        for px, py in points:
            x, y, r = px * self.zoom, py * self.zoom, 4
            self.canvas.create_oval(x-r, y-r, x+r, y+r, fill=color, outline="white", tags="overlay")
        self.canvas.create_text(coords[0] + 7, coords[1] - 7, text=label, fill="white", anchor="sw", tags="overlay")

    def save(self) -> None:
        if self.current and not messagebox.askyesno("Unfinished polygon", "Ignore the unfinished green polygon and save?"):
            return
        records = []
        moos_lines = ["// Generated by tools/label_collision_polygons.py",
                      "// Paste these lines inside a ProcessConfig = pObstacleMgr block."]
        for polygon in self.polygons:
            latlon = [self.transform.pixel_to_latlon(*point) for point in polygon["pixels"]]
            xy = [self.transform.latlon_to_xy(*point) for point in latlon]
            spec = moos_polygon_spec(polygon["name"], xy)
            records.append({
                "name": polygon["name"], "kind": "collision",
                "convex": True, "moos_spec": spec,
                "xy": [{"x": round(x, 3), "y": round(y, 3)} for x, y in xy],
                "latlon": [{"lat": round(lat, 8), "lon": round(lon, 8)} for lat, lon in latlon],
                "pixels": [{"x": round(px, 2), "y": round(py, 2)} for px, py in polygon["pixels"]],
            })
            moos_lines.append(f"given_obstacle = {spec}")
        payload = {"schema": "moos-collision-polygons/v1", "chart": str(self.chart),
                   "datum": {"lat": self.info["datum_lat"], "lon": self.info["datum_lon"]},
                   "polygons": records}
        self.output.parent.mkdir(parents=True, exist_ok=True)
        self.output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        moos_output = self.output.with_suffix(".moos")
        moos_output.write_text("\n".join(moos_lines) + "\n", encoding="utf-8")
        self.status.set(f"Saved {len(records)} convex polygons and {moos_output.name}")

    def load(self) -> None:
        if not self.output.exists():
            return
        try:
            data = json.loads(self.output.read_text(encoding="utf-8"))
            for item in data.get("polygons", []):
                if item.get("pixels"):
                    points = [(float(p["x"]), float(p["y"])) for p in item["pixels"]]
                else:
                    points = [self.transform.latlon_to_pixel(float(p["lat"]), float(p["lon"])) for p in item["latlon"]]
                self.polygons.append({"name": str(item.get("name", "land")), "pixels": convex_hull(points)})
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            messagebox.showwarning("Could not load polygons", str(exc))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chart", type=Path, help="TIFF chart (a same-name .info file is required)")
    parser.add_argument("--output", "-o", type=Path, help="output JSON (default: <chart>.collision.json)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output = args.output or args.chart.with_suffix(".collision.json")
    if not args.chart.is_file():
        raise SystemExit(f"Chart not found: {args.chart}")
    root = tk.Tk()
    root.geometry("1200x850")
    PolygonEditor(root, args.chart, output)
    root.mainloop()


if __name__ == "__main__":
    main()
