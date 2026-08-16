#!/usr/bin/env python
"""
Pre-flight satellite-imagery availability checks.

Tile providers silently return a placeholder tile ("Map data not available yet")
when the requested zoom exceeds their coverage for an area. Stitching those
produces an unusable flat-gray texture. This module detects the situation BEFORE
a full tile download so the user can be warned and offered the highest usable
zoom.

Two strategies:
  * ESRI World Imagery (and any ArcGIS MapServer with the Tilemap capability)
    expose a Tilemap service that reports exactly which tiles exist -> precise,
    per-location, needs no image downloads and no API key.
  * Any other source falls back to content sampling: a handful of tiles are
    fetched and flagged as placeholders if they fail to decode or are
    byte-identical to each other (real imagery tiles are never identical across
    different x/y; providers serve one shared placeholder for missing tiles).
"""

import json
import math
import urllib.error
import urllib.request

import cv2
import numpy as np

from utils.utils import Utils


class TileAvailability:
    # How many levels below the requested zoom to search for a usable one.
    MAX_ZOOM_WALKDOWN = 8
    # Never walk below this zoom when searching for a usable level.
    MIN_ZOOM = 8
    _UA = {'User-Agent': 'GazeboTerrainGenerator/1.0'}

    @staticmethod
    def deg2num(lat, lon, zoom):
        """Slippy-map tile x/y for a lat/lon at a zoom level."""
        n = 2 ** zoom
        x = int((lon + 180.0) / 360.0 * n)
        lat_r = math.radians(max(min(lat, 85.0511), -85.0511))
        y = int((1.0 - math.log(math.tan(lat_r) + 1.0 / math.cos(lat_r)) / math.pi) / 2.0 * n)
        return min(max(x, 0), n - 1), min(max(y, 0), n - 1)

    @staticmethod
    def _bounds(polygon_vertices):
        lngs = [v[0] for v in polygon_vertices]
        lats = [v[1] for v in polygon_vertices]
        return min(lngs), min(lats), max(lngs), max(lats)

    # --- ESRI / ArcGIS Tilemap -------------------------------------------------

    @staticmethod
    def _is_arcgis_tiled(source):
        return 'MapServer/tile/' in source

    @staticmethod
    def _tilemap_base(source):
        return source.split('/tile/')[0] + '/tilemap'

    @staticmethod
    def _arcgis_available_fraction(base, lat, lon, zoom, dim=8):
        """Fraction of a dim x dim tile block that exists at (lat, lon, zoom).

        Returns None if the tilemap service can't be reached, signalling the
        caller to fall back to sampling.
        """
        x, y = TileAvailability.deg2num(lat, lon, zoom)
        url = f'{base}/{zoom}/{y}/{x}/{dim}/{dim}'
        try:
            req = urllib.request.Request(url, headers=TileAvailability._UA)
            with urllib.request.urlopen(req, timeout=20) as resp:
                d = json.loads(resp.read().decode('utf-8'))
        except (urllib.error.URLError, ValueError, OSError):
            return None
        data = d.get('data')
        if not data:
            return 0.0
        return sum(data) / len(data)

    @staticmethod
    def _check_arcgis(source, lat_c, lon_c, zoom):
        base = TileAvailability._tilemap_base(source)
        frac = TileAvailability._arcgis_available_fraction(base, lat_c, lon_c, zoom)
        if frac is None:
            return None  # tilemap unreachable -> caller falls back to sampling
        if frac > 0.0:
            return {'available': True, 'maxAvailableZoom': zoom,
                    'requestedZoom': zoom, 'method': 'esri-tilemap',
                    'availableFraction': round(frac, 3)}
        # Walk down to find the highest usable zoom for this location.
        max_zoom = None
        floor = max(zoom - TileAvailability.MAX_ZOOM_WALKDOWN, TileAvailability.MIN_ZOOM)
        for z in range(zoom - 1, floor - 1, -1):
            f = TileAvailability._arcgis_available_fraction(base, lat_c, lon_c, z)
            if f is None:
                break
            if f > 0.0:
                max_zoom = z
                break
        return {'available': False, 'maxAvailableZoom': max_zoom,
                'requestedZoom': zoom, 'method': 'esri-tilemap',
                'availableFraction': 0.0}

    # --- Content sampling fallback --------------------------------------------

    @staticmethod
    def _decode_gray(raw):
        if not raw:
            return None
        img = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_GRAYSCALE)
        return img

    @staticmethod
    def _sample_coords(polygon_vertices, zoom):
        """Center + inset corners as distinct tile coords at a zoom level."""
        lon_min, lat_min, lon_max, lat_max = TileAvailability._bounds(polygon_vertices)
        lat_c, lon_c = (lat_min + lat_max) / 2.0, (lon_min + lon_max) / 2.0
        dlat, dlon = (lat_max - lat_min) * 0.1, (lon_max - lon_min) * 0.1
        pts = [
            (lat_c, lon_c),
            (lat_min + dlat, lon_min + dlon), (lat_min + dlat, lon_max - dlon),
            (lat_max - dlat, lon_min + dlon), (lat_max - dlat, lon_max - dlon),
        ]
        seen, coords = set(), []
        for la, lo in pts:
            xy = TileAvailability.deg2num(la, lo, zoom)
            if xy not in seen:
                seen.add(xy)
                coords.append(xy)
        return coords

    @staticmethod
    def _is_placeholder_set(imgs):
        """True if the decoded sample tiles look like provider placeholders:
        any two are byte-identical (real imagery tiles never are)."""
        for i in range(len(imgs)):
            for j in range(i + 1, len(imgs)):
                a, b = imgs[i], imgs[j]
                if a is not None and b is not None and a.shape == b.shape and np.array_equal(a, b):
                    return True
        return False

    @staticmethod
    def _check_sampling(source, polygon_vertices, zoom, api_key):
        coords = TileAvailability._sample_coords(polygon_vertices, zoom)
        raws = [Utils.download_bytes(source, x, y, zoom, api_key) for x, y in coords]
        imgs = [TileAvailability._decode_gray(r) for r in raws]
        decoded = [im for im in imgs if im is not None]

        placeholder = TileAvailability._is_placeholder_set(imgs)
        available = len(decoded) > 0 and not placeholder

        result = {'available': available, 'maxAvailableZoom': zoom if available else None,
                  'requestedZoom': zoom, 'method': 'sampling'}
        if available:
            return result

        # Use a placeholder tile as a reference and walk down until the center
        # tile stops matching it (i.e. real imagery appears).
        ref = next((im for im in decoded if im is not None), None)
        if ref is not None:
            lon_min, lat_min, lon_max, lat_max = TileAvailability._bounds(polygon_vertices)
            lat_c, lon_c = (lat_min + lat_max) / 2.0, (lon_min + lon_max) / 2.0
            floor = max(zoom - TileAvailability.MAX_ZOOM_WALKDOWN, TileAvailability.MIN_ZOOM)
            for z in range(zoom - 1, floor - 1, -1):
                x, y = TileAvailability.deg2num(lat_c, lon_c, z)
                cand = TileAvailability._decode_gray(Utils.download_bytes(source, x, y, z, api_key))
                if cand is not None and not (cand.shape == ref.shape and np.array_equal(cand, ref)):
                    result['maxAvailableZoom'] = z
                    break
        return result

    # --- Public entry point ----------------------------------------------------

    @staticmethod
    def check(source, polygon_vertices, zoom, api_key=''):
        """Return {available, maxAvailableZoom, requestedZoom, method, ...}."""
        lon_min, lat_min, lon_max, lat_max = TileAvailability._bounds(polygon_vertices)
        lat_c, lon_c = (lat_min + lat_max) / 2.0, (lon_min + lon_max) / 2.0

        if TileAvailability._is_arcgis_tiled(source):
            result = TileAvailability._check_arcgis(source, lat_c, lon_c, zoom)
            if result is not None:
                return result  # tilemap answered; otherwise fall through

        return TileAvailability._check_sampling(source, polygon_vertices, zoom, api_key)
