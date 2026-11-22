import {api} from '../api/archive.js';
import {heading} from '../components/common/layout.js';
import {metrics} from '../components/common/metrics.js';
import {table} from '../components/common/table.js';
import {states} from '../constants/enums.js';
import {onForm} from '../hooks/forms.js';
import {session} from '../stores/session.js';
import {h} from '../utils/html.js';

export async function reports(id) {
  const list = await api.recommendations();
  const group = id || list.length ? await api.recommendation(id || list[0].id) : null;
  const sidebar = `<aside class="sheet"><h2>采购单</h2><ul class="runs">${list.map(x => `<li><a href="#reports/${x.id}">${h(x.title || x.sleeve_code)}</a><span>${h(states[x.status])}</span></li>`).join('') || '<li>暂无采购单。</li>'}</ul></aside>`;
  let content = '<div class="empty">先在 <a href="#analyze">滚动回测</a> 按规格与起始周领单。</div>';
  if (group) {
    const output = group.result;
    content = `<h2>${h(group.title || group.sleeve_code)}</h2>
      <p class="state">${h(states[group.status])} · 规格 ${h(group.sleeve_code)} · 起始周 ${h(group.start_week)} · 现行批次 ${group.applied_revision || '—'}</p>`;
    const pending = group.pending_amendments || [];
    if (pending.length) content += `<p class="decision">以下补报尚未纳入本建议：${h(pending.join('、'))}</p>`;
    if (group.warnings?.length) content += `<ul class="notes">${group.warnings.map(w => `<li>${h(w)}</li>`).join('')}</ul>`;
    if (output) {
      const rows = output.rows || [];
      const columns = ['首个预测周','训练周数','实际领用','SBA','移动平均','SBA 误差','移动平均误差'];
      content += `${metrics(output.metrics)}<p class="decision">${h(output.summary)}</p>
        <h3>滚动起点与实际领用</h3><div class="tall">${table(columns, rows.map(row => columns.map(key => row[key])))}</div>
        <ul class="notes">${output.notes.map(note => `<li>${h(note)}</li>`).join('')}</ul>
        <button class="secondary" id="export">下载备料 CSV</button><p id="export-error" role="alert"></p>`;
      if (session()?.role === 'manager' && ['completed', 'stale'].includes(group.status)) content += `<form id="publish-form"><label>签核意见<input name="note" required minlength="3"></label><button type="submit">签核本份建议</button><p role="alert"></p></form>`;
    } else {
      content += `<p>${h(group.note || 'Worker 正在读取锁定的历史领用快照，页面将自动刷新。')}</p>`;
      if (['failed', 'stale'].includes(group.status)) content += `<button class="secondary" id="retry">重试计算（不新增采购单）</button><p id="retry-error" role="alert"></p>`;
    }
    if (group.signature) {
      const s = group.signature;
      content += `<div class="signed"><strong>已签单留档</strong> · 批次 ${s.revision} · 当时 ${s.packs} 包${s.is_current ? '' : '（已被后续重算取代）'}
        <p>签核意见 · ${h(s.note)}</p><p>当时结论 · ${h(s.result.summary)}</p></div>`;
    }
    content += `<section class="sheet" style="margin-top:1rem"><h3>批次记录</h3>${table(['批次','状态','说明'], (group.history || []).map(x => [x.revision, states[x.status], x.note || '—']), '暂无批次。')}</section>`;
  }
  return {
    html:heading('领用备料单','页面与下载使用同一份现行建议；已签单按当时的数据与包数留档回查。') + `<div class="workarea"> <section class="sheet" id="report">${content}</section>${sidebar}</div>`,
    bind(root, refresh, signal) {
      if (group && ['queued','running'].includes(group.status)) {
        const timer = setTimeout(refresh, 900);
        signal.addEventListener('abort', () => clearTimeout(timer));
      }
      root.querySelector('#export')?.addEventListener('click', async () => {
        try {
          const url = URL.createObjectURL(await api.export(group.id));
          const link = document.createElement('a');
          link.href = url;
          link.download = `sleeve-${group.id}.csv`;
          link.click();
          setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { root.querySelector('#export-error').textContent = error.message; }
      });
      root.querySelector('#retry')?.addEventListener('click', async () => {
        try { await api.retry(group.id); refresh(); }
        catch (error) { root.querySelector('#retry-error').textContent = error.message; }
      });
      onForm(root.querySelector('#publish-form'), async data => { await api.publish(group.id, data.note); refresh(); });
    }
  };
}
