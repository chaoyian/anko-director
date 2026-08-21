(() => {
  const bootstrap = JSON.parse(document.querySelector('#campaign-data').textContent);
  const campaignId = bootstrap.campaign.id;
  let assets = bootstrap.assets;
  let statusCards = bootstrap.statusCards || [];
  let relations = bootstrap.relations || [];
  let outcomes = bootstrap.outcomes || [];
  let timeline = bootstrap.timeline || {branches:[], commits:[], active_branch_id:null};
  let messages = bootstrap.messages;
  let cocProfiles = bootstrap.cocProfiles || [];
  let panX = Number(bootstrap.canvasView.pan_x || 0);
  let panY = Number(bootstrap.canvasView.pan_y || 0);
  let zoom = Number(bootstrap.canvasView.zoom || 1);
  let activeTurn = null;
  let stepwiseReadyForStory = false;
  let explicitProposal = null;
  let diceOperations = [];
  let canvasDrag = null;
  let assetDrag = null;
  let statusDragId = null;
  let statusPointerDrag = null;
  let suppressStatusClickUntil = 0;
  let dragged = false;
  let historicalCommitId = null;
  let historicalReferences = [];
  let readOnlyWorld = false;
  const visibleLayers = {assets:true, relations:true, statuses:true, outcomes:true};
  const selected = new Set();
  const expandedStoryIds = new Set();
  let characterStats = bootstrap.characterStats || {};

  const viewport = document.querySelector('#canvas-viewport');
  const world = document.querySelector('#canvas-world');
  const chips = document.querySelector('#asset-chips');
  const modal = document.querySelector('#asset-modal');
  const assetForm = document.querySelector('#asset-form');
  const statusModal = document.querySelector('#status-modal');
  const statusForm = document.querySelector('#status-form');
  const relationModal = document.querySelector('#relation-modal');
  const relationForm = document.querySelector('#relation-form');
  const directorInput = document.querySelector('#director-input');
  const turnCard = document.querySelector('#turn-card');
  const turnStatus = document.querySelector('#turn-status');
  const enterKey = document.querySelector('#enter-key');
  const storyOutput = document.querySelector('#story-output');
  const evidence = document.querySelector('#roll-evidence');
  const directorLeft = document.querySelector('.director-left');
  const storyResizer = document.querySelector('#story-resizer');
  const storyScroll = document.querySelector('#story-scroll');
  const dicePanel = document.querySelector('#dice-panel');
  const cocPanel = document.querySelector('#coc-panel');
  const cocForm = document.querySelector('#coc-combat-form');
  const diceTray = document.querySelector('#dice-tray');
  const slashMenu = document.querySelector('#slash-menu');
  const diceCharacter = document.querySelector('#dice-character');
  const diceStat = document.querySelector('#dice-stat');
  const keepAssetReferences = document.querySelector('#keep-asset-references');

  const kindLabels = {character:'角色', scene:'场景', prop:'道具', rule:'规则'};
  const defaultColors = {character:'#d89469', scene:'#75a48d', prop:'#cca052', rule:'#9485bd'};

  function setTransform() {
    world.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`;
    document.querySelector('#zoom-reset').textContent = `${Math.round(zoom * 100)}%`;
  }

  let viewTimer;
  function persistView() {
    if (readOnlyWorld) return;
    clearTimeout(viewTimer);
    viewTimer = setTimeout(() => api(`/api/campaigns/${campaignId}/canvas-view`, {
      method:'PUT', body:JSON.stringify({pan_x:panX, pan_y:panY, zoom}),
    }).catch(() => {}), 350);
  }

  function setZoom(next, clientX = null, clientY = null) {
    const old = zoom;
    zoom = Math.max(.25, Math.min(3, next));
    if (clientX !== null && clientY !== null) {
      const box = viewport.getBoundingClientRect();
      const px = clientX - box.left - box.width / 2;
      const py = clientY - box.top - box.height / 2;
      panX = px - ((px - panX) / old) * zoom;
      panY = py - ((py - panY) / old) * zoom;
    }
    setTransform(); persistView();
  }

  function findOpenAssetPosition(originX, originY, occupied = assets) {
    const spacingX = 196;
    const spacingY = 126;
    const isOpen = (x, y) => !occupied.some((asset) => Math.abs(asset.x - x) < 176 && Math.abs(asset.y - y) < 106);
    if (isOpen(originX, originY)) return {x:originX, y:originY};
    for (let ring = 1; ring <= 20; ring += 1) {
      const cells = [];
      for (let row = -ring; row <= ring; row += 1) {
        for (let column = -ring; column <= ring; column += 1) {
          if (Math.max(Math.abs(row), Math.abs(column)) === ring) cells.push({row, column});
        }
      }
      cells.sort((a, b) => {
        const score = (cell) => (cell.row < 0 ? 100 : cell.row * 8) + Math.abs(cell.column) * 2 + (cell.column < 0 ? 1 : 0);
        return score(a) - score(b);
      });
      for (const cell of cells) {
        const x = originX + cell.column * spacingX;
        const y = originY + cell.row * spacingY;
        if (isOpen(x, y)) return {x, y};
      }
    }
    return {x:originX + occupied.length * 24, y:originY + occupied.length * 24};
  }

  function repairOverlappingAssetPositions() {
    const occupied = [];
    const repairs = [];
    for (const asset of assets) {
      const position = findOpenAssetPosition(asset.x, asset.y, occupied);
      occupied.push({...asset, ...position});
      if (position.x === asset.x && position.y === asset.y) continue;
      asset.x = position.x;
      asset.y = position.y;
      repairs.push(api(`/api/assets/${asset.id}/position`, {
        method:'PATCH', body:JSON.stringify(position),
      }));
    }
    return Promise.all(repairs);
  }

  function svgElement(name, attributes = {}) {
    const element = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
    return element;
  }

  function addHistoricalReference(entityType, entityId, label) {
    if (!historicalCommitId) return;
    const exists = historicalReferences.some((item) => item.commit_id === historicalCommitId && item.entity_type === entityType && item.entity_id === entityId);
    if (!exists) historicalReferences.push({commit_id:historicalCommitId, entity_type:entityType, entity_id:entityId, label});
    renderChips(); toast(`已引用历史内容：${label}`);
  }

  function visibleOutcomes() {
    if (!visibleLayers.outcomes || !outcomes.length) return [];
    const range = document.querySelector('#outcome-range').value;
    if (range === 'all') return outcomes;
    const maximum = Math.max(...outcomes.map((item) => Number(item.turn_number || 0)));
    const minimum = maximum - Number(range) + 1;
    return outcomes.filter((item) => Number(item.turn_number || 0) >= minimum);
  }

  function renderAssets() {
    world.replaceChildren();
    const graph = svgElement('svg', {class:'graph-layer', 'aria-hidden':'true'});
    const defs = svgElement('defs');
    const marker = svgElement('marker', {id:'world-arrow', viewBox:'0 0 10 10', refX:'9', refY:'5', markerWidth:'6', markerHeight:'6', orient:'auto-start-reverse'});
    marker.append(svgElement('path', {d:'M 0 0 L 10 5 L 0 10 z'})); defs.append(marker); graph.append(defs); world.append(graph);
    const assetMap = new Map(assets.map((asset) => [asset.id, asset]));
    const shownOutcomes = visibleOutcomes();
    const outcomeMap = new Map(shownOutcomes.map((outcome) => [outcome.id, outcome]));

    if (visibleLayers.relations) {
      for (const relation of relations.filter((item) => !item.ended_turn_id)) {
        const source = assetMap.get(relation.source_asset_id); const target = assetMap.get(relation.target_asset_id);
        if (!source || !target) continue;
        const x1=source.x+170, y1=source.y+48, x2=target.x, y2=target.y+48;
        const relationPath=svgElement('path', {class:'relation-edge', 'data-relation-id':relation.id, d:`M ${x1} ${y1} C ${(x1+x2)/2} ${y1}, ${(x1+x2)/2} ${y2}, ${x2} ${y2}`, 'marker-end':'url(#world-arrow)'});
        relationPath.addEventListener('dblclick',(event)=>{event.stopPropagation();if(readOnlyWorld)addHistoricalReference('relation',relation.id,relation.label);else openRelation(relation);});
        graph.append(relationPath);
        const label=document.createElement('button'); label.type='button'; label.className='relation-label'; label.dataset.relationId=relation.id; label.textContent=relation.label; label.style.left=`${(x1+x2)/2-34}px`; label.style.top=`${(y1+y2)/2-13}px`;
        label.addEventListener('click',(event)=>{event.stopPropagation(); if(readOnlyWorld)addHistoricalReference('relation',relation.id,relation.label);else openRelation(relation);}); world.append(label);
      }
    }
    for (const outcome of shownOutcomes) {
      for (const assetId of outcome.participant_asset_ids || []) {
        const asset=assetMap.get(assetId); if(!asset) continue;
        graph.append(svgElement('path',{class:'outcome-edge','data-outcome-id':outcome.id,'data-asset-id':asset.id,d:`M ${asset.x+85} ${asset.y+96} C ${asset.x+85} ${outcome.y-30}, ${outcome.x+100} ${asset.y+126}, ${outcome.x+100} ${outcome.y}`,'marker-end':'url(#world-arrow)'}));
      }
      for (const previousId of outcome.previous_outcome_ids || []) {
        const previous=outcomeMap.get(previousId); if(!previous) continue;
        graph.append(svgElement('path',{class:'outcome-edge causal',d:`M ${previous.x+200} ${previous.y+45} L ${outcome.x} ${outcome.y+45}`,'marker-end':'url(#world-arrow)'}));
      }
    }

    for (const asset of assets) {
      const card = document.createElement('div'); card.className = `asset-node ${asset.kind}`; card.tabIndex=0; card.setAttribute('role','button');
      card.dataset.id = asset.id; card.hidden=!visibleLayers.assets;
      card.style.setProperty('--asset-color', asset.color || defaultColors[asset.kind]);
      card.style.left = `${asset.x}px`; card.style.top = `${asset.y}px`;
      card.classList.toggle('selected', selected.has(asset.id));
      const type = document.createElement('span'); type.className = 'asset-type'; type.textContent = kindLabels[asset.kind] || asset.kind;
      const name = document.createElement('strong'); name.textContent = asset.name;
      const summary = document.createElement('small'); summary.textContent = asset.summary || '双击补充资产内容';
      card.append(type, name, summary);
      if (asset.kind === 'character' && !readOnlyWorld) {
        const add=document.createElement('button'); add.type='button'; add.className='add-status-card'; add.textContent='＋状态'; add.addEventListener('pointerdown',(event)=>event.stopPropagation()); add.addEventListener('click',(event)=>{event.stopPropagation();openStatus(null,asset.id);}); card.append(add);
        card.addEventListener('dragover',(event)=>{if(statusDragId){event.preventDefault();card.classList.add('status-target');}});
        card.addEventListener('dragleave',()=>card.classList.remove('status-target'));
        card.addEventListener('drop',async(event)=>{event.preventDefault();card.classList.remove('status-target');const status=statusCards.find((item)=>item.id===statusDragId);if(!status)return;await api(`/api/status-cards/${status.id}`,{method:'PUT',body:JSON.stringify({target_asset_id:asset.id,duration_turns:status.duration_turns,description:status.description})});await refreshWorld();});
      }
      if (!readOnlyWorld) card.addEventListener('pointerdown', startAssetDrag);
      card.addEventListener('click', () => {
        if (dragged) { dragged = false; return; }
        if(readOnlyWorld){addHistoricalReference('asset',asset.id,asset.name);return;}
        if (selected.has(asset.id)) selected.delete(asset.id); else selected.add(asset.id);
        renderAssets(); renderChips();
      });
      card.addEventListener('dblclick', (event) => {
        event.preventDefault(); if(readOnlyWorld)return;
        if (asset.kind === 'character') location.href = `/campaigns/${campaignId}/characters/${asset.id}`;
        else openAsset(asset);
      });
      world.append(card);
    }

    if (visibleLayers.statuses) {
      for (const asset of assets.filter((item)=>item.kind==='character')) {
        const cards=statusCards.filter((item)=>item.target_asset_id===asset.id&&!item.ended_turn_id); if(!cards.length)continue;
        const stack=document.createElement('div'); stack.className='status-card-stack'; stack.style.left=`${asset.x}px`; stack.style.top=`${asset.y+104}px`;
        for(const status of cards){
          const card=document.createElement('article');card.className='tabletop-status-card';card.dataset.id=status.id;
          const duration=document.createElement('span');duration.textContent=status.duration_turns===null?'永久':`剩余 ${status.remaining_turns} 回`;
          const description=document.createElement('p');description.textContent=status.description;
          const remove=document.createElement('button');remove.type='button';remove.textContent='×';remove.setAttribute('aria-label','拿掉状态卡');remove.hidden=readOnlyWorld;
          remove.addEventListener('pointerdown',(event)=>event.stopPropagation());
          remove.addEventListener('click',async(event)=>{event.stopPropagation();await api(`/api/status-cards/${status.id}`,{method:'DELETE'});await refreshWorld();});
          card.append(duration,description,remove);
          if(!readOnlyWorld)card.addEventListener('pointerdown',(event)=>{if(event.button!==0||event.target.closest('button'))return;event.stopPropagation();statusDragId=status.id;statusPointerDrag={card,status,startX:event.clientX,startY:event.clientY,moved:false};card.setPointerCapture(event.pointerId);card.classList.add('dragging');});
          card.addEventListener('click',()=>{if(Date.now()<suppressStatusClickUntil)return;if(readOnlyWorld)addHistoricalReference('status_card',status.id,status.description);else openStatus(status);});
          stack.append(card);
        }
        world.append(stack);
      }
    }
    for (const outcome of shownOutcomes) {
      const node=document.createElement('button');node.type='button';node.className='outcome-node';node.style.left=`${outcome.x}px`;node.style.top=`${outcome.y}px`;node.innerHTML='<span></span><strong></strong><small></small>';node.querySelector('span').textContent=`第 ${outcome.turn_number} 回`;node.querySelector('strong').textContent=outcome.title;node.querySelector('small').textContent=outcome.summary;node.addEventListener('click',()=>{if(readOnlyWorld)addHistoricalReference('outcome',outcome.id,outcome.title);});world.append(node);
    }
  }

  function updateGraphGeometry() {
    const assetMap=new Map(assets.map((asset)=>[asset.id,asset]));const outcomeMap=new Map(outcomes.map((outcome)=>[outcome.id,outcome]));
    for(const path of world.querySelectorAll('[data-relation-id]')){const relation=relations.find((item)=>item.id===path.dataset.relationId);const source=assetMap.get(relation?.source_asset_id);const target=assetMap.get(relation?.target_asset_id);if(!source||!target)continue;const x1=source.x+170,y1=source.y+48,x2=target.x,y2=target.y+48;path.setAttribute('d',`M ${x1} ${y1} C ${(x1+x2)/2} ${y1}, ${(x1+x2)/2} ${y2}, ${x2} ${y2}`);const label=world.querySelector(`.relation-label[data-relation-id="${relation.id}"]`);if(label){label.style.left=`${(x1+x2)/2-34}px`;label.style.top=`${(y1+y2)/2-13}px`;}}
    for(const path of world.querySelectorAll('[data-outcome-id][data-asset-id]')){const asset=assetMap.get(path.dataset.assetId);const outcome=outcomeMap.get(path.dataset.outcomeId);if(asset&&outcome)path.setAttribute('d',`M ${asset.x+85} ${asset.y+96} C ${asset.x+85} ${outcome.y-30}, ${outcome.x+100} ${asset.y+126}, ${outcome.x+100} ${outcome.y}`);}
    for(const stack of world.querySelectorAll('.status-card-stack')){const asset=assetMap.get(statusCards.find((status)=>status.id===stack.querySelector('.tabletop-status-card')?.dataset.id)?.target_asset_id);if(asset){stack.style.left=`${asset.x}px`;stack.style.top=`${asset.y+104}px`;}}
  }

  function renderChips() {
    chips.replaceChildren();
    for (const id of selected) {
      const asset = assets.find((item) => item.id === id);
      if (!asset) continue;
      const button = document.createElement('button'); button.type = 'button'; button.className = 'asset-chip';
      button.textContent = `${kindLabels[asset.kind]} · ${asset.name} ×`;
      button.addEventListener('click', () => { selected.delete(id); renderAssets(); renderChips(); });
      chips.append(button);
    }
    for (const reference of historicalReferences) {
      const button=document.createElement('button');button.type='button';button.className='asset-chip historical';button.textContent=`历史 · ${reference.label} ×`;button.addEventListener('click',()=>{historicalReferences=historicalReferences.filter((item)=>item!==reference);renderChips();});chips.append(button);
    }
    populateDiceCharacters();
    persistSelectedAssets();
  }

  function parseNotation(notation) {
    const match = String(notation || '').match(/^\s*(\d*)d(\d+)([+-]\d+)?\s*$/i);
    if (!match) return null;
    return {count:Number(match[1] || 1), sides:Number(match[2]), modifier:Number(match[3] || 0)};
  }

  function populateDiceCharacters() {
    const previous = diceCharacter.value; diceCharacter.replaceChildren();
    const empty = document.createElement('option'); empty.value=''; empty.textContent='命运 / 无人物'; diceCharacter.append(empty);
    const characters = assets.filter((asset) => asset.kind === 'character');
    for (const asset of characters) {
      const option=document.createElement('option'); option.value=asset.id; option.textContent=`${selected.has(asset.id) ? '● ' : ''}${asset.name}`; diceCharacter.append(option);
    }
    if (characters.some((asset)=>asset.id===previous)) diceCharacter.value=previous;
    else if ([...selected].filter((id)=>characters.some((asset)=>asset.id===id)).length === 1) diceCharacter.value=[...selected].find((id)=>characters.some((asset)=>asset.id===id));
    populateDiceStats();
  }

  function populateDiceStats() {
    const assetId=diceCharacter.value; diceStat.replaceChildren();
    const empty=document.createElement('option'); empty.value=''; empty.textContent='自定义判定'; diceStat.append(empty);
    for (const stat of characterStats[assetId] || []) {
      const option=document.createElement('option'); option.value=stat.id; option.textContent=stat.value === null ? `${stat.name}（未定）` : `${stat.name} · ${stat.value}`; option.dataset.stat=JSON.stringify(stat); diceStat.append(option);
    }
  }

  function openDicePanel(command='dice') {
    dicePanel.hidden=false; cocPanel.hidden=true; slashMenu.hidden=true;
    if (command === 'continuous') { document.querySelector('#dice-repeat').value=3; document.querySelector('#dice-sequence-mode').value='stepwise'; }
    populateDiceCharacters();
    if (command === 'attribute' && diceCharacter.value) diceStat.focus(); else document.querySelector('#dice-label').focus();
  }

  const cocWeapons = {
    unarmed:{name:'徒手',damage:'1d3',use_damage_bonus:true,impaling:false,kind:'melee'},
    knife:{name:'小刀',damage:'1d4',use_damage_bonus:true,impaling:true,kind:'melee'},
    club:{name:'短棍',damage:'1d6',use_damage_bonus:true,impaling:false,kind:'melee'},
    bat:{name:'球棒',damage:'1d8',use_damage_bonus:true,impaling:false,kind:'melee'},
    handgun:{name:'手枪',damage:'1d10',use_damage_bonus:false,impaling:true,kind:'firearm'},
    shotgun:{name:'霰弹枪',damage:'4d6',use_damage_bonus:false,impaling:false,kind:'firearm'},
  };

  function cocProfile(assetId) { return cocProfiles.find((item)=>item.asset_id===assetId); }

  function renderCocRoster() {
    const root=document.querySelector('#coc-roster'); root.replaceChildren();
    if (!cocProfiles.length) { const p=document.createElement('p'); p.textContent='先建立至少两个人物资产，再进行战斗。'; root.append(p); return; }
    for (const profile of cocProfiles) {
      const card=document.createElement('button'); card.type='button'; card.className='coc-combatant'; card.dataset.assetId=profile.asset_id;
      const state=profile.state; const flags=[state.dead?'死亡':'',state.dying?'濒死':'',state.unconscious?'昏迷':'',state.major_wound?'重伤':'',state.skip_next_action?'失去下次行动':''].filter(Boolean).join(' · ');
      card.innerHTML='<strong></strong><span></span><small></small>'; card.querySelector('strong').textContent=profile.name; card.querySelector('span').textContent=`HP ${state.current_hp}/${state.max_hp} · DEX ${profile.dexterity}`; card.querySelector('small').textContent=flags || `闪避 ${profile.dodge} · 斗殴 ${profile.brawl}`;
      card.addEventListener('click', () => { cocForm.elements.attacker_asset_id.value=profile.asset_id; syncCocSkills(); }); root.append(card);
    }
  }

  function populateCocCharacters() {
    const attacker=cocForm.elements.attacker_asset_id; const defender=cocForm.elements.defender_asset_id;
    const oldAttacker=attacker.value; const oldDefender=defender.value; attacker.replaceChildren(); defender.replaceChildren();
    for (const profile of cocProfiles) {
      for (const select of [attacker,defender]) { const option=document.createElement('option'); option.value=profile.asset_id; option.textContent=profile.name; select.append(option); }
    }
    attacker.value=cocProfiles.some((item)=>item.asset_id===oldAttacker) ? oldAttacker : cocProfiles[0]?.asset_id || '';
    defender.value=cocProfiles.some((item)=>item.asset_id===oldDefender && item.asset_id!==attacker.value) ? oldDefender : cocProfiles.find((item)=>item.asset_id!==attacker.value)?.asset_id || '';
    syncCocSkills(); renderCocRoster();
  }

  function configureCocDefense() {
    const kind=cocForm.elements.attack_kind.value; const select=cocForm.elements.defense; const previous=select.value; select.replaceChildren();
    const choices=kind==='firearm' ? [['dive_for_cover','扑向掩体'],['none','不应对']] : kind==='first_aid' ? [['none','无需防御']] : [['dodge','闪避'],['fight_back','反击'],['none','不应对']];
    for (const [value,label] of choices) { const option=document.createElement('option'); option.value=value; option.textContent=label; select.append(option); }
    if (choices.some(([value])=>value===previous)) select.value=previous;
    cocForm.elements.shots.disabled=kind!=='firearm'; if (kind!=='firearm') cocForm.elements.shots.value=1;
    cocForm.querySelector('.maneuver-effect').hidden=kind!=='maneuver'; syncCocSkills();
  }

  function syncCocSkills() {
    const attacker=cocProfile(cocForm.elements.attacker_asset_id.value); const defender=cocProfile(cocForm.elements.defender_asset_id.value); const kind=cocForm.elements.attack_kind.value; const defense=cocForm.elements.defense.value;
    if (attacker) cocForm.elements.attack_skill.value=kind==='first_aid' ? attacker.first_aid : kind==='firearm' ? attacker.firearm : attacker.brawl;
    if (defender) cocForm.elements.defense_skill.value=defense==='fight_back' ? defender.brawl : defender.dodge;
  }

  function applyCocWeaponPreset() {
    const preset=cocWeapons[cocForm.elements.weapon_preset.value]; if (!preset) return;
    cocForm.elements.weapon_name.value=preset.name; cocForm.elements.damage.value=preset.damage; cocForm.elements.use_damage_bonus.checked=preset.use_damage_bonus; cocForm.elements.impaling.checked=preset.impaling; cocForm.elements.attack_kind.value=preset.kind; configureCocDefense();
  }

  async function openCocPanel() {
    const data=await api(`/api/campaigns/${campaignId}/coc/combatants`); cocProfiles=data.combatants; dicePanel.hidden=true; cocPanel.hidden=false; slashMenu.hidden=true; populateCocCharacters();
  }

  function showCocResult(result) {
    const box=document.querySelector('#coc-result'); box.hidden=false; box.textContent=result.announcement;
    const storyCard=document.createElement('div'); storyCard.className='coc-story-result'; storyCard.textContent=result.announcement;
    storyOutput.prepend(storyCard); storyScroll.scrollTop=0;
  }

  function operationFromFields() {
    const comparison=document.querySelector('#dice-comparison').value;
    const targetText=document.querySelector('#dice-target').value;
    const character=assets.find((asset)=>asset.id===diceCharacter.value);
    if (comparison && targetText === '') throw new Error('设置成功条件后必须填写目标值');
    return {
      kind:'dice', id:`explicit_${Date.now()}_${diceOperations.length+1}`,
      label:document.querySelector('#dice-label').value.trim() || '命运判定',
      character_name:character?.name || null, target_asset_id:character?.id || null,
      dice:{count:Number(document.querySelector('#dice-count').value), sides:Number(document.querySelector('#dice-sides').value), modifier:Number(document.querySelector('#dice-modifier').value || 0)},
      secret:document.querySelector('#dice-secret').checked,
      target:comparison ? Number(targetText) : null, comparison:comparison || null,
    };
  }

  function renderDiceTray() {
    diceTray.replaceChildren();
    if (!diceOperations.length) { const p=document.createElement('p'); p.textContent='骰组还是空的。点击上方骰子或“加入骰组”。'; diceTray.append(p); return; }
    diceOperations.forEach((operation,index)=>{ const row=document.createElement('div'); row.className='dice-tray-item'; const target=operation.target===null?'':` · ${operation.comparison==='lte'?'≤':'≥'} ${operation.target}`; row.innerHTML='<strong></strong><span></span><button type="button" aria-label="删除">×</button>'; row.querySelector('strong').textContent=`${operation.character_name ? `${operation.character_name} · ` : ''}${operation.label}`; row.querySelector('span').textContent=`${operation.dice.count}d${operation.dice.sides}${operation.dice.modifier ? (operation.dice.modifier>0?'+':'')+operation.dice.modifier : ''}${target}`; row.querySelector('button').addEventListener('click',()=>{diceOperations.splice(index,1);renderDiceTray();}); diceTray.append(row); });
  }

  function startAssetDrag(event) {
    if (event.button !== 0) return;
    const card = event.currentTarget;
    const asset = assets.find((item) => item.id === card.dataset.id);
    assetDrag = {card, asset, startX:event.clientX, startY:event.clientY, x:asset.x, y:asset.y};
    dragged = false; card.setPointerCapture(event.pointerId);
  }

  viewport.addEventListener('pointermove', (event) => {
    if (statusPointerDrag) {
      const dx=(event.clientX-statusPointerDrag.startX)/zoom;const dy=(event.clientY-statusPointerDrag.startY)/zoom;
      if(Math.abs(dx)+Math.abs(dy)>4)statusPointerDrag.moved=true;
      statusPointerDrag.card.style.transform=`translate(${dx}px, ${dy}px)`;
      return;
    }
    if (assetDrag) {
      const dx = (event.clientX - assetDrag.startX) / zoom;
      const dy = (event.clientY - assetDrag.startY) / zoom;
      if (Math.abs(dx) + Math.abs(dy) > 4) dragged = true;
      assetDrag.asset.x = assetDrag.x + dx; assetDrag.asset.y = assetDrag.y + dy;
      assetDrag.card.style.left = `${assetDrag.asset.x}px`; assetDrag.card.style.top = `${assetDrag.asset.y}px`;
      updateGraphGeometry();
      return;
    }
    if (canvasDrag) {
      panX = canvasDrag.panX + event.clientX - canvasDrag.startX;
      panY = canvasDrag.panY + event.clientY - canvasDrag.startY;
      setTransform();
    }
  });

  viewport.addEventListener('pointerdown', (event) => {
    if (event.target.closest('.asset-node,.status-card-stack,.outcome-node,.relation-label,.canvas-toolbar,.chapter-timeline') || event.button !== 0) return;
    canvasDrag = {startX:event.clientX, startY:event.clientY, panX, panY};
    viewport.classList.add('dragging'); viewport.setPointerCapture(event.pointerId);
  });

  viewport.addEventListener('pointerup', async (event) => {
    if(statusPointerDrag){
      const finished=statusPointerDrag;statusPointerDrag=null;statusDragId=null;finished.card.classList.remove('dragging');finished.card.style.transform='';
      if(finished.moved)suppressStatusClickUntil=Date.now()+300;
      const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('.asset-node.character');
      if(finished.moved&&target&&target.dataset.id!==finished.status.target_asset_id){
        await api(`/api/status-cards/${finished.status.id}`,{method:'PUT',body:JSON.stringify({target_asset_id:target.dataset.id,duration_turns:finished.status.duration_turns,description:finished.status.description})});
        await refreshWorld();
      }
    }
    if (assetDrag) {
      const finished = assetDrag; assetDrag = null;
      if (dragged) {
        await api(`/api/assets/${finished.asset.id}/position`, {method:'PATCH', body:JSON.stringify({x:finished.asset.x, y:finished.asset.y})});
      }
    }
    if (canvasDrag) { canvasDrag = null; viewport.classList.remove('dragging'); persistView(); }
  });
  viewport.addEventListener('pointercancel', () => { if(statusPointerDrag){statusPointerDrag.card.classList.remove('dragging');statusPointerDrag.card.style.transform='';}statusPointerDrag=null;statusDragId=null;assetDrag = null;canvasDrag = null;viewport.classList.remove('dragging'); });
  viewport.addEventListener('wheel', (event) => { event.preventDefault(); setZoom(zoom * (event.deltaY > 0 ? .9 : 1.1), event.clientX, event.clientY); }, {passive:false});
  document.querySelector('#zoom-out').addEventListener('click', () => setZoom(zoom - .1));
  document.querySelector('#zoom-in').addEventListener('click', () => setZoom(zoom + .1));
  document.querySelector('#zoom-reset').addEventListener('click', () => { zoom = 1; panX = 0; panY = 0; setTransform(); persistView(); });
  document.querySelector('#fit-assets').addEventListener('click', () => {
    if (!assets.length) return;
    const extentItems=assets.map((asset)=>{const statusCount=statusCards.filter((card)=>card.target_asset_id===asset.id&&!card.ended_turn_id).length;return{x1:asset.x,y1:asset.y,x2:asset.x+170,y2:asset.y+100+statusCount*42};});
    for(const outcome of visibleOutcomes())extentItems.push({x1:outcome.x,y1:outcome.y,x2:outcome.x+200,y2:outcome.y+90});
    const minX = Math.min(...extentItems.map((item) => item.x1)); const maxX = Math.max(...extentItems.map((item) => item.x2));
    const minY = Math.min(...extentItems.map((item) => item.y1)); const maxY = Math.max(...extentItems.map((item) => item.y2));
    const box = viewport.getBoundingClientRect();
    const topPadding = document.querySelector('.canvas-toolbar').getBoundingClientRect().height + 34;
    const bottomPadding = 42;
    const usableHeight = Math.max(80, box.height - topPadding - bottomPadding);
    zoom = Math.max(.25, Math.min(1.2, Math.min((box.width - 80) / (maxX - minX), usableHeight / (maxY - minY))));
    panX = -((minX + maxX) / 2) * zoom;
    panY = (topPadding - bottomPadding) / 2 - ((minY + maxY) / 2) * zoom;
    setTransform(); persistView();
  });

  function openAsset(asset = null) {
    assetForm.reset();
    assetForm.elements.id.value = asset?.id || '';
    assetForm.elements.name.value = asset?.name || '';
    assetForm.elements.kind.value = asset?.kind || 'character';
    assetForm.elements.color.value = asset?.color || defaultColors[asset?.kind || 'character'];
    assetForm.elements.summary.value = asset?.summary || '';
    assetForm.elements.body.value = asset?.body || '';
    document.querySelector('#asset-modal-title').textContent = asset ? `编辑 · ${asset.name}` : '新资产';
    document.querySelector('#archive-asset').hidden = !asset;
    const facts = document.querySelector('#derived-facts'); const list = document.querySelector('#derived-facts-list'); list.replaceChildren();
    const entries = Object.entries(asset?.derived_facts || {}); facts.hidden = !entries.length;
    for (const [key, value] of entries) { const dt = document.createElement('dt'); dt.textContent = key; const dd = document.createElement('dd'); dd.textContent = Array.isArray(value) ? value.join('、') : value; list.append(dt, dd); }
    modal.hidden = false; assetForm.elements.name.focus();
  }
  function closeAsset() { modal.hidden = true; }
  document.querySelector('#new-asset').addEventListener('click', () => openAsset());
  document.querySelector('#close-asset').addEventListener('click', closeAsset);
  document.querySelector('#cancel-asset').addEventListener('click', closeAsset);
  modal.addEventListener('click', (event) => { if (event.target === modal) closeAsset(); });
  assetForm.elements.kind.addEventListener('change', () => { if (!assetForm.elements.id.value) assetForm.elements.color.value = defaultColors[assetForm.elements.kind.value]; });
  assetForm.addEventListener('submit', async (event) => {
    event.preventDefault(); const values = Object.fromEntries(new FormData(assetForm)); const id = values.id; delete values.id;
    if (id) {
      const updated = await api(`/api/assets/${id}`, {method:'PUT', body:JSON.stringify(values)});
      assets = assets.map((asset) => asset.id === id ? updated : asset);
    } else {
      const position = findOpenAssetPosition(-panX / zoom - 76, -panY / zoom - 44);
      values.x = position.x; values.y = position.y;
      assets.push(await api(`/api/campaigns/${campaignId}/assets`, {method:'POST', body:JSON.stringify(values)}));
    }
    closeAsset(); renderAssets(); toast('资产已保存。');
  });
  document.querySelector('#archive-asset').addEventListener('click', async () => {
    const id = assetForm.elements.id.value; if (!id) return;
    await api(`/api/assets/${id}/archive`, {method:'POST'}); selected.delete(id); assets = assets.filter((asset) => asset.id !== id); closeAsset(); renderAssets(); renderChips(); toast('资产已归档。');
  });

  function populateAssetSelect(select, charactersOnly=false) {
    const previous=select.value; select.replaceChildren();
    for(const asset of assets.filter((item)=>!charactersOnly||item.kind==='character')){const option=document.createElement('option');option.value=asset.id;option.textContent=asset.name;select.append(option);}
    if([...select.options].some((option)=>option.value===previous))select.value=previous;
  }
  function openStatus(status=null,targetId=null){statusForm.reset();populateAssetSelect(statusForm.elements.target_asset_id,true);statusForm.elements.id.value=status?.id||'';statusForm.elements.target_asset_id.value=status?.target_asset_id||targetId||statusForm.elements.target_asset_id.value;statusForm.elements.duration_turns.value=status?.remaining_turns??'';statusForm.elements.description.value=status?.description||'';document.querySelector('#status-modal-title').textContent=status?'编辑状态卡':'添加状态卡';statusModal.hidden=false;statusForm.elements.description.focus();}
  function closeStatus(){statusModal.hidden=true;}
  document.querySelectorAll('[data-close-status]').forEach((button)=>button.addEventListener('click',closeStatus));
  statusModal.addEventListener('click',(event)=>{if(event.target===statusModal)closeStatus();});
  statusForm.addEventListener('submit',async(event)=>{event.preventDefault();const values=Object.fromEntries(new FormData(statusForm));const id=values.id;delete values.id;values.duration_turns=values.duration_turns?Number(values.duration_turns):null;await api(id?`/api/status-cards/${id}`:`/api/campaigns/${campaignId}/status-cards`,{method:id?'PUT':'POST',body:JSON.stringify(values)});closeStatus();await refreshWorld();toast('状态卡已保存。');});

  function openRelation(relation=null,sourceId=null,targetId=null){relationForm.reset();populateAssetSelect(relationForm.elements.source_asset_id);populateAssetSelect(relationForm.elements.target_asset_id);relationForm.elements.id.value=relation?.id||'';relationForm.elements.source_asset_id.value=relation?.source_asset_id||sourceId||relationForm.elements.source_asset_id.value;relationForm.elements.target_asset_id.value=relation?.target_asset_id||targetId||relationForm.elements.target_asset_id.value;relationForm.elements.label.value=relation?.label||'';relationForm.elements.description.value=relation?.description||'';document.querySelector('#relation-modal-title').textContent=relation?'编辑关系':'建立关系';document.querySelector('#delete-relation').hidden=!relation;relationModal.hidden=false;relationForm.elements.label.focus();}
  function closeRelation(){relationModal.hidden=true;}
  document.querySelectorAll('[data-close-relation]').forEach((button)=>button.addEventListener('click',closeRelation));
  relationModal.addEventListener('click',(event)=>{if(event.target===relationModal)closeRelation();});
  relationForm.addEventListener('submit',async(event)=>{event.preventDefault();const values=Object.fromEntries(new FormData(relationForm));const id=values.id;delete values.id;await api(id?`/api/relations/${id}`:`/api/campaigns/${campaignId}/relations`,{method:id?'PUT':'POST',body:JSON.stringify(values)});closeRelation();await refreshWorld();toast('关系已保存。');});
  document.querySelector('#delete-relation').addEventListener('click',async()=>{const id=relationForm.elements.id.value;if(!id)return;await api(`/api/relations/${id}`,{method:'DELETE'});closeRelation();await refreshWorld();toast('关系已结束。');});
  document.querySelector('#new-relation').addEventListener('click',()=>{const ids=[...selected];if(ids.length!==2){toast('先在桌布上选中两个资产。',true);return;}openRelation(null,ids[0],ids[1]);});

  function hydrateOutcomes(snapshot){const participantMap=new Map();for(const link of snapshot.outcome_asset_links||[]){if(!participantMap.has(link.outcome_id))participantMap.set(link.outcome_id,[]);participantMap.get(link.outcome_id).push(link.asset_id);}const previousMap=new Map();for(const link of snapshot.outcome_links||[]){if(!previousMap.has(link.target_outcome_id))previousMap.set(link.target_outcome_id,[]);previousMap.get(link.target_outcome_id).push(link.source_outcome_id);}return(snapshot.outcomes||[]).map((item)=>({...item,participant_asset_ids:participantMap.get(item.id)||[],previous_outcome_ids:previousMap.get(item.id)||[]}));}
  function applyWorldSnapshot(snapshot,applyView=false){assets=(snapshot.assets||[]).filter((item)=>!item.archived);statusCards=snapshot.status_cards||[];relations=snapshot.relations||[];outcomes=hydrateOutcomes(snapshot);characterStats={};for(const stat of snapshot.character_stats||[]){if(!characterStats[stat.asset_id])characterStats[stat.asset_id]=[];characterStats[stat.asset_id].push(stat);}if(applyView&&snapshot.canvas_view){panX=Number(snapshot.canvas_view.pan_x||0);panY=Number(snapshot.canvas_view.pan_y||0);zoom=Number(snapshot.canvas_view.zoom||1);setTransform();}selected.forEach((id)=>{if(!assets.some((asset)=>asset.id===id))selected.delete(id);});renderAssets();renderChips();}
  async function refreshTimeline(){timeline=await api(`/api/campaigns/${campaignId}/timeline`);renderTimeline();}
  async function refreshWorld(){const response=await api(`/api/campaigns/${campaignId}/world`);readOnlyWorld=false;historicalCommitId=null;applyWorldSnapshot(response.world);await refreshTimeline();updateHistoryMode();}

  function activeBranch(){return timeline.branches.find((branch)=>branch.id===timeline.active_branch_id);}
  function branchLineage(){const branch=activeBranch();const byId=new Map(timeline.commits.map((commit)=>[commit.id,commit]));const result=[];let cursor=branch?.head_commit_id;const seen=new Set();while(cursor&&byId.has(cursor)&&!seen.has(cursor)){seen.add(cursor);const commit=byId.get(cursor);result.push(commit);cursor=commit.parent_commit_id;}return result.reverse();}
  function chineseChapterNumber(value){const number=Number(value);if(!Number.isInteger(number)||number<1)return String(value);if(number>=10000)return String(number);const digits=['零','一','二','三','四','五','六','七','八','九'];const units=['','十','百','千'];let result='';let pendingZero=false;for(let index=3;index>=0;index-=1){const unitValue=10**index;const digit=Math.floor(number/unitValue)%10;if(digit){if(pendingZero&&result)result+='零';if(!(digit===1&&index===1&&!result))result+=digits[digit];result+=units[index];pendingZero=false;}else if(result&&number%unitValue)pendingZero=true;}return result;}
  function chapterLabel(turnNumber){return Number(turnNumber)>0?`第${chineseChapterNumber(turnNumber)}回`:'楔子';}
  function chapterGroups(lineage){const groups=[];for(const commit of lineage){const key=Number(commit.turn_number||0);let group=groups.at(-1);if(!group||group.turnNumber!==key){group={turnNumber:key,commits:[]};groups.push(group);}group.commits.push(commit);}return groups;}
  async function selectTimelineCommit(commit){if(!commit)return;if(commit.id===activeBranch()?.head_commit_id){await refreshWorld();return;}await loadCommit(commit.id);}
  function renderTimeline(){
    const select=document.querySelector('#branch-select');select.replaceChildren();
    for(const branch of timeline.branches){const option=document.createElement('option');option.value=branch.id;option.textContent=branch.name;select.append(option);}
    select.value=timeline.active_branch_id;
    const branch=activeBranch();const lineage=branchLineage();const groups=chapterGroups(lineage);const list=document.querySelector('#chapter-list');list.replaceChildren();
    let selectedId=historicalCommitId||branch?.head_commit_id;let selectedIndex=lineage.findIndex((commit)=>commit.id===selectedId);if(selectedIndex<0){selectedIndex=lineage.length-1;selectedId=lineage[selectedIndex]?.id;}
    const selectedCommit=lineage[selectedIndex];
    if(!groups.length){const empty=document.createElement('p');empty.className='chapter-empty';empty.textContent='这卷故事还没有章回。';list.append(empty);}
    for(const group of groups){
      const latest=group.commits.at(-1);const main=group.commits.find((commit)=>commit.kind==='turn')||latest;const containsSelection=group.commits.some((commit)=>commit.id===selectedId);
      const section=document.createElement('section');section.className='chapter-group';
      const heading=document.createElement('button');heading.type='button';heading.className=`chapter-heading${containsSelection?' active':''}`;heading.dataset.commitId=latest.id;heading.innerHTML='<span class="chapter-ordinal"></span><strong></strong><small></small>';heading.querySelector('.chapter-ordinal').textContent=chapterLabel(group.turnNumber);heading.querySelector('strong').textContent=main.summary;heading.querySelector('small').textContent=group.commits.length>1?`${group.commits.length} 版`:'';heading.addEventListener('click',()=>selectTimelineCommit(latest));section.append(heading);
      if(group.commits.length>1){const revisions=document.createElement('div');revisions.className='chapter-revisions';group.commits.forEach((commit,index)=>{const revision=document.createElement('button');revision.type='button';revision.className=`chapter-revision${commit.id===selectedId?' active':''}`;revision.dataset.commitId=commit.id;if(commit.id===selectedId)revision.setAttribute('aria-current','step');const version=commit.kind==='baseline'?'原始设定':commit.kind==='turn'?'回合定稿':`修订 ${index}`;revision.innerHTML='<span></span><span></span>';revision.children[0].textContent=version;revision.children[1].textContent=commit.summary;revision.addEventListener('click',()=>selectTimelineCommit(commit));revisions.append(revision);});section.append(revisions);}
      list.append(section);
    }
    const currentTitle=selectedCommit?chapterLabel(selectedCommit.turn_number):'尚无章回';document.querySelector('#chapter-current').textContent=branch?`${branch.name} · ${currentTitle}`:currentTitle;document.querySelector('#chapter-current-summary').textContent=selectedCommit?.summary||'等待故事开始';document.querySelector('#chapter-count').textContent=`${groups.length} 章 · ${lineage.length} 版`;
    const selectedGroupIndex=groups.findIndex((group)=>group.commits.some((commit)=>commit.id===selectedId));document.querySelector('#timeline-prev').disabled=selectedGroupIndex<=0;document.querySelector('#timeline-next').disabled=selectedGroupIndex<0||selectedGroupIndex>=groups.length-1;document.querySelector('#timeline-latest').disabled=!readOnlyWorld;document.querySelector('#timeline-branch').disabled=!selectedCommit;
    if(!document.querySelector('#chapter-drawer').hidden)requestAnimationFrame(()=>list.querySelector('[aria-current="step"],.chapter-heading.active')?.scrollIntoView({block:'nearest'}));
  }
  function updateHistoryMode(){const notice=document.querySelector('#history-notice');notice.hidden=!readOnlyWorld;if(readOnlyWorld)notice.querySelector('strong').textContent=`${document.querySelector('#chapter-current').textContent} · ${document.querySelector('#chapter-current-summary').textContent}`;directorInput.disabled=readOnlyWorld;document.querySelector('#new-asset').disabled=readOnlyWorld;document.querySelector('#new-relation').disabled=readOnlyWorld;document.querySelector('#open-dice-panel').disabled=readOnlyWorld;document.querySelector('#open-coc-panel').disabled=readOnlyWorld;}
  async function loadCommit(commitId){const response=await api(`/api/campaigns/${campaignId}/world?commit_id=${encodeURIComponent(commitId)}`);historicalCommitId=commitId;readOnlyWorld=true;selected.clear();applyWorldSnapshot(response.world,true);renderTimeline();updateHistoryMode();}
  function adjacentChapter(delta){const groups=chapterGroups(branchLineage());const selectedId=historicalCommitId||activeBranch()?.head_commit_id;const index=groups.findIndex((group)=>group.commits.some((commit)=>commit.id===selectedId));return groups[index+delta]?.commits.at(-1);}
  const chapterToggle=document.querySelector('#chapter-toggle');const chapterDrawer=document.querySelector('#chapter-drawer');
  function setChapterDrawer(open){chapterDrawer.hidden=!open;chapterToggle.setAttribute('aria-expanded',String(open));if(open)requestAnimationFrame(()=>document.querySelector('#chapter-list').querySelector('[aria-current="step"],.chapter-heading.active')?.scrollIntoView({block:'nearest'}));}
  chapterToggle.addEventListener('click',()=>setChapterDrawer(chapterDrawer.hidden));
  document.querySelector('#branch-select').addEventListener('change',async(event)=>{await api(`/api/branches/${event.target.value}/activate`,{method:'POST'});location.reload();});
  document.querySelector('#timeline-prev').addEventListener('click',()=>selectTimelineCommit(adjacentChapter(-1)));
  document.querySelector('#timeline-next').addEventListener('click',()=>selectTimelineCommit(adjacentChapter(1)));
  document.querySelector('#timeline-latest').addEventListener('click',refreshWorld);
  document.querySelector('#timeline-branch').addEventListener('click',async()=>{const lineage=branchLineage();const commit=historicalCommitId||lineage.at(-1)?.id;if(!commit)return;await api(`/api/campaigns/${campaignId}/branches`,{method:'POST',body:JSON.stringify({from_commit_id:commit})});location.reload();});
  document.querySelectorAll('[data-layer]').forEach((input)=>input.addEventListener('change',()=>{visibleLayers[input.dataset.layer]=input.checked;renderAssets();}));
  document.querySelector('#outcome-range').addEventListener('change',renderAssets);

  document.querySelector('#open-dice-panel').addEventListener('click', () => openDicePanel());
  document.querySelector('#close-dice-panel').addEventListener('click', () => { dicePanel.hidden=true; directorInput.focus(); });
  diceCharacter.addEventListener('change', populateDiceStats);
  diceStat.addEventListener('change', () => {
    const option=diceStat.selectedOptions[0]; if (!option?.dataset.stat) return;
    const stat=JSON.parse(option.dataset.stat); const notation=parseNotation(stat.notation);
    document.querySelector('#dice-label').value=stat.name;
    if (stat.value !== null) { document.querySelector('#dice-count').value=1; document.querySelector('#dice-sides').value=100; document.querySelector('#dice-modifier').value=0; document.querySelector('#dice-comparison').value='lte'; document.querySelector('#dice-target').value=stat.value; }
    else if (notation) { document.querySelector('#dice-count').value=notation.count; document.querySelector('#dice-sides').value=notation.sides; document.querySelector('#dice-modifier').value=notation.modifier; }
  });
  document.querySelectorAll('.quick-dice button').forEach((button) => button.addEventListener('click', () => {
    document.querySelector('#dice-sides').value=button.dataset.sides;
    if (document.querySelector('#dice-label').value === '命运判定') document.querySelector('#dice-label').value=`d${button.dataset.sides} 判定`;
  }));
  document.querySelector('#add-dice-operation').addEventListener('click', () => {
    try { diceOperations.push(operationFromFields()); renderDiceTray(); } catch (error) { toast(error.message, true); }
  });
  document.querySelector('#clear-dice-tray').addEventListener('click', () => { diceOperations=[]; explicitProposal=null; renderDiceTray(); document.querySelector('#explicit-dice-badge').hidden=true; });
  document.querySelector('#use-dice-group').addEventListener('click', () => {
    if (!diceOperations.length) { try { diceOperations.push(operationFromFields()); } catch (error) { toast(error.message, true); return; } }
    const actorIds=new Set(diceOperations.map((operation)=>operation.target_asset_id).filter(Boolean));
    const onlyActorId=actorIds.size===1 ? [...actorIds][0] : null;
    const sharedActorId=onlyActorId && diceOperations.every((operation)=>operation.target_asset_id===onlyActorId) ? onlyActorId : null;
    const target=sharedActorId ? assets.find((asset)=>asset.id===sharedActorId) : null;
    const repeatCount=Number(document.querySelector('#dice-repeat').value || 1);
    let sequenceMode=document.querySelector('#dice-sequence-mode').value;
    if (repeatCount === 1) sequenceMode='batch';
    explicitProposal={title:repeatCount>1?'连续投骰':'显式投骰', explanation:'由导演在骰子面板中明确设置，不经过小模型解析。普通检定不会改写人物基础属性。', character_name:target?.name || null, target_asset_id:target?.id || null, create_character:false, writeback_to_character:false, operations:diceOperations, repeat_count:repeatCount, sequence_mode:sequenceMode, warnings:[]};
    const badge=document.querySelector('#explicit-dice-badge'); badge.hidden=false; badge.textContent=`已装载 ${diceOperations.length} 项 · ${repeatCount} 次${sequenceMode==='stepwise'?' · 分步':''}`;
    dicePanel.hidden=true; slashMenu.hidden=true;
    if (!directorInput.value.trim() || directorInput.value.trim().startsWith('/')) directorInput.value='根据这组真实骰子结果判定并推进故事';
    enterKey.textContent='Enter 确认骰组'; turnStatus.textContent='显式骰组已装载，不调用小模型'; directorInput.focus();
  });
  document.querySelector('#open-coc-panel').addEventListener('click', () => openCocPanel().catch(() => {}));
  document.querySelector('#close-coc-panel').addEventListener('click', () => { cocPanel.hidden=true; directorInput.focus(); });
  cocForm.elements.attacker_asset_id.addEventListener('change', syncCocSkills); cocForm.elements.defender_asset_id.addEventListener('change', syncCocSkills); cocForm.elements.defense.addEventListener('change', syncCocSkills); cocForm.elements.attack_kind.addEventListener('change', configureCocDefense); cocForm.elements.weapon_preset.addEventListener('change', applyCocWeaponPreset);
  cocForm.addEventListener('submit', async (event) => {
    event.preventDefault(); const button=cocForm.querySelector('button[type="submit"]'); button.disabled=true; button.textContent='裁定中…';
    try {
      const payload={request_id:crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random()}`,attacker_asset_id:cocForm.elements.attacker_asset_id.value,defender_asset_id:cocForm.elements.defender_asset_id.value,attack_kind:cocForm.elements.attack_kind.value,defense:cocForm.elements.defense.value,attack_skill:Number(cocForm.elements.attack_skill.value),defense_skill:Number(cocForm.elements.defense_skill.value),bonus_dice:Number(cocForm.elements.bonus_dice.value),weapon_name:cocForm.elements.weapon_name.value.trim(),damage:cocForm.elements.damage.value.trim(),use_damage_bonus:cocForm.elements.use_damage_bonus.checked,impaling:cocForm.elements.impaling.checked,shots:Number(cocForm.elements.shots.value),point_blank:cocForm.elements.point_blank.checked,outnumbered:cocForm.elements.outnumbered.checked,maneuver_effect:cocForm.elements.maneuver_effect.value.trim()};
      const response=await api(`/api/campaigns/${campaignId}/coc/combat`, {method:'POST',body:JSON.stringify(payload)}); cocProfiles=response.combatants; renderCocRoster(); showCocResult(response.result); document.querySelector('#coc-round').textContent=`第 ${response.encounter?.round_number || 1} 轮 · 结果已写入故事上下文`; await refreshMessages();
    } finally { button.disabled=false; button.textContent='立即裁定'; }
  });
  document.querySelector('#coc-next-round').addEventListener('click', async () => { const response=await api(`/api/campaigns/${campaignId}/coc/next-round`, {method:'POST'}); cocProfiles=response.combatants; renderCocRoster(); document.querySelector('#coc-round').textContent=`第 ${response.encounter.round_number} 轮`; });
  slashMenu.addEventListener('click', (event) => { const button=event.target.closest('button[data-command]'); if (!button) return; if (button.dataset.command==='coc') openCocPanel().catch(()=>{}); else openDicePanel(button.dataset.command); });

  const storyHeightKey = `anko-story-height:${campaignId}`;
  const keepAssetsKey = `anko-keep-assets:${campaignId}`;
  const selectedAssetsKey = `anko-selected-assets:${campaignId}`;
  function persistSelectedAssets() {
    if (keepAssetReferences.checked) localStorage.setItem(selectedAssetsKey, JSON.stringify([...selected]));
    else localStorage.removeItem(selectedAssetsKey);
  }
  keepAssetReferences.checked = localStorage.getItem(keepAssetsKey) === 'true';
  if (keepAssetReferences.checked) {
    try {
      const savedAssetIds = JSON.parse(localStorage.getItem(selectedAssetsKey) || '[]');
      const availableIds = new Set(assets.map((asset) => asset.id));
      for (const id of savedAssetIds) if (availableIds.has(id)) selected.add(id);
    } catch (_) {
      localStorage.removeItem(selectedAssetsKey);
    }
  }
  keepAssetReferences.addEventListener('change', () => {
    localStorage.setItem(keepAssetsKey, String(keepAssetReferences.checked));
    persistSelectedAssets();
  });
  const savedStoryHeight = Number(localStorage.getItem(storyHeightKey));
  if (savedStoryHeight > 0) directorLeft.style.setProperty('--story-height', `${savedStoryHeight}px`);
  let resizeState = null;
  function startStoryResize(clientY) {
    resizeState = {
      startY:clientY,
      startHeight:document.querySelector('.story-flow').getBoundingClientRect().height,
    };
    storyResizer.classList.add('dragging');
  }
  function moveStoryResize(clientY) {
    if (!resizeState) return;
    const maximum = Math.max(300, directorLeft.getBoundingClientRect().height - 247);
    const next = Math.max(300, Math.min(maximum, resizeState.startHeight + resizeState.startY - clientY));
    directorLeft.style.setProperty('--story-height', `${next}px`);
  }
  function finishStoryResize() {
    if (!resizeState) return;
    resizeState = null; storyResizer.classList.remove('dragging');
    const height = Math.round(document.querySelector('.story-flow').getBoundingClientRect().height);
    localStorage.setItem(storyHeightKey, String(height));
  }
  storyResizer.addEventListener('mousedown', (event) => { event.preventDefault(); startStoryResize(event.clientY); });
  document.addEventListener('mousemove', (event) => moveStoryResize(event.clientY));
  document.addEventListener('mouseup', finishStoryResize);
  storyResizer.addEventListener('touchstart', (event) => { event.preventDefault(); startStoryResize(event.touches[0].clientY); }, {passive:false});
  document.addEventListener('touchmove', (event) => moveStoryResize(event.touches[0].clientY), {passive:true});
  document.addEventListener('touchend', finishStoryResize);

  function showProposal(preview) {
    document.querySelector('#turn-title').textContent = preview.title;
    document.querySelector('#turn-character').textContent = preview.character_name ? `${preview.writeback_to_character ? '写入人物' : '判定人物'}：${preview.character_name}` : '';
    const sequence = preview.repeat_count > 1 ? `连续 ${preview.repeat_count} 次 · ${preview.sequence_mode === 'stepwise' ? '逐次按 Enter' : '一次投完'}` : '';
    document.querySelector('#turn-explanation').textContent = [preview.explanation, sequence].filter(Boolean).join(' · ');
    const list = document.querySelector('#turn-items'); list.replaceChildren();
    for (const item of preview.items) { const li = document.createElement('li'); li.innerHTML = `<strong></strong><span></span>`; li.querySelector('strong').textContent = item.label; li.querySelector('span').textContent = item.description; list.append(li); }
    const warnings = document.querySelector('#turn-warnings'); warnings.textContent = preview.warnings.join(' · '); warnings.hidden = !preview.warnings.length;
    turnCard.hidden = false; turnStatus.textContent = explicitProposal ? '显式骰组已确认，等待开骰' : '已解析，等待确认'; enterKey.textContent = preview.sequence_mode === 'stepwise' && preview.repeat_count > 1 ? 'Enter 投第 1 骰' : 'Enter 投骰并写故事';
  }

  function showRollOutcome(result) {
    storyOutput.replaceChildren();
    const resultBox = document.createElement('div'); resultBox.className = 'roll-summary';
    const announcement = document.createElement('strong'); announcement.className = 'roll-announcement'; announcement.textContent = result.roll_announcement || '🎲 投骰完成！'; resultBox.append(announcement);
    const resultDetails = document.createElement('div'); resultDetails.className = 'roll-result-details';
    for (const item of result.roll_summary || []) { const span = document.createElement('span'); span.textContent = `${item.label} · ${item.value}`; resultDetails.append(span); }
    resultBox.append(resultDetails); storyOutput.append(resultBox);
    if (result.turn?.roll_result) {
      evidence.hidden = false;
      document.querySelector('#roll-evidence-content').textContent = JSON.stringify(result.turn.roll_result, null, 2);
    }
    storyScroll.scrollTop = 0;
  }

  async function prepareTurn() {
    const instruction = directorInput.value.trim(); if (!instruction) return;
    turnStatus.textContent = explicitProposal ? '正在确认显式骰组…' : '小模型正在解析…'; directorInput.disabled = true;
    try {
      const assetIds=new Set(selected); if (explicitProposal?.target_asset_id) assetIds.add(explicitProposal.target_asset_id);
      const references=historicalReferences.map(({commit_id,entity_type,entity_id})=>({commit_id,entity_type,entity_id}));
      const result = await api(`/api/campaigns/${campaignId}/turns/prepare`, {method:'POST', body:JSON.stringify({instruction, asset_ids:[...assetIds], historical_references:references, proposal:explicitProposal})});
      activeTurn = result.turn; stepwiseReadyForStory=false; showProposal(result.preview);
    } catch (error) {
      turnStatus.textContent = '解析失败，可修改后重试'; throw error;
    } finally { directorInput.disabled = false; directorInput.focus(); }
  }

  async function executeTurn() {
    if (!activeTurn) return; directorInput.disabled = true;
    try {
      if (!activeTurn.roll_result && activeTurn.proposal.sequence_mode !== 'stepwise') {
        turnStatus.textContent = '正在投骰…';
        const rolled = await api(`/api/turns/${activeTurn.id}/roll`, {method:'POST'});
        activeTurn = rolled.turn; showRollOutcome(rolled);
        turnStatus.textContent = '骰果已公开并保存 · 故事模型正在写作…';
      } else {
        turnStatus.textContent = '骰果已保存 · 故事模型正在写作…';
      }
      const result = await api(`/api/turns/${activeTurn.id}/execute`, {method:'POST'});
      activeTurn = result.turn;
      showRollOutcome(result); const article = document.createElement('article'); article.className = 'story-draft'; article.contentEditable = String(result.delivery === 'local_draft'); article.textContent = result.draft.content; storyOutput.append(article);
      turnCard.hidden = true;
      turnStatus.textContent = result.delivery === 'discord_queued' ? '故事已自动进入 Discord 发布队列' : '本地草稿已生成 · 未连接 Discord'; enterKey.textContent = 'Enter 解析';
      if (!keepAssetReferences.checked) selected.clear();
      if (!keepAssetReferences.checked) historicalReferences=[];
      renderAssets(); renderChips(); directorInput.value = ''; activeTurn = null;
      explicitProposal=null; diceOperations=[]; stepwiseReadyForStory=false; renderDiceTray(); document.querySelector('#explicit-dice-badge').hidden=true;
      storyScroll.scrollTop = 0;
      await Promise.all([refreshMessages(),refreshWorld()]);
    } catch (error) {
      turnStatus.textContent = '生成失败 · 再按 Enter 安全重试（不会重投）'; throw error;
    } finally { directorInput.disabled = false; directorInput.focus(); }
  }

  async function rollNext() {
    if (!activeTurn) return; directorInput.disabled=true; turnStatus.textContent='正在投下一骰…';
    try {
      const result=await api(`/api/turns/${activeTurn.id}/roll-next`, {method:'POST'}); activeTurn=result.turn;
      if (result.roll_announcement) {
        if (!storyOutput.querySelector('.stepwise-rolls')) { storyOutput.replaceChildren(); const box=document.createElement('div'); box.className='stepwise-rolls'; storyOutput.append(box); }
        const item=document.createElement('div'); item.className='stepwise-roll-item'; item.textContent=result.roll_announcement; storyOutput.querySelector('.stepwise-rolls').append(item); storyScroll.scrollTop=storyScroll.scrollHeight;
      }
      stepwiseReadyForStory=result.complete;
      if (result.complete) { turnStatus.textContent=`连续骰 ${result.current}/${result.total} 已完成`; enterKey.textContent='Enter 生成判定与故事'; }
      else { turnStatus.textContent=`连续骰 ${result.current}/${result.total} · 等待下一骰`; enterKey.textContent=`Enter 投第 ${result.current+1} 骰`; }
    } finally { directorInput.disabled=false; directorInput.focus(); }
  }

  directorInput.addEventListener('keydown', async (event) => {
    if (event.key === 'Escape' && activeTurn) { activeTurn = null; stepwiseReadyForStory=false; turnCard.hidden = true; turnStatus.textContent = '已取消，可修改指令'; enterKey.textContent = explicitProposal ? 'Enter 确认骰组' : 'Enter 解析'; return; }
    if (event.key === '/' && !directorInput.value) { slashMenu.hidden=false; }
    if (event.key !== 'Enter' || event.shiftKey) return;
    event.preventDefault();
    if (!slashMenu.hidden) {
      const value=directorInput.value.trim();
      const command=/coc|战斗/i.test(value)?'coc':value.includes('连续')?'continuous':value.includes('属性')?'attribute':'dice';
      if (command==='coc') await openCocPanel(); else openDicePanel(command); return;
    }
    try {
      if (!activeTurn) await prepareTurn();
      else if (activeTurn.proposal.sequence_mode === 'stepwise' && !stepwiseReadyForStory) await rollNext();
      else await executeTurn();
    } catch (_) {}
  });
  directorInput.addEventListener('input', () => {
    const value=directorInput.value.trim(); slashMenu.hidden=!value.startsWith('/');
  });
  function renderMessages() {
    const feed = document.querySelector('#chat-feed');
    const previousScrollTop = feed.scrollTop;
    const previousScrollHeight = feed.scrollHeight;
    const firstRender = feed.dataset.rendered !== 'true';
    const wasNearBottom = previousScrollHeight - previousScrollTop - feed.clientHeight < 72;
    feed.replaceChildren();
    if (!messages.length) { const empty = document.createElement('p'); empty.className = 'chat-empty'; empty.textContent = '这里会显示本地讨论、Discord 留言和故事预览。'; feed.append(empty); return; }
    const storyMessages = messages.filter((message) => message.source === 'story_preview');
    const storyNumbers = new Map(storyMessages.map((message,index) => [message.id,index+1]));
    const recentStoryIds = new Set(storyMessages.slice(-2).map((message) => message.id));
    for (const message of messages) {
      const row = document.createElement('article'); row.className = `chat-message ${message.source}`;
      const avatar = document.createElement('span'); avatar.className = 'chat-avatar'; avatar.textContent = message.author_name.slice(0,1);
      const body = document.createElement('div'); const meta = document.createElement('div'); meta.className = 'chat-meta';
      const author = document.createElement('strong'); author.textContent = message.author_name; const time = document.createElement('time'); time.textContent = message.created_at.slice(11,16);
      const storyNumber = document.createElement('span'); storyNumber.className = 'story-turn-number'; storyNumber.textContent = message.source === 'story_preview' ? `第 ${storyNumbers.get(message.id)} 回` : '';
      const status = document.createElement('span'); status.className = `message-status ${message.status}`; status.textContent = message.source === 'director_prompt' ? '导演提示' : message.status === 'pending' || message.status === 'retry' ? '待发送' : message.status === 'preview' ? '未发布预览' : '';
      const content = document.createElement('p');
      const olderStory = message.source === 'story_preview' && !recentStoryIds.has(message.id);
      const expandedStory = olderStory && expandedStoryIds.has(message.id);
      content.textContent = olderStory && !expandedStory ? `${message.content.slice(0, 180)}…` : message.content;
      meta.append(author,time,storyNumber,status); body.append(meta,content);
      if (olderStory) {
        const toggle = document.createElement('button'); toggle.type = 'button'; toggle.className = 'chat-story-toggle'; toggle.textContent = expandedStory ? '收起' : '展开全文';
        toggle.addEventListener('click', () => {
          const expanded = expandedStoryIds.has(message.id);
          if (expanded) expandedStoryIds.delete(message.id); else expandedStoryIds.add(message.id);
          content.textContent = expanded ? `${message.content.slice(0, 180)}…` : message.content;
          toggle.textContent = expanded ? '展开全文' : '收起';
        });
        body.append(toggle);
      }
      row.append(avatar,body); feed.append(row);
    }
    feed.dataset.rendered = 'true';
    feed.scrollTop = firstRender || wasNearBottom ? feed.scrollHeight : previousScrollTop;
  }
  async function refreshMessages() { messages = await api(`/api/campaigns/${campaignId}/messages`); renderMessages(); }
  function restoreLatestStory() {
    const latest = [...messages].reverse().find((message) => message.source === 'story_preview');
    if (!latest) return;
    storyOutput.replaceChildren();
    const article = document.createElement('article');
    article.className = 'story-draft restored-story';
    article.contentEditable = 'false';
    article.textContent = latest.content;
    storyOutput.append(article);
    storyScroll.scrollTop = 0;
  }
  const chatForm = document.querySelector('#chat-form');
  chatForm.querySelector('textarea').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); chatForm.requestSubmit(); } });
  chatForm.addEventListener('submit', async (event) => {
    event.preventDefault(); const input = chatForm.elements.content; const content = input.value.trim(); if (!content) return; input.disabled = true;
    try { await api(`/api/campaigns/${campaignId}/messages`, {method:'POST', body:JSON.stringify({content})}); input.value = ''; await refreshMessages(); } finally { input.disabled = false; input.focus(); }
  });
  const bindForm = document.querySelector('#discord-bind');
  if (bindForm) bindForm.addEventListener('submit', async (event) => {
    event.preventDefault(); const values = Object.fromEntries(new FormData(bindForm)); values.forum_channel_id = Number(values.forum_channel_id);
    await api(`/api/campaigns/${campaignId}/discord-bind`, {method:'POST', body:JSON.stringify(values)}); toast('Discord 帖子已创建并绑定。'); setTimeout(() => location.reload(), 650);
  });

  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  let socket;
  let worldSocketSignature='';
  function connectSocket() {
    socket = new WebSocket(`${protocol}://${location.host}/ws/campaigns/${campaignId}`);
    socket.addEventListener('message', (event) => {
      const state = JSON.parse(event.data); messages = state.chat_messages || messages; renderMessages();
      if(!readOnlyWorld&&!assetDrag&&!statusDragId){const signature=JSON.stringify([state.assets,state.status_cards,state.relations,state.outcomes,state.timeline]);if(signature!==worldSocketSignature){worldSocketSignature=signature;assets=state.assets||assets;statusCards=state.status_cards||statusCards;relations=state.relations||relations;outcomes=state.outcomes||outcomes;timeline=state.timeline||timeline;renderAssets();renderChips();renderTimeline();}}
    });
    socket.addEventListener('close', () => setTimeout(connectSocket, 1600));
  }
  repairOverlappingAssetPositions().catch(() => {});
  renderAssets(); renderChips(); renderDiceTray(); renderMessages(); renderTimeline(); updateHistoryMode(); restoreLatestStory(); setTransform(); connectSocket();
})();
