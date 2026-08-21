(() => {
  const data=JSON.parse(document.querySelector('#memory-data').textContent);
  const campaignId=data.campaign.id; let memories=data.memories; let owner='';
  const names=Object.fromEntries(data.characters.map((item)=>[item.id,item.name]));
  function render() {
    const root=document.querySelector('#memory-list'); root.replaceChildren();
    const visible=memories.filter((item)=>!owner || item.owner_asset_id===owner);
    if (!visible.length) { const empty=document.createElement('div'); empty.className='empty-state memory-empty'; empty.innerHTML='<span>◌</span><strong>还没有角色记忆</strong><p>完成一个引用人物的故事回合后，再从最新故事生成。</p>'; root.append(empty); return; }
    for (const memory of visible) {
      const card=document.createElement('article'); card.className='memory-card';
      card.innerHTML='<header><strong></strong><span class="importance"></span></header><h2></h2><p class="memory-view"></p><footer><span class="emotion"></span><span class="involved"></span></footer>';
      card.querySelector('strong').textContent=names[memory.owner_asset_id] || '未知人物';
      card.querySelector('.importance').textContent=`重要度 ${memory.importance}/5`;
      card.querySelector('h2').textContent=memory.summary;
      card.querySelector('.memory-view').textContent=memory.interpretation || '尚未形成明确看法';
      card.querySelector('.emotion').textContent=memory.emotion ? `情绪：${memory.emotion}` : '';
      const involved=memory.involved_asset_ids.map((id)=>names[id]).filter(Boolean); card.querySelector('.involved').textContent=involved.length ? `涉及：${involved.join('、')}` : '';
      root.append(card);
    }
  }
  document.querySelector('#memory-filter').addEventListener('click',(event)=>{ const button=event.target.closest('button'); if(!button)return; owner=button.dataset.owner; document.querySelectorAll('#memory-filter button').forEach((item)=>item.classList.toggle('active',item===button)); render(); });
  document.querySelector('#generate-memories').addEventListener('click',async(event)=>{ const button=event.currentTarget; button.disabled=true; button.textContent='小模型正在整理…'; try { const result=await api(`/api/campaigns/${campaignId}/memories/generate`,{method:'POST',body:'{}'}); memories=result.memories; render(); toast('最新故事的角色记忆已更新。'); } finally { button.disabled=false; button.textContent='从最新故事生成记忆'; } });
  render();
})();
