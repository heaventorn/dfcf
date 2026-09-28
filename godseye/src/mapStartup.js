/**
 * Keyless map startup.
 *
 * The Google Photorealistic 3D Tiles path (a metered Google/Cesium-ion
 * integration) was removed with the paid-provider cleanup. The globe always
 * boots on the free keyless stack — Esri World Imagery with OSM available in
 * the map tray.
 */

/** @returns {'osm'} The only remaining startup route. */
export function selectMapStartupRoute() {
  return 'osm';
}

/**
 * No 3D tileset is loaded: the keyless globe is the startup surface.
 * @returns {Promise<{tileset: null, route: 'osm', errors: []}>}
 */
export async function loadPhotorealisticTileset() {
  return { tileset: null, route: 'osm', errors: [] };
}
