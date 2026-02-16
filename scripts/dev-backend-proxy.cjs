#!/usr/bin/env node

const http = require('node:http');
const { URL } = require('node:url');

const TARGET_ORIGIN = process.env.TARGET_ORIGIN || 'https://ai.shuofang.cloud';
const LISTEN_HOST = process.env.PROXY_HOST || '127.0.0.1';
const LISTEN_PORT = Number(process.env.PROXY_PORT || '8080');

const targetBase = new URL(TARGET_ORIGIN);

const HOP_BY_HOP_HEADERS = new Set([
	'connection',
	'keep-alive',
	'proxy-authenticate',
	'proxy-authorization',
	'te',
	'trailers',
	'transfer-encoding',
	'upgrade',
	'host'
]);

const setCorsHeaders = (res, origin) => {
	res.setHeader('Access-Control-Allow-Origin', origin || '*');
	res.setHeader('Access-Control-Allow-Credentials', 'true');
	res.setHeader('Access-Control-Allow-Methods', 'GET,POST,PUT,PATCH,DELETE,OPTIONS,HEAD');
	res.setHeader('Access-Control-Allow-Headers', 'Content-Type,Authorization,X-Requested-With');
	res.setHeader('Vary', 'Origin');
};

const server = http.createServer(async (req, res) => {
	const origin = req.headers.origin || '';

	if (req.method === 'OPTIONS') {
		setCorsHeaders(res, origin);
		res.writeHead(200);
		res.end('OK');
		return;
	}

	try {
		const targetUrl = new URL(req.url || '/', targetBase);
		const headers = new Headers();

		for (const [key, value] of Object.entries(req.headers)) {
			if (HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
				continue;
			}
			if (Array.isArray(value)) {
				for (const item of value) {
					headers.append(key, item);
				}
			} else if (value !== undefined) {
				headers.set(key, value);
			}
		}

		headers.set('host', targetBase.host);

		const upstreamRes = await fetch(targetUrl, {
			method: req.method,
			headers,
			body:
				req.method === 'GET' || req.method === 'HEAD' || req.method === 'OPTIONS'
					? undefined
					: req,
			duplex: 'half',
			redirect: 'manual'
		});

		const responseHeaders = {};
		upstreamRes.headers.forEach((value, key) => {
			if (!HOP_BY_HOP_HEADERS.has(key.toLowerCase())) {
				responseHeaders[key] = value;
			}
		});

		const isBackendConfig =
			req.method === 'GET' &&
			(new URL(req.url || '/', 'http://localhost').pathname === '/api/config') &&
			(responseHeaders['content-type'] || '').includes('application/json');

		Object.entries(responseHeaders).forEach(([key, value]) => res.setHeader(key, value));
		setCorsHeaders(res, origin);

		if (isBackendConfig) {
			const payload = await upstreamRes.json();
			if (payload?.features) {
				payload.features.enable_websocket = false;
			}

			const body = JSON.stringify(payload);
			res.setHeader('content-length', Buffer.byteLength(body));
			res.writeHead(upstreamRes.status);
			res.end(body);
			return;
		}

		res.writeHead(upstreamRes.status);
		if (upstreamRes.body) {
			for await (const chunk of upstreamRes.body) {
				res.write(chunk);
			}
		}
		res.end();
	} catch (error) {
		setCorsHeaders(res, origin);
		res.writeHead(502, { 'Content-Type': 'application/json' });
		res.end(JSON.stringify({ error: 'proxy_error', detail: String(error) }));
	}
});

server.listen(LISTEN_PORT, LISTEN_HOST, () => {
	console.log(`Proxy listening on http://${LISTEN_HOST}:${LISTEN_PORT} -> ${TARGET_ORIGIN}`);
});
