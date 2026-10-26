type RecordValue = Record<string, any>;

// A source file and its processing jobs share one row, keyed by asset ID.
export function mergeImportRows(assets: RecordValue[], jobs: RecordValue[]) {
  const grouped = new Map<string, RecordValue[]>();
  for (const job of jobs) {
    const key = job.asset_id || `job:${job.id}`;
    grouped.set(key, [...(grouped.get(key) || []), job]);
  }
  const rowFor = (asset: RecordValue | undefined, history: RecordValue[]) => {
    const ordered = [...history].sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')) || String(b.id).localeCompare(String(a.id)));
    const latest = ordered[0];
    // An active job must remain visible during asset/job refresh transitions.
    const active = ordered.find(job => ['queued', 'running'].includes(job.status));
    const job = active || latest;
    return {
      ...(asset || {}), id: asset?.id || `job:${job.id}`, asset, jobs: ordered, job,
      title: asset?.title || job?.title || '未命名资料', brain: asset?.brain || job?.brain,
      processing_status: active?.status || asset?.processing_status || job?.processing_status || 'unknown',
      publication_status: asset?.publication_status || job?.publication_status || 'not_ready',
      created_at: asset?.created_at || job?.created_at,
    };
  };
  const rows = assets.map(asset => {
    const history = grouped.get(asset.id) || [];
    grouped.delete(asset.id);
    return rowFor(asset, history);
  });
  for (const history of grouped.values()) rows.push(rowFor(undefined, history));
  return rows.sort((a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')) || String(b.id).localeCompare(String(a.id)));
}
