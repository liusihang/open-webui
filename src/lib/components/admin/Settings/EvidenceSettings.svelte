<script lang="ts">
	import { onMount } from 'svelte';
	import { getEvidenceSettings, saveEvidenceSettings } from '$lib/apis/evidence';
	let value: any;
	let error = '';
	let saved = false;
	onMount(async () => {
		try {
			value = await getEvidenceSettings(localStorage.token);
		} catch (e) {
			error = String(e);
		}
	});
	async function save() {
		error = '';
		saved = false;
		try {
			value = await saveEvidenceSettings(localStorage.token, value.settings);
			saved = true;
		} catch (e) {
			error = String(e);
		}
	}
</script>

<section class="my-4 space-y-2 border-t border-gray-100 pt-4 text-sm dark:border-gray-800">
	<h3 class="font-medium">图文证据检索</h3>
	{#if value}
		<label class="flex items-center gap-2"
			><input
				type="checkbox"
				bind:checked={value.settings['rag.extract_document_image_assets']}
			/>提取 Office / PDF 嵌图</label
		>
		<label class="block"
			>PDF 图片来源
			<select
				class="ml-2 rounded bg-gray-50 px-2 py-1 dark:bg-gray-850"
				bind:value={value.settings['rag.pdf_image_asset_source']}
			>
				<option value="parser">当前解析器（同次响应，不重复 OCR）</option><option value="local"
					>本地 PDF 原生嵌图</option
				>
			</select>
		</label>
		<label class="block"
			>PaddleOCR API 协议
			<select
				class="ml-2 rounded bg-gray-50 px-2 py-1 dark:bg-gray-850"
				bind:value={value.settings['rag.paddleocr_vl_api_mode']}
			>
				<option value="layout">Layout</option><option value="jobs">异步 Jobs（Live 协议）</option>
			</select>
		</label>
		<label class="block"
			>检索编码空间
			<select
				class="mt-1 w-full rounded bg-gray-50 px-2 py-1 dark:bg-gray-850"
				bind:value={value.settings['rag.evidence_vector_space_id']}
			>
				<option value="">未选择：不启用未知向量</option>
				{#each value.spaces as space}<option value={space.id}
						>{space.model} · {space.dimensions ?? '?'} 维 · {space.status} · {space.id}</option
					>{/each}
			</select>
		</label>
		<p class="text-xs text-gray-500">
			运行状态：{value.runtime
				.status}。只有已核实来源且与当前编码配置兼容的空间可用；选择模型名不会自动确认旧向量。此处不重建索引，不更改当前文档解析器。
		</p>
		<button type="button" class="rounded bg-gray-100 px-3 py-1 dark:bg-gray-800" on:click={save}
			>保存图文设置</button
		>
		{#if saved}<span role="status">已保存</span>{/if}
	{/if}
	{#if error}<p role="alert" class="text-red-500">{error}</p>{/if}
</section>
