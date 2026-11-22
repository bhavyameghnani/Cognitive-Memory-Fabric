import { useEffect, useState } from 'react';
import { api } from '../api';
import type { DsTablePage } from '../types';

// Each communication form maps to the CSV table(s) that were actually used. Keys match api/datasource.py TABLES.
export const GROUPS: Record<string, { key: string; label: string }[]> = {
  Meetings: [{ key: 'meetings_original', label: 'Original transcripts' }, { key: 'meetings_generated', label: 'Generated follow-ups' }],
  Calls: [{ key: 'calls', label: 'Call transcripts' }],
  Email: [{ key: 'emails', label: 'Email messages' }],
  Chat: [{ key: 'chats', label: 'Chat messages' }],
  CRM: [{ key: 'crm_meetings', label: 'CRM meetings' }, { key: 'crm_calls', label: 'CRM calls' }, { key: 'crm_email', label: 'CRM email' }, { key: 'crm_chat', label: 'CRM chat' }],
};

export function TableBrowser({ groups, clients }: { groups: string[]; clients: string[] }) {
  const [group, setGroup] = useState(groups[0]);
  const [key, setKey] = useState(GROUPS[groups[0]][0].key);
  const [client, setClient] = useState('');
  const [page, setPage] = useState<DsTablePage | null>(null);
  const [rows, setRows] = useState<string[][]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const pick = (g: string) => { setGroup(g); setKey(GROUPS[g][0].key); };
  useEffect(() => {
    let live = true;
    setPage(null); setRows([]); setErr(null); setBusy(true);
    api.table(key, client, 0).then((p) => { if (live) { setPage(p); setRows(p.rows); } }).catch((e) => { if (live) setErr(String(e.message ?? e)); }).finally(() => { if (live) setBusy(false); });
    return () => { live = false; };
  }, [key, client]);
  const more = () => { if (!page) return; setBusy(true); api.table(key, client, rows.length).then((p) => { setRows((r) => [...r, ...p.rows]); setPage(p); }).catch((e) => setErr(String(e.message ?? e))).finally(() => setBusy(false)); };

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/40">
      <div className="flex flex-wrap items-center gap-2 p-3 border-b border-slate-800">
        {groups.map((g) => (
          <button key={g} onClick={() => pick(g)} className={`rounded-lg px-3 py-1.5 text-xs font-medium cursor-pointer ${g === group ? 'bg-blue-600 text-white' : 'bg-slate-800 text-slate-300 hover:text-white'}`}>{g}</button>
        ))}
        <label className="ml-auto flex items-center gap-2 text-xs text-slate-400">Client
          <select value={client} onChange={(e) => setClient(e.target.value)} className="rounded-lg bg-slate-900 border border-slate-700 px-2 py-1 text-slate-100">
            <option value="">All clients</option>{clients.map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </label>
      </div>
      {GROUPS[group].length > 1 && (
        <div className="flex gap-2 px-3 pt-2">{GROUPS[group].map((t) => (
          <button key={t.key} onClick={() => setKey(t.key)} className={`text-xs px-2 py-1 rounded cursor-pointer ${t.key === key ? 'text-blue-300 bg-blue-500/10' : 'text-slate-400 hover:text-slate-200'}`}>{t.label}</button>))}</div>
      )}
      <div className="px-3 py-2 text-xs text-slate-400 font-mono">{page ? <>{page.file} · showing {rows.length} of {page.total} rows · {page.columns.length} columns</> : busy ? 'Loading…' : ''}</div>
      {err && <div className="px-3 pb-3 text-xs text-rose-300">Could not load: {err}</div>}
      {page && (
        <div className="max-h-[28rem] overflow-auto border-t border-slate-800">
          <table className="text-xs min-w-full">
            <thead className="sticky top-0 bg-slate-900 text-slate-400 z-10"><tr><th className="p-2 text-left font-mono text-slate-600">#</th>{page.columns.map((c) => <th key={c} className="p-2 text-left font-mono font-medium whitespace-nowrap">{c}</th>)}</tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={i} className="border-t border-slate-800/70 align-top hover:bg-slate-800/30">
                <td className="p-2 font-mono text-slate-600">{i + 1}</td>
                {r.map((v, j) => <td key={j} className="p-2 text-slate-200 max-w-[28rem]"><div className="max-h-24 overflow-auto whitespace-pre-wrap break-words">{v}</div></td>)}
              </tr>))}</tbody>
          </table>
        </div>
      )}
      {page && rows.length < page.total && (
        <div className="p-3 border-t border-slate-800"><button onClick={more} disabled={busy} className="rounded-lg bg-slate-800 px-3 py-1.5 text-xs text-slate-200 hover:text-white disabled:opacity-50 cursor-pointer">{busy ? 'Loading…' : `Load 200 more (${page.total - rows.length} left)`}</button></div>
      )}
    </div>
  );
}
