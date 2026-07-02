import path from 'path';
import { fileURLToPath } from 'url';

import { loadPyodide } from 'pyodide';
import { setGlobalDispatcher, ProxyAgent } from 'undici';
import { access, copyFile, mkdir, readFile, readdir, rm, writeFile } from 'fs/promises';

const packages = [
	'micropip',
	'packaging',
	'requests',
	'beautifulsoup4',
	'numpy',
	'pandas',
	'matplotlib',
	'scikit-learn',
	'scipy',
	'regex',
	'sympy',
	'tiktoken',
	'seaborn',
	'pytz',
	'black',
	'openai',
	'openpyxl'
];

// Pure-Python packages whose wheels must be downloaded from PyPI and saved into
// static/pyodide/ so that the browser can install them offline via micropip.
// Packages already provided by the Pyodide distribution (click, platformdirs,
// typing_extensions, etc.) do NOT need to be listed here.
export const pypiPackages = [
	'black',
	'pathspec',
	'mypy_extensions',
	'pytokens',
	'openpyxl',
	'et_xmlfile'
];

const DEFAULT_CACHE_POLICY = 'prefer-local';
const DEFAULT_PYPI_API_BASE_URL = 'https://pypi.org/pypi';
const CACHE_POLICIES = new Set(['prefer-local', 'refresh', 'local-only']);

function resolvePath(baseDir, relativePath) {
	return path.join(baseDir, relativePath);
}

function stripTrailingSlashes(value) {
	return value.replace(/\/+$/, '');
}

export function normalizeBaseUrl(value, { trailingSlash = false } = {}) {
	const normalized = stripTrailingSlashes(new URL(value).toString());
	return trailingSlash ? `${normalized}/` : normalized;
}

function normalizeCachePolicy(value) {
	return CACHE_POLICIES.has(value) ? value : DEFAULT_CACHE_POLICY;
}

function splitIndexUrls(value) {
	return value
		.split(',')
		.map((entry) => entry.trim())
		.filter(Boolean)
		.map((entry) => normalizeBaseUrl(entry));
}

export function buildPyodideFetchConfig(env = process.env) {
	const cachePolicy = normalizeCachePolicy(env.PYODIDE_CACHE_POLICY);
	const indexURL = env.PYODIDE_INDEX_URL
		? normalizeBaseUrl(env.PYODIDE_INDEX_URL, { trailingSlash: true })
		: null;
	const pypiApiBaseUrl = normalizeBaseUrl(
		env.PYODIDE_PYPI_API_BASE_URL || DEFAULT_PYPI_API_BASE_URL
	);
	const pypiFilesBaseUrl = env.PYODIDE_PYPI_FILES_BASE_URL
		? normalizeBaseUrl(env.PYODIDE_PYPI_FILES_BASE_URL)
		: null;
	const pypiIndexUrls = env.PYODIDE_PYPI_INDEX_URLS
		? splitIndexUrls(env.PYODIDE_PYPI_INDEX_URLS)
		: [pypiApiBaseUrl];

	return {
		cachePolicy,
		indexURL,
		pypiApiBaseUrl,
		pypiFilesBaseUrl,
		pypiIndexUrls
	};
}

function buildPyodideLoadOptions(baseDir, config) {
	const options = {
		packageCacheDir: resolvePath(baseDir, 'static/pyodide')
	};
	if (config.indexURL) {
		options.indexURL = config.indexURL;
	}
	return options;
}

function buildPypiMetadataUrl(packageName, pypiApiBaseUrl) {
	return `${normalizeBaseUrl(pypiApiBaseUrl)}/${packageName}/json`;
}

export function rewriteWheelUrl(wheelUrl, pypiFilesBaseUrl) {
	if (!pypiFilesBaseUrl) {
		return wheelUrl;
	}

	const parsed = new URL(wheelUrl);
	if (parsed.hostname !== 'files.pythonhosted.org') {
		return wheelUrl;
	}

	const mirrorBase = new URL(normalizeBaseUrl(pypiFilesBaseUrl));
	let rewrittenPath = parsed.pathname;
	if (
		mirrorBase.pathname !== '/' &&
		rewrittenPath.startsWith(`${stripTrailingSlashes(mirrorBase.pathname)}/`)
	) {
		rewrittenPath = rewrittenPath.slice(stripTrailingSlashes(mirrorBase.pathname).length);
	}

	return `${normalizeBaseUrl(pypiFilesBaseUrl)}${rewrittenPath}${parsed.search}`;
}

async function fileExists(filePath) {
	try {
		await access(filePath);
		return true;
	} catch {
		return false;
	}
}

async function readJson(filePath) {
	return JSON.parse(await readFile(filePath, 'utf-8'));
}

function normalizePypiPackageName(packageName) {
	return packageName.replace(/-/g, '_');
}

function normalizeVersionRange(versionSpec) {
	return versionSpec.replace(/^[~^<>= ]+/, '');
}

async function readRequestedPyodideVersion(baseDir) {
	const installedPackagePath = resolvePath(baseDir, 'node_modules/pyodide/package.json');
	if (await fileExists(installedPackagePath)) {
		const installedPackage = await readJson(installedPackagePath);
		return installedPackage.version;
	}

	const rootPackageJson = await readJson(resolvePath(baseDir, 'package.json'));
	return normalizeVersionRange(rootPackageJson.dependencies.pyodide);
}

export async function isLocalPyodideCacheUsable(baseDir, pyodideVersion) {
	const pyodidePackagePath = resolvePath(baseDir, 'static/pyodide/package.json');
	const lockPath = resolvePath(baseDir, 'static/pyodide/pyodide-lock.json');

	if (!(await fileExists(pyodidePackagePath)) || !(await fileExists(lockPath))) {
		return false;
	}

	try {
		const localPackage = await readJson(pyodidePackagePath);
		if (localPackage.version !== pyodideVersion) {
			return false;
		}

		const lockData = await readJson(lockPath);
		if (!lockData.packages) {
			return false;
		}

		for (const pkg of pypiPackages) {
			const packageInfo = lockData.packages[normalizePypiPackageName(pkg)];
			if (!packageInfo?.file_name) {
				return false;
			}

			if (!(await fileExists(resolvePath(baseDir, `static/pyodide/${packageInfo.file_name}`)))) {
				return false;
			}
		}

		return true;
	} catch {
		return false;
	}
}

async function clearLocalPyodideCache(baseDir) {
	await rm(resolvePath(baseDir, 'static/pyodide'), { recursive: true, force: true });
}

/**
 * Loading network proxy configurations from the environment variables.
 * And the proxy config with lowercase name has the highest priority to use.
 */
export function initNetworkProxyFromEnv(env = process.env) {
	// we assume all subsequent requests in this script are HTTPS:
	// https://cdn.jsdelivr.net
	// https://pypi.org
	// https://files.pythonhosted.org
	const allProxy = env.all_proxy || env.ALL_PROXY;
	const httpsProxy = env.https_proxy || env.HTTPS_PROXY;
	const httpProxy = env.http_proxy || env.HTTP_PROXY;
	const preferredProxy = httpsProxy || allProxy || httpProxy;
	/**
	 * use only http(s) proxy because socks5 proxy is not supported currently:
	 * @see https://github.com/nodejs/undici/issues/2224
	 */
	if (!preferredProxy || !preferredProxy.startsWith('http')) return;
	let preferredProxyURL;
	try {
		preferredProxyURL = new URL(preferredProxy).toString();
	} catch {
		console.warn(`Invalid network proxy URL: "${preferredProxy}"`);
		return;
	}
	const dispatcher = new ProxyAgent({ uri: preferredProxyURL });
	setGlobalDispatcher(dispatcher);
	console.log(`Initialized network proxy "${preferredProxy}" from env`);
}

async function installMicropipPackages(micropip, config) {
	console.log('Downloading Pyodide packages:', packages);
	console.log('Using PyPI index URLs:', config.pypiIndexUrls);

	for (const pkg of packages) {
		console.log(`Installing package: ${pkg}`);
		await micropip.install(pkg, {
			index_urls: config.pypiIndexUrls
		});
	}
}

async function downloadPackages(baseDir, config) {
	console.log('Setting up pyodide + micropip');

	const requestedVersion = await readRequestedPyodideVersion(baseDir);

	if (config.cachePolicy !== 'refresh' && (await isLocalPyodideCacheUsable(baseDir, requestedVersion))) {
		console.log(`Reusing local static/pyodide cache for Pyodide ${requestedVersion}`);
		return { cacheHit: true, requestedVersion };
	}

	if (config.cachePolicy === 'local-only') {
		throw new Error(
			`Local static/pyodide cache is unavailable or stale for Pyodide ${requestedVersion}`
		);
	}

	const pyodidePackagePath = resolvePath(baseDir, 'static/pyodide/package.json');
	if (await fileExists(pyodidePackagePath)) {
		const localPackage = await readJson(pyodidePackagePath);
		if (localPackage.version !== requestedVersion) {
			console.log('Pyodide version mismatch, removing static/pyodide directory');
			await clearLocalPyodideCache(baseDir);
		}
	}

	await mkdir(resolvePath(baseDir, 'static/pyodide'), { recursive: true });

	let pyodide;
	try {
		pyodide = await loadPyodide(buildPyodideLoadOptions(baseDir, config));
	} catch (err) {
		console.error('Failed to load Pyodide:', err);
		throw err;
	}

	try {
		console.log('Loading micropip package');
		await pyodide.loadPackage('micropip');

		const micropip = pyodide.pyimport('micropip');
		try {
			await installMicropipPackages(micropip, config);
			console.log('Pyodide packages downloaded, freezing into lock file');
			const lockFile = await micropip.freeze();
			await writeFile(resolvePath(baseDir, 'static/pyodide/pyodide-lock.json'), lockFile);
		} finally {
			micropip.destroy?.();
		}
	} catch (err) {
		console.error('Failed to load or install micropip:', err);
		throw err;
	}

	return { cacheHit: false, requestedVersion };
}

async function copyPyodide(baseDir) {
	console.log('Copying Pyodide files into static directory');
	for await (const entry of await readdir(resolvePath(baseDir, 'node_modules/pyodide'))) {
		await copyFile(
			resolvePath(baseDir, `node_modules/pyodide/${entry}`),
			resolvePath(baseDir, `static/pyodide/${entry}`)
		);
	}
}

async function downloadPyPIWheels(baseDir, config) {
	const lockPath = resolvePath(baseDir, 'static/pyodide/pyodide-lock.json');
	let lockData;
	try {
		lockData = await readJson(lockPath);
	} catch {
		console.warn('Could not read pyodide-lock.json, skipping PyPI wheel download');
		return;
	}

	for (const pkg of pypiPackages) {
		console.log(`Fetching PyPI metadata for: ${pkg}`);
		const metadataUrl = buildPypiMetadataUrl(pkg, config.pypiApiBaseUrl);
		const res = await fetch(metadataUrl);
		if (!res.ok) {
			console.error(`Failed to fetch PyPI metadata for ${pkg}: ${res.status}`);
			continue;
		}
		const meta = await res.json();
		const version = meta.info.version;
		const files = meta.urls || [];
		const wheel = files.find(
			(file) => file.filename.endsWith('.whl') && file.filename.includes('py3-none-any')
		);
		if (!wheel) {
			console.warn(`No pure-Python wheel found for ${pkg}==${version}, skipping`);
			continue;
		}

		const dest = resolvePath(baseDir, `static/pyodide/${wheel.filename}`);
		try {
			await access(dest);
			console.log(`  Already exists: ${wheel.filename}`);
		} catch {
			console.log(`  Downloading: ${wheel.filename}`);
			const wheelUrl = rewriteWheelUrl(wheel.url, config.pypiFilesBaseUrl);
			const wheelRes = await fetch(wheelUrl);
			if (!wheelRes.ok) {
				console.error(`  Failed to download ${wheel.filename}: ${wheelRes.status}`);
				continue;
			}
			const buffer = Buffer.from(await wheelRes.arrayBuffer());
			await writeFile(dest, buffer);
			console.log(`  Saved: ${dest} (${buffer.length} bytes)`);
		}

		const normalizedName = normalizePypiPackageName(pkg);
		if (!lockData.packages[normalizedName]) {
			lockData.packages[normalizedName] = {
				name: normalizedName,
				version,
				file_name: wheel.filename,
				install_dir: 'site',
				sha256: wheel.digests?.sha256 || '',
				package_type: 'package',
				imports: [normalizedName],
				depends: []
			};
			console.log(`  Added ${normalizedName}==${version} to pyodide-lock.json`);
		}
	}

	await writeFile(lockPath, JSON.stringify(lockData, null, 2));
	console.log('Updated pyodide-lock.json with PyPI packages');
}

export async function main({ baseDir = '.', env = process.env } = {}) {
	initNetworkProxyFromEnv(env);
	const config = buildPyodideFetchConfig(env);
	const { cacheHit } = await downloadPackages(baseDir, config);
	if (cacheHit) {
		return;
	}
	await copyPyodide(baseDir);
	await downloadPyPIWheels(baseDir, config);
}

const isDirectRun =
	process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isDirectRun) {
	main().catch((err) => {
		console.error(err);
		process.exitCode = 1;
	});
}
