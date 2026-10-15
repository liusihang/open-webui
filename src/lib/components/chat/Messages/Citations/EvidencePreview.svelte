<script lang="ts">
	import { onMount } from 'svelte';
	import { WEBUI_API_BASE_URL } from '$lib/constants';
	import { getEvidence, getEvidenceImage } from '$lib/apis/evidence';
	export let metadata: any;
	let item: any;
	let image = '';
	let error = '';
	let loading = true;
	onMount(() => {
		const controller = new AbortController();
		(async () => {
			try {
				item = await getEvidence(
					localStorage.token,
					metadata.file_id,
					metadata.evidence_ref,
					controller.signal
				);
				if (item.modality === 'image') {
					const blob = await getEvidenceImage(
						localStorage.token,
						metadata.file_id,
						metadata.evidence_ref,
						controller.signal
					);
					if (!controller.signal.aborted) image = URL.createObjectURL(blob);
				}
			} catch (e) {
				if (!controller.signal.aborted) error = '来源已更新、已删除或不再有权访问。';
			} finally {
				loading = false;
			}
		})();
		return () => {
			controller.abort();
			if (image) URL.revokeObjectURL(image);
		};
	});
</script>

{#if loading}<div class="text-xs text-gray-500">正在验证来源…</div>
{:else if error}<div class="text-xs text-amber-600" role="status">{error}</div>
{:else if item}
	<div class="my-2 flex flex-col gap-2 text-xs">
		{#if image}<img
				src={image}
				alt={item.preview || '文档图片证据'}
				class="max-h-64 max-w-full rounded object-contain"
			/>{/if}
		<a
			class="underline"
			target="_blank"
			rel="noreferrer"
			href={`${WEBUI_API_BASE_URL}/files/${encodeURIComponent(item.source.file_id)}/content${Number.isInteger(item.source.page_index) ? `#page=${item.source.page_index + 1}` : ''}`}
		>
			{item.source.name} · 版本 {item.source.file_version}
			{#if Number.isInteger(item.source.page_index)}
				· 第 {item.source.page_index + 1} 页{/if}
		</a>
		{#if item.source.anchor}<div class="break-words text-gray-500">
				位置：{JSON.stringify(item.source.anchor)}
			</div>{/if}
	</div>
{/if}
