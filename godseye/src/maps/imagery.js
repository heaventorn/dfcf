import * as Cesium from 'cesium';

// Attribution and service rights are documented in DATA_SOURCES.md.
export const ESRI_ATTRIBUTION_HTML =
  '<a href="https://www.esri.com" target="_blank" rel="noopener">Powered by Esri</a>';

export function createOsmImagery() {
  // Mainland-China networks cannot reach tile.openstreetmap.org, so the "map"
  // stack is served by the keyless Esri World Street Map instead. The ArcGIS
  // provider reads its own attribution from the service metadata.
  return Cesium.ArcGisMapServerImageryProvider.fromUrl(
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer',
    { enablePickFeatures: false },
  );
}

export function createEsriImagery() {
  return Cesium.ArcGisMapServerImageryProvider.fromUrl(
    // services.arcgisonline.com is unreachable on this network; the tile host
    // server.arcgisonline.com serves the same World_Imagery MapServer.
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer',
    {
      credit:
        'Powered by Esri — Source: Esri, Maxar, Earthstar Geographics, and the GIS User Community',
      enablePickFeatures: false,
    },
  );
}

export function createIonImagery(style, accessToken) {
  accessToken = String(accessToken || '').trim();
  if (!accessToken) throw new Error('Ion imagery requires an explicit token');
  return Cesium.IonImageryProvider.fromAssetId(style, { accessToken });
}
