import {api} from '../api/archive.js';
import {heading} from '../components/common/layout.js';
import {states} from '../constants/enums.js';
import {onForm} from '../hooks/forms.js';
import {canWrite} from '../stores/session.js';
import {h} from '../utils/html.js';

export async function analyze() {
  const [skus, groups] = await Promise.all([api.sleeves(), api.recommendations()]);
  return {
    html: heading('滚动回测', '同一规格与备料起始周只保留一份现行建议；以后补报会触发重算。') +
      `<div class="workarea reverse"><section class="sheet"><h2>领单 / 重算</h2>${canWrite() ? `
      <form id="analysis-form"><label>单据标题<input name="title" required value="修复组下批封套备料"></label>
      <label>封套规格<select name="sleeve_code">${skus.map(x => `<option value="${h(x.code)}" ${x.code==='SL-A4'?'selected':''}>${h(x.title)}（${x.lead_weeks} 周）</option>`).join('')}</select></label>
      <label>备料起始周<input name="start_week" type="date"><small>留空取该规格最近领用周的下一个周一</small></label>
      <label>SBA 平滑系数<input name="alpha" type="number" min=".05" max=".5" step=".01" value=".2" required></label>
      <label>移动平均窗口 / 周<input name="baseline_weeks" type="number" min="4" max="16" value="8" required></label>
      <button type="submit">按起始周领单并回测</button><p role="alert"></p></form>
      <p class="notes">同一规格与起始周再次提交只会重算并替换现行建议，不会新增采购单。</p>` : '<p>观察员可查看测算记录。</p>'}</section>
      <section class="sheet"><h2>采购单（规格 + 起始周）</h2><ul class="runs">${groups.map(x => `<li><a href="#reports/${x.id}">${h(x.title || x.sleeve_code)}</a><span>${h(states[x.status])}</span></li>`).join('') || '<li>首次领单后会出现记录。</li>'}</ul></section></div>`,
    bind(root) { onForm(root.querySelector('#analysis-form'), async data => {
      const payload = {...data, alpha:Number(data.alpha), baseline_weeks:Number(data.baseline_weeks)};
      if (!payload.start_week) delete payload.start_week;
      const run = await api.recommendations(payload);
      location.hash = `reports/${run.id}`;
    }); }
  };
}
