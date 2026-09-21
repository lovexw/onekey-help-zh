// Cloudflare Pages Function: POST /api/search-log
// Records every search into Cloudflare's静态请求日志之外的可追踪存储。
// Free-tier friendly: uses Pages Functions + KV binding (WRAPPED: optional).
// If KV binding SEARCH_LOGS is not bound, falls back to returning 202 and the
// client keeps its localStorage copy — logging degrades gracefully.
export const onRequestPost = async ({ request, env }) => {
  let body = {};
  try { body = await request.json(); } catch (e) { /* noop */ }
  const q = String(body.q || '').slice(0, 100);
  const n = Number.isFinite(body.n) ? body.n : -1;
  const ms = Number.isFinite(body.ms) ? body.ms : -1;
  const ua = request.headers.get('user-agent') || '';
  const cf = request.cf || {};
  const entry = {
    q, n, ms,
    t: new Date().toISOString(),
    country: cf.country || '',
    ua: ua.slice(0, 120),
  };
  if (env && env.SEARCH_LOGS) {
    const day = entry.t.slice(0, 10);
    const key = `log:${day}`;
    let arr = [];
    try { arr = await env.SEARCH_LOGS.get(key, 'json') || []; } catch (e) { arr = []; }
    arr.push(entry);
    // cap per-day entries to keep KV objects small
    if (arr.length > 5000) arr = arr.slice(-5000);
    await env.SEARCH_LOGS.put(key, JSON.stringify(arr));
    return Response.json({ ok: true, stored: 'kv', total: arr.length });
  }
  return Response.json({ ok: true, stored: 'client-only' }, { status: 202 });
};
