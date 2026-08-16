import os
import tempfile
from pathlib import Path


class GlobalParam:

    # Draw tile borders on aerial.png output for debugging tile grid alignment
    DEBUG_TILE_BORDERS          = False

    # Output base directory (override with GAZEBO_TERRAIN_OUTPUT_PATH env var)
    OUTPUT_BASE_PATH            = os.path.abspath(os.path.expanduser(
                                      os.getenv('GAZEBO_TERRAIN_OUTPUT_PATH',
                                                os.path.join(tempfile.gettempdir(), 'gazebo_terrain_generator'))
                                  ))

    # Path to the SDF/XML world templates directory
    TEMPLATE_DIR_PATH           = str(Path(__file__).resolve().parents[2] / 'templates')

    # DEM zoom cap — AWS Terrain Tiles are backed by ~30m SRTM data, so zoom > 13
    # yields no additional real-world elevation detail.
    DEM_RESOLUTION              = 13

    # AWS Terrain Tiles (terrarium encoding) are 256×256px, unlike Mapbox's 512px tiles.
    DEM_TILE_PX                 = 256

    # OSM building query zoom — kept for API compatibility; buildings are now fetched
    # via the Overpass API by bounding box rather than per-tile.
    DEM_BUILDING_RESOLUTION     = 15

    # Valid Gazebo heightmap sizes (must be 2^n+1)
    VALID_HEIGHTMAP_SIZES       = [257, 513, 1025, 2049, 4097]

    # Max aerial.png texture dimension (px). GPUs cap textures at GL_MAX_TEXTURE_SIZE
    # (commonly 16384; 32768 on high-end cards) — exceeding it makes the GL driver
    # refuse the texture, crashing gz-sim's renderer. 16384 is a safe, widely-supported
    # default (~1 GB VRAM with mipmaps). Override with GAZEBO_MAX_AERIAL_TEXTURE_SIZE.
    MAX_AERIAL_TEXTURE_SIZE     = int(os.getenv('GAZEBO_MAX_AERIAL_TEXTURE_SIZE', '16384'))

