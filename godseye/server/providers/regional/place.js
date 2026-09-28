import { makeRateLimiter, clientKey } from '../common/rate-limit.js';
import { coalesceProxyRequest } from '../common/http.js';
import { fetchRegionalJson } from './http.js';
import { normalizeRegionalPlace } from '../../../src/data/regionalModel.js';
import {
  nominatimToGeocodeResult,
  nominatimViewboxFromBounds,
} from '../../../src/nominatimGeocode.js';

/**
 * The usage policy for the public Nominatim instance asks for a User-Agent or
 * Referer that identifies the application, and states that stock library
 * agents will not do. Both are sent.
 */
const NOMINATIM_HEADERS = Object.freeze({
  'User-Agent':
    'gods-eye-view/0.1 (+https://github.com/bilawalsidhu/gods-eye-view)',
  Referer: 'https://github.com/bilawalsidhu/gods-eye-view',
});

/**
 * Minimum spacing between upstream calls. The policy states an absolute
 * maximum of one request per second; 1.1 s keeps clock jitter from crossing it.
 */
const NOMINATIM_MIN_SPACING_MS = 1100;

/**
 * How many searches may be waiting for their turn.
 *
 * One request per second and an unbounded queue are incompatible: a burst of
 * searches would keep the upstream busy long after everyone who asked has given
 * up, which is exactly the load the policy asks callers not to create. Past
 * this depth a search is refused at once instead of being promised a slot
 * minutes away.
 */
const NOMINATIM_MAX_PENDING = 4;

/**
 * How long a queued search may wait before it is not worth sending. The
 * browser gives up well before this, so anything reaching the front later than
 * this is answering nobody.
 */
const NOMINATIM_MAX_WAIT_MS = 10_000;

const NOMINATIM_SEARCH_CACHE_MS = 5 * 60_000;

const NOMINATIM_SEARCH_MAX_CACHE = 80;

const NOMINATIM_SEARCH_MAX_QUERY = 200;

/**
 * Photon (komoot) is the keyless, OSM-derived geocoder the browser half of the
 * application already uses. Mainland-China networks cannot reach
 * nominatim.openstreetmap.org at all, so the server side needs a reachable
 * service too: whichever way round the chain runs, one of the two has to
 * answer. Photon replies with a GeoJSON FeatureCollection, and each feature is
 * translated into the Nominatim JSONv2 shape below so every downstream rule
 * (result framing, place context) keeps working untouched.
 */
const NOMINATIM_SEARCH_ENDPOINT = 'https://nominatim.openstreetmap.org/search';

const NOMINATIM_REVERSE_ENDPOINT = 'https://nominatim.openstreetmap.org/reverse';

const PHOTON_SEARCH_ENDPOINT = 'https://photon.komoot.io/api';

const PHOTON_REVERSE_ENDPOINT = 'https://photon.komoot.io/reverse';

const PHOTON_HEADERS = Object.freeze({ Accept: 'application/json' });

const PHOTON_LANGUAGE = 'en';

/** One cleaned Photon text field, or an empty string. */
function photonClean(value, maxLength = 120) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, maxLength);
}

/**
 * Photon's `extent` is `[west, north, east, south]`; Nominatim's `boundingbox`
 * is `[south, north, west, east]`. Reading either the naive way produces a box
 * that still looks valid and frames the wrong hemisphere, so the swap is done
 * once, here.
 */
function photonBoundingBox(extent) {
  if (!Array.isArray(extent) || extent.length !== 4) return null;
  const [west, north, east, south] = extent.map(Number);
  if (![west, north, east, south].every(Number.isFinite)) return null;
  if (Math.abs(north) > 90 || Math.abs(south) > 90) return null;
  if (Math.abs(west) > 180 || Math.abs(east) > 180) return null;
  if (west > east) return null;
  return [String(south), String(north), String(west), String(east)];
}

/**
 * One Photon feature as a Nominatim hit, or null when it carries no usable
 * point. Photon keeps the containing places in separate fields and Nominatim
 * nests them under `address`, which is where the reverse-geocode context reads
 * them from.
 */
export function photonFeatureToNominatimHit(feature) {
  const coordinates = feature?.geometry?.coordinates;
  if (!Array.isArray(coordinates) || coordinates.length < 2) return null;
  const lon = Number(coordinates[0]);
  const lat = Number(coordinates[1]);
  if (!Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  if (Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;

  const properties = feature.properties || {};
  const locality = photonClean(
    properties.city ||
      properties.town ||
      properties.village ||
      properties.district ||
      properties.county,
    90,
  );
  const region = photonClean(properties.state || properties.region, 90);
  const country = photonClean(properties.country, 90);
  const countryCode = photonClean(properties.countrycode, 4).toLowerCase();
  const street = [
    photonClean(properties.street, 90),
    photonClean(properties.housenumber, 20),
  ]
    .filter(Boolean)
    .join(' ');
  const name = photonClean(properties.name, 120) || street || locality;

  const label = [];
  for (const part of [
    name,
    street,
    photonClean(properties.postcode, 20),
    locality,
    region,
    country,
  ]) {
    if (part && !label.includes(part)) label.push(part);
  }

  const boundingbox = photonBoundingBox(properties.extent);
  return {
    lat: String(lat),
    lon: String(lon),
    display_name: label.join(', '),
    name,
    class: photonClean(properties.osm_key, 40),
    type: photonClean(properties.osm_value || properties.type, 40),
    addresstype: photonClean(
      properties.type || properties.osm_value,
      40,
    ).toLowerCase(),
    ...(boundingbox ? { boundingbox } : {}),
    address: {
      ...(locality ? { city: locality } : {}),
      ...(region ? { state: region } : {}),
      ...(country ? { country } : {}),
      ...(countryCode ? { country_code: countryCode } : {}),
      ...(street ? { road: street } : {}),
    },
  };
}

/** One Photon request, reduced to the Nominatim-shaped hits its callers read. */
async function fetchPhotonHits(url, params, requestJson) {
  const payload = await requestJson(`${url}?${params}`, {
    headers: PHOTON_HEADERS,
    redirect: 'error',
  });
  const features = Array.isArray(payload?.features) ? payload.features : [];
  return features
    .map(photonFeatureToNominatimHit)
    .filter((hit) => hit !== null);
}

/**
 * Photon's proximity bias, read from the `viewbox=west,north,east,south` the
 * Nominatim call already builds. Photon biases by point, and a rectangle
 * translated literally into `bbox` would be a hard filter that hides every
 * result outside it.
 */
function photonBiasParams(viewbox) {
  const params = new URLSearchParams();
  if (!viewbox) return params;
  const parts = viewbox.split(',').map(Number);
  if (parts.length !== 4 || !parts.every(Number.isFinite)) return params;
  const [west, north, east, south] = parts;
  params.set('lat', String((north + south) / 2));
  params.set('lon', String((west + east) / 2));
  return params;
}

// The pacer is deliberately module state while everything else here is
// per-instance. One request per second is a budget for the whole application,
// not for each provider object: two instances each pacing themselves would
// send two requests a second between them. Reverse lookups and searches
// therefore queue together.
let _nominatimQueue = Promise.resolve();

let _nominatimLastRequestAt = 0;

let _nominatimPending = 0;

/** Raised when the queue is already as deep as it is allowed to get. */
function queueFullError() {
  return Object.assign(new Error('Place search queue is full'), {
    code: 'NOMINATIM_QUEUE_FULL',
  });
}

/** Raised when a queued search waited so long that nobody is left to answer. */
function abandonedError() {
  return Object.assign(new Error('Place search was abandoned'), {
    code: 'NOMINATIM_ABANDONED',
  });
}

/**
 * Run one piece of upstream work, never closer than the policy spacing to the
 * last one.
 *
 * `bounded` applies the queue-depth and staleness limits. The cockpit's reverse
 * lookups are a slow background trickle and stay unbounded; the search route,
 * which a person can fire as fast as they can type, does not.
 *
 * @param {() => Promise<unknown>} work
 * @param {{bounded?: boolean, signal?: AbortSignal}} [options]
 */
function enqueueNominatim(work, { bounded = false, signal } = {}) {
  if (bounded && _nominatimPending >= NOMINATIM_MAX_PENDING)
    return Promise.reject(queueFullError());
  if (bounded) _nominatimPending += 1;
  const queuedAt = Date.now();
  const task = _nominatimQueue.then(async () => {
    try {
      const waitMs = Math.max(
        0,
        NOMINATIM_MIN_SPACING_MS - (Date.now() - _nominatimLastRequestAt),
      );
      if (waitMs) await new Promise((resolve) => setTimeout(resolve, waitMs));
      // Nobody is waiting for this any more: sending it would spend the one
      // request per second the policy allows on an answer with no reader.
      if (bounded && Date.now() - queuedAt > NOMINATIM_MAX_WAIT_MS)
        throw abandonedError();
      if (signal?.aborted) throw abandonedError();
      _nominatimLastRequestAt = Date.now();
      return await work();
    } finally {
      if (bounded) _nominatimPending -= 1;
    }
  });
  _nominatimQueue = task.catch(() => null);
  return task;
}

/**
 * Construct the serialized Nominatim reverse adapter with a trusted endpoint.
 *
 * The reachable keyless service leads and the policy-paced one remains the
 * fallback: a reverse lookup is a background trickle, and waiting out a
 * nine-second connect to a host this network cannot reach would hold the
 * cockpit's location label open for no reason. A caller that names its own
 * `endpoint` gets exactly that endpoint and no fallback.
 */
export function createRegionalPlaceProvider({
  endpoint = NOMINATIM_REVERSE_ENDPOINT,
  requestJson = fetchRegionalJson,
  keylessFallback = endpoint === NOMINATIM_REVERSE_ENDPOINT,
} = {}) {
  function fetchRegionalPlace(point) {
    return enqueueNominatim(async () => {
      if (keylessFallback) {
        try {
          const hits = await fetchPhotonHits(
            PHOTON_REVERSE_ENDPOINT,
            new URLSearchParams({
              lat: point.latitude.toFixed(5),
              lon: point.longitude.toFixed(5),
              lang: PHOTON_LANGUAGE,
            }),
            requestJson,
          );
          // An empty answer is a verdict on the coordinate; only a failed
          // request — or a hit that carries no usable place context — sends the
          // lookup on to the second service.
          const place = hits.length ? normalizeRegionalPlace(hits[0]) : null;
          if (!hits.length || place) return place;
        } catch {
          /* fall through to Nominatim */
        }
      }
      const params = new URLSearchParams({
        format: 'jsonv2',
        lat: point.latitude.toFixed(5),
        lon: point.longitude.toFixed(5),
        zoom: '10',
        addressdetails: '1',
        'accept-language': 'en',
      });
      const payload = await requestJson(`${endpoint}?${params}`, {
        headers: NOMINATIM_HEADERS,
        redirect: 'error',
      });
      return normalizeRegionalPlace(payload);
    });
  }

  return fetchRegionalPlace;
}

export const fetchRegionalPlace = createRegionalPlaceProvider();

/**
 * Construct the forward search adapter with a trusted endpoint.
 *
 * The policy asks that results be cached, and warns that a client repeating the
 * same query may be treated as faulty, so an answer is remembered and identical
 * searches already in flight share one upstream call rather than queueing
 * behind each other.
 */
export function createNominatimSearchProvider({
  endpoint = NOMINATIM_SEARCH_ENDPOINT,
  requestJson = fetchRegionalJson,
  keylessFallback = endpoint === NOMINATIM_SEARCH_ENDPOINT,
} = {}) {
  const cache = new Map();
  const inFlight = new Map();

  const trimCache = () => {
    while (cache.size > NOMINATIM_SEARCH_MAX_CACHE) {
      const oldest = cache.keys().next().value;
      if (oldest === undefined) break;
      cache.delete(oldest);
    }
  };

  return async function fetchNominatimSearch(query, bounds, { signal } = {}) {
    const cacheKey = `${query.toLowerCase()}|${bounds || ''}`;
    const cached = cache.get(cacheKey);
    if (cached && Date.now() - cached.cachedAt <= NOMINATIM_SEARCH_CACHE_MS) {
      return { ...cached.payload, cached: true };
    }
    const { promise } = coalesceProxyRequest(inFlight, cacheKey, async () => {
      const params = new URLSearchParams({
        format: 'jsonv2',
        q: query,
        addressdetails: '1',
        limit: '1',
        'accept-language': 'en',
      });
      const viewbox = nominatimViewboxFromBounds(bounds);
      if (viewbox) params.set('viewbox', viewbox);
      // The paced call goes first and the queue bound still decides how much of
      // a burst can be in flight, because that is what keeps a typed-ahead
      // search box from hammering the public instance. Photon answers only when
      // Nominatim could not; a queue refusal or a caller that hung up is not a
      // reason to ask anybody else.
      let rows;
      try {
        rows = await enqueueNominatim(
          () =>
            requestJson(`${endpoint}?${params}`, {
              headers: NOMINATIM_HEADERS,
              redirect: 'error',
            }),
          { bounded: true, signal },
        );
      } catch (error) {
        if (
          !keylessFallback ||
          signal?.aborted ||
          error?.code === 'NOMINATIM_QUEUE_FULL' ||
          error?.code === 'NOMINATIM_ABANDONED'
        )
          throw error;
        const photonParams = photonBiasParams(viewbox);
        photonParams.set('q', query);
        photonParams.set('limit', '1');
        photonParams.set('lang', PHOTON_LANGUAGE);
        rows = await fetchPhotonHits(
          PHOTON_SEARCH_ENDPOINT,
          photonParams,
          requestJson,
        );
      }
      const result = nominatimToGeocodeResult(
        Array.isArray(rows) ? rows[0] : null,
      );
      const payload = result
        ? { status: 'OK', results: [result] }
        : { status: 'ZERO_RESULTS', results: [] };
      cache.set(cacheKey, { payload, cachedAt: Date.now() });
      trimCache();
      return payload;
    });
    return await promise;
  };
}

export const fetchNominatimSearch = createNominatimSearchProvider();

/** Vite plugin: last-resort place search over the public Nominatim instance. */
export function geocodeProxy({ search = fetchNominatimSearch } = {}) {
  const limiter = makeRateLimiter({
    windowMs: 60_000,
    max: 30,
    globalMax: 90,
  });

  function install(middlewares) {
    middlewares.use('/api/geocode', async (req, res) => {
      if (req.method !== 'GET') {
        res.writeHead(405, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ error: 'Method Not Allowed' }));
        return;
      }
      if (!limiter(clientKey(req))) {
        res.writeHead(429, {
          'Content-Type': 'application/json',
          'Retry-After': '10',
        });
        res.end(JSON.stringify({ error: 'Rate limit exceeded' }));
        return;
      }
      const url = new URL(req.url || '', 'http://localhost');
      const query = String(url.searchParams.get('q') || '').trim();
      if (!query || query.length > NOMINATIM_SEARCH_MAX_QUERY) {
        res.writeHead(400, { 'Content-Type': 'application/json' });
        res.end(
          JSON.stringify({
            error: 'A place query of 1-200 characters is required',
          }),
        );
        return;
      }
      // A browser that gave up is no longer waiting; the queue reads this
      // before spending its slot.
      const abandoned = new AbortController();
      req.on?.('aborted', () => abandoned.abort());
      res.on?.('close', () => abandoned.abort());
      try {
        const payload = await search(query, url.searchParams.get('bounds'), {
          signal: abandoned.signal,
        });
        if (res.writableEnded) return;
        res.writeHead(200, {
          'Content-Type': 'application/json',
          'Cache-Control': payload.cached ? 'public, max-age=60' : 'no-store',
        });
        res.end(
          JSON.stringify({ status: payload.status, results: payload.results }),
        );
      } catch (error) {
        if (res.writableEnded) return;
        const busy =
          error?.code === 'NOMINATIM_QUEUE_FULL' ||
          error?.code === 'NOMINATIM_ABANDONED';
        res.writeHead(busy ? 429 : 503, {
          'Content-Type': 'application/json',
          'Cache-Control': 'no-store',
          ...(busy ? { 'Retry-After': '5' } : {}),
        });
        res.end(
          JSON.stringify({
            error: busy
              ? 'Place search is busy'
              : 'Place search is temporarily unavailable',
          }),
        );
      }
    });
  }

  return {
    name: 'geocode-proxy',
    configureServer(server) {
      install(server.middlewares);
    },
    configurePreviewServer(server) {
      install(server.middlewares);
    },
  };
}

export { NOMINATIM_MAX_PENDING };
