"""Earth Search STAC queries. Only asset URLs returned by STAC items are used."""
from __future__ import annotations

import json
import time
from datetime import datetime

from pydantic import BaseModel
from pystac_client import Client

from app.config import get_settings
from app.satellite import cache

ASSET_KEYS = {"B02": "blue", "B03": "green", "B04": "red", "B08": "nir", "B11": "swir16", "SCL": "scl"}


class BandAsset(BaseModel):
    band: str
    href: str
    scale: float | None
    offset: float | None
    nodata: float | None
    resolution_m: float | None


class SceneMeta(BaseModel):
    scene_id: str
    collection: str
    acquired_at: datetime
    cloud_cover: float | None
    epsg: int | None
    processing_baseline: str | None
    platform: str | None
    self_href: str | None
    assets: dict[str, BandAsset]

    def missing_bands(self) -> list[str]:
        return [b for b in ASSET_KEYS if b not in self.assets]


def _scene_from_item(item) -> SceneMeta:
    assets = {}
    for band, key in ASSET_KEYS.items():
        a = item.assets.get(key)
        if a is None:
            continue
        rb = (a.extra_fields.get("raster:bands") or [{}])[0]
        assets[band] = BandAsset(band=band, href=a.href, scale=rb.get("scale"), offset=rb.get("offset"),
                                 nodata=rb.get("nodata"), resolution_m=rb.get("spatial_resolution"))
    code = item.properties.get("proj:epsg") or item.properties.get("proj:code")
    epsg = int(str(code).split(":")[-1]) if code else None
    self_link = next((l.href for l in item.links if l.rel == "self"), None)
    return SceneMeta(scene_id=item.id, collection=item.collection_id, acquired_at=item.datetime,
                     cloud_cover=item.properties.get("eo:cloud_cover"), epsg=epsg,
                     processing_baseline=item.properties.get("s2:processing_baseline"),
                     platform=item.properties.get("platform"), self_href=self_link, assets=assets)


def search_scenes(bbox: list[float], start: str, end: str, max_cloud: float | None = None,
                  max_items: int = 40) -> list[SceneMeta]:
    """Search the configured collection. Results are cached by query parameters."""
    s = get_settings()
    max_cloud = s.max_scene_cloud_percent if max_cloud is None else max_cloud
    params = {"url": s.stac_api_url, "collection": s.stac_collection, "bbox": [round(v, 6) for v in bbox],
              "datetime": f"{start}/{end}", "max_cloud": max_cloud, "max_items": max_items}
    key = cache.key("stac_search", params)
    hit = cache.get_json(key)
    if hit is not None:
        return [SceneMeta.model_validate(x) for x in hit["scenes"]]

    last_err: Exception | None = None
    for attempt in range(3):
        try:
            catalog = Client.open(s.stac_api_url, timeout=s.http_timeout_s)
            items = list(catalog.search(collections=[s.stac_collection], bbox=bbox, datetime=f"{start}/{end}",
                                        query={"eo:cloud_cover": {"lt": max_cloud}},
                                        max_items=max_items).items())
            break
        except Exception as e:  # noqa: BLE001 - network errors vary
            last_err = e
            time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"STAC search failed: {last_err}") from last_err

    scenes = sorted((_scene_from_item(i) for i in items), key=lambda x: x.acquired_at)
    cache.put_json(key, {"query": params, "fetched_at": datetime.utcnow().isoformat() + "Z",
                         "scenes": [json.loads(sc.model_dump_json()) for sc in scenes]})
    return scenes
