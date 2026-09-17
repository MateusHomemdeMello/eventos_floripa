(() => {
  'use strict';

  const categories = {
    exposicoes:{name:'Exposições',icon:'▣',color:'#6b5b95'},
    apresentacoes:{name:'Apresentações',icon:'★',color:'#b64e34'},
    musica:{name:'Música',icon:'♪',color:'#a23e62'},
    feiras:{name:'Feiras',icon:'▦',color:'#28745d'},
    esportes:{name:'Esportes',icon:'⚡',color:'#087780'},
    gastronomia:{name:'Gastronomia',icon:'●',color:'#916022'},
    cinema_audiovisual:{name:'Cinema e Audiovisual',icon:'▶',color:'#345c83'},
    cursos_oficinas:{name:'Cursos e Oficinas',icon:'✎',color:'#7b6d36'},
    festivais:{name:'Festivais',icon:'✦',color:'#9b4d73'},
    encontros:{name:'Encontros',icon:'◆',color:'#3f6f68'}
  };
  const source = window.QUAL_A_BOA_DATA || {eventos:[],atualizado_em:null};
  const events = Array.isArray(source.eventos) ? source.eventos : [];
  const state = {query:'',category:null,from:null,to:null,view:'map',markers:[]};
  const el = id => document.getElementById(id);
  const html = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
  const parseDate = value => value ? new Date(`${value}T12:00:00`) : null;
  const iso = date => date.toISOString().slice(0,10);
  const addDays = (date,days) => { const copy=new Date(date); copy.setDate(copy.getDate()+days); return copy; };
  const category = key => categories[key] || {name:'Evento',icon:'•',color:'#075c68'};

  function formatDate(event) {
    const start=parseDate(event.data_inicio), end=parseDate(event.data_fim);
    if (!start) return 'Data a confirmar';
    const opts={day:'2-digit',month:'short'};
    let text=start.toLocaleDateString('pt-BR',opts).replace('.','');
    if (end && event.data_fim!==event.data_inicio) text+=` — ${end.toLocaleDateString('pt-BR',opts).replace('.','')}`;
    if (event.hora_inicio) text+=` · ${event.hora_inicio}`;
    return text;
  }

  function filteredEvents() {
    const query=state.query.toLocaleLowerCase('pt-BR').trim();
    return events.filter(event => {
      if (state.category && event.categoria!==state.category) return false;
      const haystack=`${event.nome} ${event.local} ${event.descricao} ${category(event.categoria).name}`.toLocaleLowerCase('pt-BR');
      if (query && !haystack.includes(query)) return false;
      const start=event.data_inicio, end=event.data_fim || start;
      if (state.from && end<state.from) return false;
      if (state.to && start>state.to) return false;
      return true;
    }).sort((a,b) => `${a.data_inicio}${a.hora_inicio||'99:99'}`.localeCompare(`${b.data_inicio}${b.hora_inicio||'99:99'}`));
  }

  function renderCategories() {
    el('categories').innerHTML=Object.entries(categories).map(([key,item]) => `
      <button class="category-button${state.category===key?' active':''}" style="--category:${item.color}" data-category="${key}" type="button">
        <span class="category-icon">${item.icon}</span><span>${item.name}</span>
      </button>`).join('');
    el('categories').querySelectorAll('button').forEach(button => button.addEventListener('click',() => {
      state.category=state.category===button.dataset.category?null:button.dataset.category;
      renderAll();
    }));
  }

  function eventCard(event) {
    const item=category(event.categoria);
    return `<article class="event-card" data-id="${html(event.id)}" tabindex="0" role="button" style="--category:${item.color}" aria-label="Ver ${html(event.nome)}">
      <div class="card-category"><i></i>${item.name}</div>
      <h3>${html(event.nome)}</h3><p>${html(event.descricao || 'Confira os detalhes deste evento.')}</p>
      <div class="card-meta"><span>◷ ${html(formatDate(event))}</span><span>⌖ ${html(event.local || 'Local a confirmar')}</span></div>
      <span class="card-arrow" aria-hidden="true">→</span>
    </article>`;
  }

  function renderList(filtered) {
    el('resultCount').textContent=`${filtered.length} ${filtered.length===1?'evento':'eventos'}`;
    el('eventGrid').innerHTML=filtered.length ? filtered.map(eventCard).join('') : '<div class="empty-state"><strong>Nenhum evento por aqui</strong>Tente remover algum filtro ou buscar outro termo.</div>';
    el('eventGrid').querySelectorAll('.event-card').forEach(card => {
      const open=() => openEvent(events.find(event => String(event.id)===card.dataset.id));
      card.addEventListener('click',open); card.addEventListener('keydown',e => {if(e.key==='Enter'||e.key===' '){e.preventDefault();open();}});
    });
  }

  const map = L.map('map',{zoomControl:false,preferCanvas:true}).setView([-27.5954,-48.5480],12);
  const osmCredit='&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap contributors</a>';
  const esriCredit='Tiles &copy; Esri — Sources: Esri, HERE, Garmin, &copy; OpenStreetMap contributors, and the GIS user community';
  const streetLayer=L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',{maxZoom:19,attribution:osmCredit,referrerPolicy:'strict-origin-when-cross-origin'});
  const localLayer=L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',{maxZoom:20,maxNativeZoom:16,attribution:esriCredit});
  let activeBase=location.protocol==='file:'?localLayer:streetLayer;
  activeBase.addTo(map);
  let osmFailed=false;
  streetLayer.on('tileerror',() => {
    if (map.hasLayer(streetLayer) && !osmFailed) {
      osmFailed=true;
      map.removeLayer(streetLayer);
      activeBase=localLayer;
      localLayer.addTo(map);
    }
  });
  L.control.zoom({position:'topright'}).addTo(map);
  const markerLayer=L.layerGroup().addTo(map);

  function renderMap(filtered) {
    markerLayer.clearLayers(); state.markers=[];
    filtered.forEach(event => {
      if (!Number.isFinite(Number(event.lat)) || !Number.isFinite(Number(event.lng))) return;
      const item=category(event.categoria);
      const icon=L.divIcon({className:'',iconSize:[36,36],iconAnchor:[18,34],html:`<div class="event-marker" style="--category:${item.color}"><span>${item.icon}</span></div>`});
      const marker=L.marker([event.lat,event.lng],{icon,title:event.nome}).addTo(markerLayer);
      marker.bindTooltip(`<b>${html(event.nome)}</b><br>${html(formatDate(event))}`,{direction:'top',offset:[0,-28]});
      marker.on('click',() => openEvent(event)); state.markers.push(marker);
    });
    el('mapCount').textContent=`${filtered.length} ${filtered.length===1?'evento encontrado':'eventos encontrados'}`;
  }

  function openEvent(event) {
    if (!event) return;
    const item=category(event.categoria);
    const directions=`https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(`${event.lat},${event.lng}`)}`;
    el('dialogContent').innerHTML=`<div class="dialog-hero" style="--category:${item.color}"></div>${event.foto?`<img class="dialog-photo" src="${html(event.foto)}" alt="Foto da publicação de ${html(event.nome)}" loading="lazy" referrerpolicy="no-referrer">`:''}<div class="dialog-body">
      <div class="card-category" style="--category:${item.color}"><i></i>${item.name}</div>
      <h2>${html(event.nome)}</h2><p class="dialog-description">${html(event.descricao || 'Sem descrição disponível.')}</p>
      <div class="dialog-meta"><span><b>Data:</b> ${html(formatDate(event))}</span><span><b>Local:</b> ${html(event.local || 'A confirmar')}</span></div>
      <div class="dialog-actions"><a href="${directions}" target="_blank" rel="noopener">Como chegar</a>${event.instagram_url?`<a class="secondary" href="${html(event.instagram_url)}" target="_blank" rel="noopener">Ver publicação</a>`:''}</div>
    </div>`;
    const photo=el('dialogContent').querySelector('.dialog-photo');
    if(photo)photo.addEventListener('error',()=>photo.remove(),{once:true});
    el('eventDialog').showModal();
  }

  function renderAll() {
    renderCategories();
    const filtered=filteredEvents(); renderList(filtered); renderMap(filtered);
    document.querySelectorAll('.preset').forEach(button => button.classList.toggle('active',button.dataset.preset==='all'&&!state.from&&!state.to));
  }

  function setPreset(preset) {
    const today=new Date(); let from=null,to=null;
    if (preset==='today') from=to=iso(today);
    if (preset==='7days'){from=iso(today);to=iso(addDays(today,6));}
    if (preset==='weekend'){
      const saturday=addDays(today,(6-today.getDay()+7)%7); from=iso(saturday);to=iso(addDays(saturday,1));
    }
    state.from=from;state.to=to;el('dateFrom').value=from||'';el('dateTo').value=to||'';
    document.querySelectorAll('.preset').forEach(button => button.classList.toggle('active',button.dataset.preset===preset)); renderAll();
  }

  document.querySelectorAll('.view-tab').forEach(button => button.addEventListener('click',() => {
    state.view=button.dataset.view; document.querySelectorAll('.view-tab').forEach(tab => tab.classList.toggle('active',tab===button));
    el('mapView').classList.toggle('active',state.view==='map'); el('listView').classList.toggle('active',state.view==='list');
    if(state.view==='map') setTimeout(() => map.invalidateSize(),0);
  }));
  el('search').addEventListener('input',event => {state.query=event.target.value;renderAll();});
  el('dateFrom').addEventListener('change',event => {state.from=event.target.value||null;renderAll();});
  el('dateTo').addEventListener('change',event => {state.to=event.target.value||null;renderAll();});
  el('datePresets').addEventListener('click',event => {const button=event.target.closest('[data-preset]');if(button)setPreset(button.dataset.preset);});
  el('clearFilters').addEventListener('click',() => {state.query='';state.category=null;el('search').value='';setPreset('all');});
  el('dialogClose').addEventListener('click',() => el('eventDialog').close());
  el('locate').addEventListener('click',() => map.locate({setView:true,maxZoom:15}));
  map.on('locationfound',event => L.circleMarker(event.latlng,{radius:7,color:'#075c68',fillColor:'#fffaf0',fillOpacity:1,weight:3}).addTo(map).bindTooltip('Você está aqui').openTooltip());
  map.on('locationerror',() => alert('Não foi possível acessar sua localização. Verifique a permissão do navegador.'));

  const openSidebar=() => {el('sidebar').classList.add('open');el('scrim').classList.add('open');};
  const closeSidebar=() => {el('sidebar').classList.remove('open');el('scrim').classList.remove('open');};
  el('mobileFilter').addEventListener('click',openSidebar);el('closeSidebar').addEventListener('click',closeSidebar);el('scrim').addEventListener('click',closeSidebar);

  if (source.atualizado_em) {
    const updated=new Date(source.atualizado_em);
    el('lastUpdate').textContent=`Atualizado em ${updated.toLocaleDateString('pt-BR')} às ${updated.toLocaleTimeString('pt-BR',{hour:'2-digit',minute:'2-digit'})}`;
  }
  renderAll();
})();
