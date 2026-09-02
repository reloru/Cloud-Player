/**
 * Cloud Player - Cloudflare Worker.
 *
 * Serves the PWA from the ASSETS binding and proxies /api/* to the music
 * API on the Ubuntu VM through the MUSIC_API Workers VPC Service binding.
 * The VPC Service can only reach the one host:port registered against the
 * "VM" tunnel, so the music API is never addressable from the internet and
 * every request to it passes through the checks below.
 *
 * Browsing, streaming, cover art and downloads are public. Upload, delete
 * and metadata edits require `Authorization: Bearer <AUTH_PASSWORD>`,
 * compared against the AUTH_PASSWORD secret.
 */

interface Env {
	/** Static assets for the PWA (worker/public). */
	ASSETS: Fetcher;
	/** VPC Service bound to localhost:8000 on the "VM" tunnel. */
	MUSIC_API: Fetcher;
	/** Set with `wrangler secret put AUTH_PASSWORD`. Absent means deny. */
	AUTH_PASSWORD?: string;
}

/**
 * The VPC Service routes to its registered target regardless of the
 * authority here, but the binding still needs a well-formed absolute URL.
 * This matches the address the service was created against.
 */
const MUSIC_API_ORIGIN = 'http://localhost:8000';

/** Track ids are unpadded base64url of the filename, so this is the full alphabet. */
const ID = '[A-Za-z0-9_-]+';

interface Route {
	pattern: RegExp;
	methods: readonly string[];
	auth: boolean;
}

const ROUTES: readonly Route[] = [
	{ pattern: new RegExp('^/api/songs$'), methods: ['GET', 'HEAD'], auth: false },
	{
		pattern: new RegExp(`^/api/(?:stream|download|cover)/${ID}$`),
		methods: ['GET', 'HEAD'],
		auth: false,
	},
	{ pattern: new RegExp('^/api/upload$'), methods: ['POST'], auth: true },
	{ pattern: new RegExp(`^/api/metadata/${ID}$`), methods: ['PUT'], auth: true },
	{ pattern: new RegExp(`^/api/delete/${ID}$`), methods: ['DELETE'], auth: true },
	{ pattern: new RegExp('^/api/health$'), methods: ['GET', 'HEAD'], auth: true },
];

/**
 * Request headers forwarded to the VM. An allowlist rather than a denylist:
 * it keeps hop-by-hop headers (connection, transfer-encoding, keep-alive,
 * upgrade) out by construction, and makes sure the client's Authorization
 * header is never relayed to a service that does not use it.
 */
const FORWARDED_REQUEST_HEADERS = [
	'range',
	'if-range',
	'if-none-match',
	'if-modified-since',
	'accept',
	'content-type',
	'content-length',
] as const;

/** Response headers passed back to the client, for the same reasons. */
const FORWARDED_RESPONSE_HEADERS = [
	'content-type',
	'content-length',
	'content-range',
	'content-disposition',
	'accept-ranges',
	'etag',
	'last-modified',
	'cache-control',
	'vary',
] as const;

const MUSIC_API_DOWN = 'music API unreachable';
const MUSIC_API_HINT =
	'Check that music_api.py is running on the VM (systemctl status cloud-player-api) ' +
	'and that the "VM" tunnel is healthy.';

/** Statuses that must not carry a response body. */
const BODILESS_STATUSES = new Set([101, 204, 205, 304]);

function json(status: number, payload: unknown, extra?: HeadersInit): Response {
	const headers = new Headers(extra);
	headers.set('Content-Type', 'application/json; charset=utf-8');
	headers.set('Cache-Control', 'no-store');
	return new Response(JSON.stringify(payload), { status, headers });
}

async function sha256(value: string): Promise<ArrayBuffer> {
	return crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
}

/**
 * Constant-time bearer check.
 *
 * `crypto.subtle.timingSafeEqual` throws on operands of differing length, so
 * both sides are hashed first: SHA-256 digests are always 32 bytes, which
 * keeps the comparison itself constant-time and stops the response time from
 * revealing the length of the configured password.
 */
async function isAuthorized(request: Request, env: Env): Promise<boolean> {
	const expected = env.AUTH_PASSWORD;
	if (typeof expected !== 'string' || expected.length === 0) {
		return false;
	}
	const header = request.headers.get('Authorization');
	if (!header) {
		return false;
	}
	const match = /^Bearer[ \t]+(.+)$/i.exec(header.trim());
	if (!match) {
		return false;
	}
	const [presented, configured] = await Promise.all([sha256(match[1]), sha256(expected)]);
	return crypto.subtle.timingSafeEqual(presented, configured);
}

async function proxyToMusicApi(request: Request, env: Env, url: URL): Promise<Response> {
	const headers = new Headers();
	for (const name of FORWARDED_REQUEST_HEADERS) {
		const value = request.headers.get(name);
		if (value !== null) {
			headers.set(name, value);
		}
	}

	// Pass the path and query through byte for byte: the track id and the
	// upload filename are percent-encoded and must not be re-encoded.
	const target = `${MUSIC_API_ORIGIN}${url.pathname}${url.search}`;
	const hasBody = request.method !== 'GET' && request.method !== 'HEAD';

	let upstream: Response;
	try {
		upstream = await env.MUSIC_API.fetch(target, {
			method: request.method,
			headers,
			// Streamed straight through rather than buffered: a Worker has
			// 128 MB of memory and uploads can approach Cloudflare's 100 MB
			// request body cap.
			body: hasBody ? request.body : null,
		});
	} catch (error) {
		const detail = error instanceof Error ? error.message : String(error);
		console.warn('MUSIC_API fetch threw', detail);
		return json(502, { error: MUSIC_API_DOWN, detail, hint: MUSIC_API_HINT });
	}

	// When the tunnel cannot reach localhost:8000 the VPC Service does not
	// reject - it resolves with a 5xx of its own. Relaying that verbatim gives a
	// blank 500 with nothing to act on. music_api.py stamps X-Music-API on
	// everything it sends, so a 5xx without it did not come from the VM, and the
	// VM's own 500s still pass through untouched.
	if (upstream.status >= 500 && upstream.headers.get('X-Music-API') === null) {
		console.warn('MUSIC_API unreachable, upstream status', upstream.status);
		return json(502, {
			error: MUSIC_API_DOWN,
			upstreamStatus: upstream.status,
			hint: MUSIC_API_HINT,
		});
	}

	const responseHeaders = new Headers();
	for (const name of FORWARDED_RESPONSE_HEADERS) {
		const value = upstream.headers.get(name);
		if (value !== null) {
			responseHeaders.set(name, value);
		}
	}

	const bodiless = BODILESS_STATUSES.has(upstream.status) || request.method === 'HEAD';
	return new Response(bodiless ? null : upstream.body, {
		status: upstream.status,
		statusText: upstream.statusText,
		headers: responseHeaders,
	});
}

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		const url = new URL(request.url);

		if (!url.pathname.startsWith('/api/')) {
			return env.ASSETS.fetch(request);
		}

		const route = ROUTES.find((candidate) => candidate.pattern.test(url.pathname));
		if (!route) {
			return json(404, { error: 'no such endpoint' });
		}
		if (!route.methods.includes(request.method)) {
			return json(405, { error: 'method not allowed' }, { Allow: route.methods.join(', ') });
		}
		if (route.auth && !(await isAuthorized(request, env))) {
			return json(
				401,
				{ error: 'authentication required' },
				{ 'WWW-Authenticate': 'Bearer realm="cloud-player"' },
			);
		}

		return proxyToMusicApi(request, env, url);
	},
} satisfies ExportedHandler<Env>;
