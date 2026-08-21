(() => {
  const data = JSON.parse(document.querySelector('#character-data').textContent);
  const assetId = data.asset.id;
  let stats = data.stats;
  let traits = data.traits;
  let memories = data.memories;
  let cocProfile = data.cocProfile;
  let statsDirty = false;

  function selectTab(name) {
    document.querySelectorAll('.character-tabs button').forEach((button) => button.classList.toggle('active', button.dataset.tab === name));
    document.querySelectorAll('.character-tab-panel').forEach((panel) => {
      const active = panel.dataset.panel === name; panel.hidden = !active; panel.classList.toggle('active', active);
    });
  }

  function markStatsDirty(dirty = true) {
    statsDirty = dirty;
    document.querySelector('#stat-dirty').hidden = !dirty;
    document.querySelector('#save-all-stats').classList.toggle('primary', dirty);
  }

  function statPayloadFromRows() {
    return [...document.querySelectorAll('.stat-row')].map((row) => ({
      id: row.dataset.id,
      name: row.elements.name.value.trim(),
      notation: row.elements.notation.value.trim(),
      value: row.elements.value.value === '' ? null : Number(row.elements.value.value),
      locked: row.elements.locked.checked,
    }));
  }

  async function saveAllStats() {
    const items = statPayloadFromRows();
    if (!items.length) return;
    const result = await api(`/api/assets/${assetId}/stats/batch`, {method:'PUT', body:JSON.stringify({stats:items})});
    stats = result.stats; cocProfile = result.coc_profile; markStatsDirty(false); renderStats(); renderCocProfile(); toast('全部属性已保存。');
  }

  function renderStats() {
    const root = document.querySelector('#stat-list'); root.replaceChildren();
    const query = document.querySelector('#stat-filter').value.trim().toLowerCase();
    for (const stat of stats) {
      const row = document.createElement('form'); row.className = 'stat-row'; row.dataset.id = stat.id;
      row.innerHTML = '<input name="name" aria-label="属性名"><input name="notation" aria-label="骰式"><input name="value" type="number" placeholder="未定" aria-label="属性值"><label class="check-label"><input name="locked" type="checkbox">锁定</label><span class="stat-source"></span><button type="button" data-roll>投骰</button><button type="button" data-delete aria-label="删除属性">×</button>';
      row.elements.name.value = stat.name; row.elements.notation.value = stat.notation;
      row.elements.value.value = stat.value ?? ''; row.elements.locked.checked = stat.locked;
      row.querySelector('.stat-source').textContent = stat.source === 'roll' ? '骰得' : stat.source === 'manual' ? '手填' : stat.source === 'derived' ? '派生' : stat.source === 'template' ? '模板' : '未定';
      row.querySelector('[data-roll]').disabled = stat.locked;
      row.hidden = Boolean(query && !`${stat.name} ${stat.notation}`.toLowerCase().includes(query));
      row.addEventListener('submit', (event) => { event.preventDefault(); saveAllStats().catch(() => {}); });
      row.querySelectorAll('input').forEach((input) => input.addEventListener('input', () => markStatsDirty()));
      row.querySelector('[data-roll]').addEventListener('click', async () => {
        if (statsDirty) await saveAllStats();
        const result = await api(`/api/character-stats/${stat.id}/roll`, {method:'POST'});
        stats = result.stats; cocProfile = result.coc_profile; markStatsDirty(false); renderStats(); renderCocProfile();
        toast(`${result.summary[0].label}：${result.summary[0].value}`);
      });
      row.querySelector('[data-delete]').addEventListener('click', async () => {
        await api(`/api/character-stats/${stat.id}`, {method:'DELETE'});
        stats = stats.filter((item) => item.id !== stat.id); renderStats(); toast('属性已删除。');
      });
      root.append(row);
    }
  }

  async function applyTemplate(rollNow) {
    const templateId = document.querySelector('#character-template').value;
    const mode = document.querySelector('#replace-template').checked ? 'replace' : 'merge';
    const result = await api(`/api/assets/${assetId}/stats/apply-template`, {method:'POST', body:JSON.stringify({template_id:templateId, mode, roll_now:rollNow})});
    stats = result.stats; cocProfile = result.coc_profile; markStatsDirty(false); renderStats(); renderCocProfile();
    toast(rollNow ? '模板已套用，所有未定基础属性已一次投完。' : '人物模板已套用。');
  }

  function renderTraits() {
    const root = document.querySelector('#trait-list'); root.replaceChildren();
    if (!traits.length) { const empty = document.createElement('p'); empty.className='empty-copy'; empty.textContent='还没有特殊特质。'; root.append(empty); return; }
    for (const trait of traits) {
      const item = document.createElement('article'); item.className='trait-item';
      item.innerHTML='<div><strong></strong><span></span></div><p></p>';
      item.querySelector('strong').textContent=trait.label; item.querySelector('span').textContent=trait.source === 'roll' ? '骰池抽取' : '导演添加'; item.querySelector('p').textContent=trait.description || '';
      root.append(item);
    }
  }

  function renderMemories() {
    const root = document.querySelector('#character-memory-list'); root.replaceChildren();
    if (!memories.length) {
      const empty = document.createElement('div'); empty.className='empty-state memory-empty'; empty.innerHTML='<span>◌</span><strong>还没有属于这个人物的记忆</strong><p>完成一个引用该人物的故事回合后，再让小模型整理。</p>'; root.append(empty); return;
    }
    for (const memory of memories) {
      const card=document.createElement('article'); card.className='memory-card'; card.innerHTML='<header><strong>亲历事件</strong><span class="importance"></span></header><h2></h2><p class="memory-view"></p><footer><span class="emotion"></span></footer>';
      card.querySelector('.importance').textContent=`重要度 ${memory.importance}/5`; card.querySelector('h2').textContent=memory.summary; card.querySelector('.memory-view').textContent=memory.interpretation || '尚未形成明确看法'; card.querySelector('.emotion').textContent=memory.emotion ? `当时情绪：${memory.emotion}` : ''; root.append(card);
    }
  }

  function renderCocProfile() {
    const root=document.querySelector('#coc-profile-summary'); root.replaceChildren();
    const values=[['DEX',cocProfile.dexterity],['CON',cocProfile.constitution],['闪避',cocProfile.dodge],['斗殴',cocProfile.brawl],['手枪',cocProfile.firearm],['伤害加值',cocProfile.damage_bonus],['体格',cocProfile.build]];
    for (const [label,value] of values) { const item=document.createElement('div'); item.innerHTML='<span></span><strong></strong>'; item.querySelector('span').textContent=label; item.querySelector('strong').textContent=value; root.append(item); }
    const form=document.querySelector('#coc-state-form'); const state=cocProfile.state;
    form.elements.max_hp.value=state.max_hp; form.elements.current_hp.value=state.current_hp; form.elements.armor.value=state.armor;
    for (const name of ['major_wound','unconscious','dying','dead']) form.elements[name].checked=Boolean(state[name]);
  }

  document.querySelector('#profile-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const values=Object.fromEntries(new FormData(event.currentTarget)); values.kind='character'; await api(`/api/assets/${assetId}`, {method:'PUT', body:JSON.stringify(values)}); toast('人物资料已保存。');
  });
  document.querySelector('#save-all-stats').addEventListener('click', () => saveAllStats().catch(() => {}));
  document.querySelector('#apply-template').addEventListener('click', () => applyTemplate(false).catch(() => {}));
  document.querySelector('#apply-template-roll').addEventListener('click', () => applyTemplate(true).catch(() => {}));
  document.querySelector('#stat-filter').addEventListener('input', renderStats);
  document.querySelector('#roll-pending').addEventListener('click', async () => {
    if (statsDirty) await saveAllStats();
    const result=await api(`/api/assets/${assetId}/stats/roll-pending`, {method:'POST'}); stats=result.stats; cocProfile=result.coc_profile; markStatsDirty(false); renderStats(); renderCocProfile(); toast('未定属性已完成投掷。');
  });
  document.querySelector('#new-stat-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const values=Object.fromEntries(new FormData(event.currentTarget)); stats.push(await api(`/api/assets/${assetId}/stats`, {method:'POST', body:JSON.stringify(values)})); event.currentTarget.reset(); event.currentTarget.elements.notation.value='1d100'; renderStats();
  });
  document.querySelector('#manual-trait-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const values=Object.fromEntries(new FormData(event.currentTarget)); traits.push(await api(`/api/assets/${assetId}/traits`, {method:'POST', body:JSON.stringify(values)})); event.currentTarget.reset(); renderTraits();
  });
  document.querySelector('#trait-pool-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const form=event.currentTarget; const options=form.elements.options.value.split(/\n/).map((line,index) => { const [label,weight]=line.split('|').map((part)=>part.trim()); return label ? {id:`trait_${index+1}`,label,weight:Number(weight || 1)} : null; }).filter(Boolean);
    const result=await api(`/api/assets/${assetId}/traits/draw`, {method:'POST', body:JSON.stringify({label:'特殊特质抽取',options,draw_count:Number(form.elements.draw_count.value),replace:form.elements.replace.checked})}); traits.push(...result.traits); renderTraits(); toast(`抽取完成：${result.traits.map((item)=>item.label).join('、')}`);
  });
  document.querySelector('.character-tabs').addEventListener('click', (event) => { const button=event.target.closest('button[data-tab]'); if (button) selectTab(button.dataset.tab); });
  document.querySelector('#generate-character-memories').addEventListener('click', async (event) => {
    const button=event.currentTarget; button.disabled=true; button.textContent='小模型正在整理…';
    try { const result=await api(`/api/campaigns/${data.campaign.id}/memories/generate`, {method:'POST',body:'{}'}); memories=result.memories.filter((item)=>item.owner_asset_id===assetId); renderMemories(); toast('角色记忆已更新。'); }
    finally { button.disabled=false; button.textContent='从最新故事整理记忆'; }
  });
  document.querySelector('#coc-state-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const form=event.currentTarget; const payload={max_hp:Number(form.elements.max_hp.value),current_hp:Number(form.elements.current_hp.value),armor:Number(form.elements.armor.value),major_wound:form.elements.major_wound.checked,unconscious:form.elements.unconscious.checked,dying:form.elements.dying.checked,dead:form.elements.dead.checked}; cocProfile=await api(`/api/assets/${assetId}/coc-state`, {method:'PUT',body:JSON.stringify(payload)}); renderCocProfile(); toast('战斗状态已保存。');
  });
  document.querySelector('#reset-coc-state').addEventListener('click', async () => {
    const state={...cocProfile.state,current_hp:cocProfile.state.max_hp,major_wound:false,unconscious:false,dying:false,dead:false}; cocProfile=await api(`/api/assets/${assetId}/coc-state`, {method:'PUT',body:JSON.stringify(state)}); renderCocProfile(); toast('已恢复至满生命并清除伤势。');
  });
  document.addEventListener('keydown', (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase()==='s') { event.preventDefault(); if (statsDirty) saveAllStats().catch(() => {}); return; }
    if (event.key==='Escape') location.href=`/campaigns/${data.campaign.id}`;
  });
  renderStats(); renderTraits(); renderMemories(); renderCocProfile();
})();
