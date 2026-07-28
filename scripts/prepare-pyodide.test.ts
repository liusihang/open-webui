import { mkdtemp, mkdir, readFile, rm, writeFile } from 'fs/promises';
import os from 'os';
import path from 'path';

import { afterEach, describe, expect, it } from 'vitest';

import {
	buildLockPackageDepends,
	buildPypiMetadataUrls,
	buildPyodideFetchConfig,
	isLocalPyodideCacheUsable,
	packages,
	parsePackageSpec,
	pypiPackages,
	pyodidePackages,
	rewriteWheelUrl
} from './prepare-pyodide';

const tempDirs: string[] = [];

async function makeTempDir() {
	const dir = await mkdtemp(path.join(os.tmpdir(), 'prepare-pyodide-test-'));
	tempDirs.push(dir);
	return dir;
}

async function writeJson(filePath: string, value: unknown) {
	await mkdir(path.dirname(filePath), { recursive: true });
	await writeFile(filePath, JSON.stringify(value, null, 2));
}

async function writePyodideCache(dir: string, version: string, cachedPackages = pypiPackages) {
	const pyodideDir = path.join(dir, 'static/pyodide');
	const packageFiles = Object.fromEntries(
		cachedPackages.map((packageSpec) => {
			const { packageName, normalizedName } = parsePackageSpec(packageSpec);
			return [normalizedName, `${packageName}-1.0.0-py3-none-any.whl`];
		})
	);

	await writeJson(path.join(pyodideDir, 'package.json'), { version });
	await writeJson(path.join(pyodideDir, 'pyodide-lock.json'), {
		packages: Object.fromEntries(
			Object.entries(packageFiles).map(([name, fileName]) => [
				name,
				{
					name,
					version: '1.0.0',
					file_name: fileName
				}
			])
		)
	});
	await Promise.all(
		Object.values(packageFiles).map((fileName) => writeFile(path.join(pyodideDir, fileName), ''))
	);
}

afterEach(async () => {
	await Promise.all(tempDirs.splice(0).map((dir) => rm(dir, { recursive: true, force: true })));
});

describe('buildPyodideFetchConfig', () => {
	it('defaults to prefer-local policy and public upstreams', () => {
		const config = buildPyodideFetchConfig({});

		expect(config.cachePolicy).toBe('prefer-local');
		expect(config.pypiApiBaseUrl).toBe('https://pypi.org/pypi');
		expect(config.pypiFilesBaseUrl).toBe(null);
		expect(config.pypiIndexUrls).toEqual(['https://pypi.org/pypi']);
	});

	it('normalizes configured mirror URLs', () => {
		const config = buildPyodideFetchConfig({
			PYODIDE_CACHE_POLICY: 'local-only',
			PYODIDE_INDEX_URL: 'https://mirror.example.com/pyodide',
			PYODIDE_PYPI_API_BASE_URL: 'https://pypi.example.com/pypi',
			PYODIDE_PYPI_FILES_BASE_URL: 'https://files.example.com/packages'
		});

		expect(config.cachePolicy).toBe('local-only');
		expect(config.indexURL).toBe('https://mirror.example.com/pyodide/');
		expect(config.pypiApiBaseUrl).toBe('https://pypi.example.com/pypi');
		expect(config.pypiFilesBaseUrl).toBe('https://files.example.com/packages');
		expect(config.pypiIndexUrls).toEqual(['https://pypi.example.com/pypi']);
	});
});

describe('buildPypiMetadataUrls', () => {
	it('tries mirror-compatible slug variants before falling back to upstream', () => {
		expect(buildPypiMetadataUrls('et_xmlfile', 'https://pypi.tuna.tsinghua.edu.cn/pypi')).toEqual([
			'https://pypi.tuna.tsinghua.edu.cn/pypi/et_xmlfile/json',
			'https://pypi.tuna.tsinghua.edu.cn/pypi/et-xmlfile/json',
			'https://pypi.org/pypi/et_xmlfile/json',
			'https://pypi.org/pypi/et-xmlfile/json'
		]);
	});

	it('uses version-specific metadata URLs for pinned packages', () => {
		expect(
			buildPypiMetadataUrls('black', 'https://pypi.tuna.tsinghua.edu.cn/pypi', '25.1.0')
		).toEqual([
			'https://pypi.tuna.tsinghua.edu.cn/pypi/black/25.1.0/json',
			'https://pypi.org/pypi/black/25.1.0/json'
		]);
	});
});

describe('package selection', () => {
	it('pins black before the native-only pytokens dependency entered its runtime dependency tree', () => {
		expect(packages).toContain('black');
		expect(pyodidePackages).not.toContain('black');
		expect(pyodidePackages).not.toContain('black==25.1.0');
		expect(pypiPackages).toContain('black==25.1.0');
		expect(pypiPackages).not.toContain('black');
		expect(pypiPackages).not.toContain('pytokens');
	});

	it('pins seaborn to a pure-wheel release for Pyodide cold builds', () => {
		expect(packages).toContain('seaborn');
		expect(pyodidePackages).not.toContain('seaborn');
		expect(pyodidePackages).not.toContain('seaborn==0.13.2');
		expect(pypiPackages).toContain('seaborn==0.13.2');
	});

	it('keeps pure PyPI wheels out of the build-time micropip install pass', () => {
		expect(pyodidePackages).not.toContain('openpyxl');
		expect(pypiPackages).toContain('openpyxl');
		expect(pypiPackages).toContain('et_xmlfile');
	});
});

describe('buildLockPackageDepends', () => {
	it('uses local lock names for PyPI wheel dependencies and ignores extras', () => {
		const lockNameByProjectName = new Map([
			['mypy-extensions', 'mypy_extensions'],
			['et-xmlfile', 'et_xmlfile']
		]);

		expect(
			buildLockPackageDepends(
				[
					'numpy>=1.20',
					'mypy-extensions>=0.4.3',
					'et-xmlfile',
					'pytest ; extra == "dev"',
					'typing-extensions>=4.0.1; python_version < "3.11"'
				],
				lockNameByProjectName
			)
		).toEqual(['numpy', 'mypy_extensions', 'et_xmlfile', 'typing-extensions']);
	});
});

describe('isLocalPyodideCacheUsable', () => {
	it('accepts a matching local artifact', async () => {
		const dir = await makeTempDir();
		await writePyodideCache(dir, '0.28.2');

		await expect(isLocalPyodideCacheUsable(dir, '0.28.2')).resolves.toBe(true);
	});

	it('rejects a version mismatch even when files exist', async () => {
		const dir = await makeTempDir();
		await writeJson(path.join(dir, 'static/pyodide/package.json'), { version: '0.28.1' });
		await writeJson(path.join(dir, 'static/pyodide/pyodide-lock.json'), { packages: {} });

		await expect(isLocalPyodideCacheUsable(dir, '0.28.2')).resolves.toBe(false);
	});

	it('rejects a matching local artifact when required pure-Python PyPI packages are missing', async () => {
		const dir = await makeTempDir();
		await writePyodideCache(
			dir,
			'0.28.2',
			pypiPackages.filter((name) => !['openpyxl', 'et_xmlfile'].includes(name))
		);

		await expect(isLocalPyodideCacheUsable(dir, '0.28.2')).resolves.toBe(false);
	});
});

describe('rewriteWheelUrl', () => {
	it('rewrites pythonhosted wheel URLs to the configured files mirror', () => {
		expect(
			rewriteWheelUrl(
				'https://files.pythonhosted.org/packages/ab/cd/example.whl',
				'https://mirror.example.com/packages'
			)
		).toBe('https://mirror.example.com/packages/ab/cd/example.whl');
	});

	it('leaves wheel URLs untouched when no files mirror is configured', () => {
		const url = 'https://files.pythonhosted.org/packages/ab/cd/example.whl';
		expect(rewriteWheelUrl(url, null)).toBe(url);
	});
});
