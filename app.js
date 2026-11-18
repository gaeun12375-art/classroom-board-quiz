(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const teamColors = ['#48877d','#d18c75','#8e83bd','#d1a24c','#6d9cc2','#be7897','#8c9c70','#878e9a'];
  const labels = {move:'말 이동', special:'특별 퀴즈', portal:'포털 이동', reset:'출발점 복귀', swap:'선두 위치 교환'};
  const faces = ['⚀','⚁','⚂','⚃','⚄','⚅'];
  const example = [
    {text:'대한민국의 수도는 어디인가요?',answer:'서울',hint:'한강이 흐르는 도시예요.'},
    {text:'1년은 몇 개월인가요?',answer:'12|12개월',hint:'한 계절에 약 3개월이 있어요.'},
    {text:'물이 얼면 무엇이 되나요?',answer:'얼음',hint:'0℃ 부근에서 만들어집니다.'},
    {text:'태양계에서 우리가 사는 행성은?',answer:'지구',hint:'지표면에 많은 물이 있어요.'},
    {text:'세 변으로 이루어진 도형의 이름은?',answer:'삼각형',hint:'‘삼’은 숫자 3과 관련이 있어요.'},
    {text:'빨간색과 노란색 물감을 섞으면 어떤 색이 되나요?',answer:'주황색|주황',hint:'귤과 비슷한 색이에요.'},
    {text:'하루는 몇 시간인가요?',answer:'24|24시간',hint:'시계의 반나절을 두 번 합해 보세요.'},
    {text:'무지개의 색은 일반적으로 몇 가지로 구분하나요?',answer:'7|7가지',hint:'빨주노초파남보를 떠올려 보세요.'}
  ];
  let roomCode=null, state=null, teacherKey=null, teamId=null, teamPin=null, watchOnly=false, eventSource=null;
  let lastEffectSeq=null, audioContext=null, localMute=false, loading=false, toastTimer=null;
  const URLparams = new URLSearchParams(location.search);
  const escapeHTML = x => String(x ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const getTiles = count => {
    const goal = count + 7, ratios=[['portal_a',.21],['reset',.38],['swap',.59],['portal_b',.78]], used = new Set(), tiles={};
    for (const [name,rat] of ratios) {
      let p = Math.min(goal-1,Math.max(1,Math.round(goal*rat)));
      while (used.has(p)) p = p < goal-1 ? p+1 : 1;
      used.add(p); tiles[p]=name;
    }
    return {goal,tiles};
  };
  function message(msg, isError=false) {
    const el=$('toast'); el.textContent=msg;el.style.background=isError?'#8d4548':'#173b3c';el.classList.add('show');
    clearTimeout(toastTimer);toastTimer=setTimeout(()=>el.classList.remove('show'),3300);
  }
  function show(page) {
    for (const id of ['landing','builder','game']) $(id).hidden = id!==page;
    window.scrollTo({top:0,behavior:'instant'});
  }
  async function request(path, data, key) {
    const opts={method:data===undefined?'GET':'POST',headers:{'Accept':'application/json'}};
    if (data!==undefined) {opts.headers['Content-Type']='application/json';opts.body=JSON.stringify({data});}
    if (key) opts.headers['X-Teacher-Key']=key;
    let res;
    try {res=await fetch(path,opts);} catch {throw Error('서버에 연결할 수 없습니다. 네트워크를 확인해 주세요.');}
    let body={};try {body=await res.json();}catch{}
    if (!res.ok) throw Error(body.error || body.detail || '요청을 처리할 수 없습니다.');
    return body;
  }
  function loadSoundOptions(containerId,sounds,prefix) {
    $(containerId).innerHTML=Object.entries(labels).map(([k,v])=>`<label class="sound-option"><input type="checkbox" data-sound-key="${k}" ${sounds.enabled[k]?'checked':''} aria-label="${v} 효과음 사용"/><span>${{move:'👣',special:'✨',portal:'🌀',reset:'↩',swap:'↔'}[k]} ${v}</span></label>`).join('');
    const vol=$(prefix+'volume'); if(vol){vol.value=Math.round(sounds.volume*100);$(prefix+'volume-value').textContent=vol.value+'%';}
  }
  function soundFrom(containerId,volumeId) {
    const enabled={};$(containerId).querySelectorAll('[data-sound-key]').forEach(el=>{enabled[el.dataset.soundKey]=el.checked;});
    return {enabled,volume:Number($(volumeId).value)/100};
  }
  function defaultSounds(){return {enabled:Object.fromEntries(Object.keys(labels).map(k=>[k,true])),volume:.4};}
  function teamInputs(count,values=[]) {
    $('team-inputs').innerHTML=Array.from({length:count},(_,i)=>`<label>${i+1}팀 이름<input class="team-name" maxlength="15" required value="${escapeHTML(values[i]||`${i+1}팀`)}" /></label>`).join('');
  }
  function collectQuestions(){
    return [...$('question-list').querySelectorAll('.question-card')].map(card=>({
      text:card.querySelector('.question-text').value, hint:card.querySelector('.question-hint').value,
      type:card.querySelector('.q-type-select').value, answer:card.querySelector('.question-answer').value,
      options:[...card.querySelectorAll('.option-fields input')].map(el=>el.value)
    }));
  }
  function renderQuestions(n,values=[]) {
    $('questions-count-text').textContent=`총 ${n}문항`;
    $('question-list').innerHTML=Array.from({length:n},(_,i)=>{
      const q=values[i]||{text:'',hint:'',answer:'',type:'text',options:['','','','']};
      return `<div class="question-card" data-type="${q.type||'text'}"><div class="q-card-head"><strong>문제 ${String(i+1).padStart(2,'0')}</strong><span>QUESTION ${String(i+1).padStart(2,'0')}</span></div>
        <div class="q-grid"><label class="wide">문제 내용<textarea class="question-text" required placeholder="문제를 입력해 주세요.">${escapeHTML(q.text)}</textarea></label>
        <label>문제 유형<select class="q-type-select"><option value="text" ${(q.type||'text')==='text'?'selected':''}>단답형</option><option value="choice" ${q.type==='choice'?'selected':''}>4지선다형</option></select></label>
        <label>정답 <span class="help">단답형 복수 정답은 | 로 구분</span><input class="question-answer" required value="${escapeHTML(q.answer)}" placeholder="단답형: 정답 / 객관식: 1~4" /></label>
        <div class="option-fields">${[0,1,2,3].map(j=>`<input placeholder="${j+1}번 보기" aria-label="${j+1}번 보기" value="${escapeHTML((q.options||[])[j]||'')}" />`).join('')}</div>
        <label class="wide">힌트 <span class="help">선택 입력</span><input class="question-hint" value="${escapeHTML(q.hint)}" placeholder="힌트를 입력해 주세요." /></label></div></div>`;
    }).join('');
  }
  function collectSpecials() {
    return [...$('special-list').querySelectorAll('.special-card')].map(card=>({
      square:Number(card.querySelector('.special-square').value),text:card.querySelector('.special-question').value,
      answer:card.querySelector('.special-answer').value
    }));
  }
  function addSpecial(data={}) {
    const {goal,tiles}=getTiles(Number($('set-count').value));
    const taken=new Set(collectSpecials().map(x=>x.square));
    const first=Array.from({length:goal-1},(_,i)=>i+1).find(i=>!tiles[i]&&!taken.has(i));
    if (!first && !data.square) {message('특별 카드 칸을 더 배치할 수 없습니다.',true);return;}
    const box=document.createElement('div');box.className='special-card';
    box.innerHTML=`<div class="q-card-head"><strong>★ 특별 퀴즈 카드</strong><button class="remove" type="button">삭제 ×</button></div><div class="q-grid"><label>도착할 칸 번호<input type="number" class="special-square" min="1" max="${goal-1}" required value="${data.square||first}" /></label><label>교사용 정답<input class="special-answer" value="${escapeHTML(data.answer||'')}" placeholder="판정 참고용" /></label><label class="wide">특별 문제<textarea class="special-question" required placeholder="예: 세상에서 가장 뜨거운 바다는?">${escapeHTML(data.text||'')}</textarea></label></div>`;
    box.querySelector('.remove').onclick=()=>{box.remove();updateBoardRule();};
    box.querySelector('.special-square').onchange=updateBoardRule;
    $('special-list').appendChild(box);
    updateBoardRule();
  }
  function updateBoardRule(){
    const n=Number($('set-count').value),{goal,tiles}=getTiles(n);
    const rows=Object.entries(tiles).map(([p,t])=>({p:Number(p),t}));
    for(const x of collectSpecials())if(x.square)rows.push({p:x.square,t:'special'});
    rows.sort((a,b)=>a.p-b.p);
    const tileLabel={portal_a:'🌀 포털 A',portal_b:'🌀 포털 B',reset:'↩ 처음으로',swap:'↔ 선두 교환',special:'★ 특별 퀴즈'};
    $('board-rule-preview').innerHTML=`<div class="rule-row"><span class="rule-number">${goal}</span><strong>🏁 결승점</strong></div>` + rows.map(x=>`<div class="rule-row"><span class="rule-number">${x.p}</span><strong>${tileLabel[x.t]}</strong></div>`).join('');
    $('reserved-info').textContent=`총 ${goal+1}칸(0~${goal}번). 특별 퀴즈는 ${Object.keys(tiles).join('·')}번을 제외한 1~${goal-1}번 칸에 배치해 주세요.`;
    $('special-list').querySelectorAll('.special-square').forEach(el=>el.max=goal-1);
  }
  function setupBuilder(){
    show('builder');
    teamInputs(Number($('set-teams').value));
    renderQuestions(Number($('set-count').value));
    loadSoundOptions('sound-options',defaultSounds(),'sound-');
    updateBoardRule();
  }
  async function createGame(evt){
    evt.preventDefault(); if(loading)return;
    const specials=collectSpecials(),count=Number($('set-count').value);
    const {goal,tiles}=getTiles(count);
    if(specials.some(s=>s.square<1||s.square>=goal||tiles[s.square])) {message('특별 퀴즈 카드 위치가 함정·포털이나 범위 밖에 있어요.',true);return;}
    if(new Set(specials.map(s=>s.square)).size!==specials.length){message('특별 퀴즈 카드의 칸 번호가 겹칩니다.',true);return;}
    const payload={questionCount:count,teamCount:Number($('set-teams').value),retries:Number($('set-retries').value),
      teamNames:[...document.querySelectorAll('.team-name')].map(x=>x.value),questions:collectQuestions(),specials,
      sounds:soundFrom('sound-options','sound-volume')};
    try{
      loading=true;$('create-btn').disabled=true;$('create-btn').textContent='게임방 생성 중…';
      const result=await request('/api/rooms',payload);
      localStorage.setItem('boardTeacher_'+result.code,result.teacherKey);
      location.href='/?room='+result.code+'&role=teacher&key='+encodeURIComponent(result.teacherKey);
    }catch(e){message(e.message,true);}
    finally{loading=false;$('create-btn').disabled=false;$('create-btn').textContent='게임방 만들기 ↗';}
  }
  function roomStudentURL(){return location.origin+'/?room='+roomCode;}
  function roomDisplayURL(){return location.origin+'/?room='+roomCode+'&display=1';}
  const isTeacher=()=>!!teacherKey;
  const isDisplay=()=>URLparams.get('display')==='1';
  const isActive=()=>isTeacher()||(teamId!==null&&teamPin&&state?.turnIndex===teamId);
  function actor(){return {teamId, pin:teamPin};}
  async function refreshRoom(){
    if(!roomCode)return;
    try{
      const updated=await request('/api/rooms/'+roomCode,undefined,teacherKey);
      if(lastEffectSeq===null)lastEffectSeq=updated.effectSeq;
      else{
        const incoming=updated.effects.filter(x=>x.seq>lastEffectSeq);
        for(let i=0;i<incoming.length;i++)setTimeout(()=>{effect(incoming[i].type);message(incoming[i].message);},i*360);
        lastEffectSeq=Math.max(lastEffectSeq,updated.effectSeq);
      }
      state=updated;renderRoom();$('status-tag').hidden=false;
    }catch(e){$('status-tag').hidden=true;message(e.message,true);}
  }
  async function sendAction(a,extra={}){
    if(loading)return;unlockAudio();
    try{loading=true; const data={action:a,...actor(),...extra};const out=await request('/api/rooms/'+roomCode+'/action',data,teacherKey);state=out.state;renderRoom();}
    catch(e){message(e.message,true);}finally{loading=false;}
  }
  function startLiveEvents(){
    if(eventSource)eventSource.close();
    eventSource=new EventSource('/api/rooms/'+roomCode+'/events');
    eventSource.addEventListener('change',()=>refreshRoom());
    eventSource.onerror=()=>{$('status-tag').hidden=true;};
  }
  async function openRoom(code){
    roomCode=String(code).toUpperCase();
    teacherKey=URLparams.get('role')==='teacher' ? (URLparams.get('key')||localStorage.getItem('boardTeacher_'+roomCode)) : null;
    if(teacherKey)localStorage.setItem('boardTeacher_'+roomCode,teacherKey);
    teamId=null;teamPin=null;watchOnly=isDisplay();
    if(!teacherKey && !isDisplay()){
      try{const stored=JSON.parse(localStorage.getItem('boardTeam_'+roomCode)||'null');if(stored){teamId=stored.teamId;teamPin=stored.pin;}}
      catch{}
    }
    show('game');$('room-code-label').textContent=roomCode;
    $('student-url').value=roomStudentURL();$('display-url').value=roomDisplayURL();
    $('qr-image').src='/api/rooms/'+roomCode+'/qr';
    await refreshRoom();
    startLiveEvents();
  }
  const squareLabel={portal_a:'🌀 포털 A',portal_b:'🌀 포털 B',reset:'↩ 처음으로',swap:'↔ 선두 교환',special:'★ 특별 퀴즈'};
  function renderBoard(){
    const columns=7,goal=state.goal, teamNodes=Array.from({length:goal+1},()=>[]);
    state.teams.forEach((t,i)=>teamNodes[t.position].push(`<span class="piece ${state.turnIndex===i?'active':''}" title="${escapeHTML(t.name)}" style="background:${teamColors[i]}">${i+1}</span>`));
    const squares=[];
    for(let p=0;p<=goal;p++){
      let type=p===0?'start':p===goal?'finish':state.tiles[p]||((state.specials.some(x=>x.square===p))?'special':'normal');
      squares.push(`<div class="square ${type} ${state.teams[state.turnIndex].position===p?'is-target':''}" aria-label="${p}번 칸 ${squareLabel[type]||''}"><span class="square-id">${String(p).padStart(2,'0')}</span><div class="pieces">${teamNodes[p].join('')}</div><span class="square-label">${p===0?'START':p===goal?'🏁 FINISH':squareLabel[type]||''}</span></div>`);
    }
    // Each second row flows right-to-left to form a snake path.
    const rows=[];
    for(let i=0;i<squares.length;i+=columns){const group=squares.slice(i,i+columns);if(Math.floor(i/columns)%2===1)group.reverse();rows.push(...group);}
    $('board').style.setProperty('--columns',columns);
    $('board').innerHTML=rows.join('');
    $('board-goal').textContent=`${goal+1}칸`;
  }
  function renderTeacher(){
    $('teacher-tools').hidden=!isTeacher();
    if(!isTeacher())return;
    $('start-game').hidden=state.status!=='waiting';
    $('skip-turn').hidden=state.status!=='playing';
    $('pin-list').innerHTML=state.teams.map((t,i)=>`<div class="pin-item"><span>${escapeHTML(t.name)}</span><b>${t.pin}</b></div>`).join('');
    const setting=state.config.sounds;
    loadSoundOptions('live-sound-options',setting,'live-');
  }
  function renderJoin(){
    const canJoin=!isTeacher()&&!isDisplay()&&!watchOnly&&teamId===null;
    $('join-team-panel').hidden=!canJoin;
    if(canJoin){
      const select=$('choose-team'),selected=select.value;
      select.innerHTML=state.teams.map((t,i)=>`<option value="${i}">${escapeHTML(t.name)}</option>`).join('');
      if(selected)select.value=selected;
    }
    $('mode-chip').textContent=isTeacher()?'교사 · 관리자':isDisplay()?'공유용 전광판':teamId!==null?state.teams[teamId]?.name+' · 참가':'학생 · 관전';
  }
  function renderQuestion(){
    const q=state.current, active=isActive(),panel=$('question-panel');
    if(!q || state.phase!=='question'){panel.hidden=true;return;}
    panel.hidden=false;
    const now=state.teams[state.turnIndex],remaining=1+state.config.retries-q.attempts;
    let form='';
    if(active){
      if(q.type==='choice')form=`<form class="answer-form" id="answer-form">${q.options.map((op,i)=>`<label class="choice-option"><input type="radio" name="answer" value="${i+1}" required/> <span>${i+1}. ${escapeHTML(op)}</span></label>`).join('')}<button type="submit" class="btn btn-primary btn-full">정답 제출 →</button></form>`;
      else form=`<form class="answer-form" id="answer-form"><label class="sr-only" for="answer-input">문제 정답</label><input id="answer-input" autocomplete="off" placeholder="정답을 입력하세요" maxlength="250" required/><button class="btn btn-primary btn-full" type="submit">정답 제출 →</button></form>`;
    } else form=`<div class="waiting-indicator">다른 팀의 답안 입력은 잠겨 있습니다.</div>`;
    panel.innerHTML=`<span class="question-kicker">📖 FORMATIVE QUIZ · ${String(q.id+1).padStart(2,'0')}</span><h3>${escapeHTML(q.text)}</h3>${form}
      <div class="hint-control"><span class="help-inline">답안 제출 기회: ${remaining}회 · 팀 힌트 잔여 ${5-now.hintsUsed}회</span>${active&&q.hintRevealed===false&&q.hint?'<button id="hint-btn" class="btn btn-sm btn-outline">💡 힌트 -1회</button>':''}</div>
      ${q.hintRevealed?`<div class="hint-reveal"><strong>💡 힌트</strong> ${escapeHTML(q.hint)}</div>`:''}
      ${isTeacher()?`<details class="teacher-answer"><summary>🔒 교사용 정답 확인 (클릭 시 공개)</summary><p>${escapeHTML(q.answer)}</p></details>`:''}`;
    $('answer-form')?.addEventListener('submit',evt=>{
      evt.preventDefault();let answer='';
      if(q.type==='choice')answer=new FormData(evt.target).get('answer');else answer=$('answer-input').value;
      if(!answer){message('정답을 선택해 주세요.',true);return;}
      sendAction('answer',{answer:String(answer)});
    });
    $('hint-btn')?.addEventListener('click',()=>sendAction('hint'));
  }
  function renderSpecial(){
    const panel=$('special-panel'),q=state.current;
    if(!q || state.phase!=='special'){panel.hidden=true;return;}
    panel.hidden=false;
    panel.innerHTML=`<span class="special-badge">★ SPECIAL QUIZ CARD · ${q.square}칸</span><h3>${escapeHTML(q.text)}</h3><p class="help-inline">특별 퀴즈의 답은 말로 이야기해 주세요. 정답 여부는 선생님이 판정합니다.</p>
    ${isTeacher()?`<details class="teacher-answer"><summary>🔒 교사용 정답 확인</summary><p>${escapeHTML(q.answer)||'정답 기준을 교사가 판정합니다.'}</p></details><div class="grade-actions"><button id="special-yes" class="btn btn-correct">✓ 정답 · 머무르기</button><button id="special-no" class="btn btn-wrong">✕ 오답 · 돌아가기</button></div>`:'<div class="waiting-indicator">교사의 판정을 기다리는 중입니다.</div>'}`;
    $('special-yes')?.addEventListener('click',()=>sendAction('special_correct'));
    $('special-no')?.addEventListener('click',()=>sendAction('special_wrong'));
  }
  function renderRoom(){
    if(!state)return;
    renderTeacher();renderJoin();renderBoard();
    const active=state.teams[state.turnIndex];
    $('turn-label').textContent=state.status==='waiting'?'게임 시작 전':state.status==='finished'?'게임 종료':'진행 중';
    $('round-number').textContent='TURN '+String(state.turnNumber).padStart(2,'0');
    $('active-team-name').textContent=active.name;
    $('team-color-icon').style.background=teamColors[state.turnIndex];
    $('team-color-icon').textContent=state.turnIndex+1;
    $('turn-status').textContent=state.message;
    $('dice-panel').hidden=!state.dice;
    if(state.dice){const d=state.dice;$('die-a').textContent=faces[d.a-1];$('die-b').textContent=faces[d.b-1];$('dice-equation').textContent=`${d.a} ${d.op} ${d.b} = ${d.steps}`;}
    $('roll-button').hidden=state.phase!=='roll'||state.status!=='playing'||!isActive();
    $('locked-note').hidden=state.status!=='playing'||isActive()||!['question','roll'].includes(state.phase);
    renderQuestion();renderSpecial();
    $('rankings').innerHTML=state.teams.map((t,i)=>`<div class="rank-row ${state.turnIndex===i&&state.status==='playing'?'is-current':''}"><div class="rank-avatar" style="background:${teamColors[i]}">${i+1}</div><div class="rank-main"><div class="rank-name">${escapeHTML(t.name)} ${state.winner===i?'🏆':''}</div><div class="rank-track"><div class="rank-track-fill" style="background:${teamColors[i]};width:${Math.round(100*t.position/state.goal)}%"></div></div></div><div class="rank-meta">${t.position} / ${state.goal}칸<br/>💡 ${5-t.hintsUsed}회</div></div>`).join('');
    $('history-list').innerHTML=state.history.map(x=>`<div class="history-item">${escapeHTML(x.text)}</div>`).join('') || '<div class="muted">아직 기록이 없습니다.</div>';
  }
  function unlockAudio(){
    if(!audioContext){try{audioContext=new(window.AudioContext||window.webkitAudioContext)();}catch{message('이 브라우저에서는 효과음을 지원하지 않습니다.',true);return;}}
    if(audioContext.state==='suspended') audioContext.resume();
    localMute=false;$('audio-unlock').textContent='♫ 소리 켜짐';
  }
  function tone(freq,at,duration,kind='sine',gain=.2,endFreq=null){
    if(!audioContext)return;
    const osc=audioContext.createOscillator(),vol=audioContext.createGain();osc.type=kind;osc.frequency.setValueAtTime(freq,at);
    if(endFreq)osc.frequency.exponentialRampToValueAtTime(Math.max(20,endFreq),at+duration);
    vol.gain.setValueAtTime(.0001,at);vol.gain.exponentialRampToValueAtTime(Math.max(.0001,gain),at+.02);
    vol.gain.exponentialRampToValueAtTime(.0001,at+duration);
    osc.connect(vol);vol.connect(audioContext.destination);osc.start(at);osc.stop(at+duration+.02);
  }
  function effect(kind){
    if(!state||!audioContext||audioContext.state==='suspended'||localMute)return;
    const setting=state.config.sounds;if(!setting.enabled[kind])return;
    const volume=setting.volume*.32;if(volume<=0)return;
    const t=audioContext.currentTime+.02;
    if(kind==='move'){tone(370,t,.13,'sine',volume);tone(480,t+.12,.14,'sine',volume);tone(590,t+.25,.18,'triangle',volume);}
    if(kind==='special'){[660,880,990,1320].forEach((f,i)=>tone(f,t+i*.075,.3,'sine',volume*.72));}
    if(kind==='portal'){tone(240,t,.55,'triangle',volume,920);tone(510,t+.13,.50,'sine',volume*.7,270);}
    if(kind==='reset'){tone(560,t,.45,'triangle',volume,170);tone(230,t+.36,.19,'sine',volume*.7);}
    if(kind==='swap'){tone(440,t,.17,'triangle',volume);tone(660,t+.18,.17,'triangle',volume);tone(440,t+.39,.17,'triangle',volume);tone(880,t+.57,.30,'sine',volume);}
  }
  $('new-game').addEventListener('click',setupBuilder);
  $('back-home').addEventListener('click',()=>show('landing'));
  $('home-btn').addEventListener('click',()=>location.href='/');
  $('set-count').addEventListener('change',()=>{const n=Math.max(5,Math.min(48,Number($('set-count').value)||8));$('set-count').value=n;renderQuestions(n,collectQuestions());updateBoardRule();});
  $('set-teams').addEventListener('change',()=>{const n=Math.max(2,Math.min(8,Number($('set-teams').value)||3));$('set-teams').value=n;teamInputs(n,[...document.querySelectorAll('.team-name')].map(x=>x.value));});
  $('question-list').addEventListener('change',e=>{if(e.target.classList.contains('q-type-select'))e.target.closest('.question-card').dataset.type=e.target.value;});
  $('load-example').addEventListener('click',()=>{
    const n=Number($('set-count').value),old=collectQuestions();
    for(let i=0;i<Math.min(n,example.length);i++)old[i]={...example[i],type:'text',options:['','','','']};
    renderQuestions(n,old);
    message('예시 문제를 불러왔습니다. 교과에 맞게 수정해 주세요.');
  });
  $('add-special').addEventListener('click',()=>addSpecial());
  $('sound-volume').addEventListener('input',e=>$('sound-volume-value').textContent=e.target.value+'%');
  $('live-volume').addEventListener('input',e=>$('live-volume-value').textContent=e.target.value+'%');
  $('create-form').addEventListener('submit',createGame);
  $('join-form').addEventListener('submit',evt=>{evt.preventDefault();const code=$('join-code').value.trim().toUpperCase();if(!/^[A-Z2-9]{6}$/.test(code)){message('게임방 코드를 확인해 주세요.',true);return;}location.href='/?room='+code;});
  $('team-join-form').addEventListener('submit',async evt=>{
    evt.preventDefault();const id=Number($('choose-team').value),pin=$('team-pin').value.trim();
    try{await request('/api/rooms/'+roomCode+'/join',{teamId:id,pin});teamId=id;teamPin=pin;watchOnly=false;localStorage.setItem('boardTeam_'+roomCode,JSON.stringify({teamId,pin}));unlockAudio();renderRoom();message(state.teams[id].name+'으로 참가했습니다.');}
    catch(e){message(e.message,true);}
  });
  $('watch-only').addEventListener('click',()=>{watchOnly=true;renderJoin();});
  $('roll-button').addEventListener('click',()=>sendAction('roll'));
  $('start-game').addEventListener('click',()=>sendAction('start'));
  $('skip-turn').addEventListener('click',()=>{if(confirm('현재 팀의 차례를 건너뛸까요? 이동 전 위치로 돌아갑니다.'))sendAction('skip');});
  $('clone-game').addEventListener('click',async()=>{
    if(loading)return;
    if(!confirm('지금 설정한 문제·힌트·특별카드·팀 구성을 그대로 사용해 다른 반의 새 게임방을 만들까요?\n기존 반의 게임과 기록은 유지되고, 새 주소와 참가 코드가 발급됩니다.'))return;
    try{
      loading=true;
      $('clone-game').disabled=true;
      const result=await request('/api/rooms/'+roomCode+'/clone',{},teacherKey);
      localStorage.setItem('boardTeacher_'+result.code,result.teacherKey);
      location.href='/?room='+result.code+'&role=teacher&key='+encodeURIComponent(result.teacherKey);
    }catch(e){message(e.message,true);}
    finally{loading=false;$('clone-game').disabled=false;}
  });
  $('restart-game').addEventListener('click',()=>{
    if(confirm('현재 게임방의 모든 게임 기록을 삭제하고 처음부터 다시 시작할까요?\n문제·힌트·특별카드·팀 이름·학생용 주소와 참가 코드는 유지됩니다. 삭제된 기록은 복구할 수 없습니다.'))sendAction('restart');
  });
  $('save-sounds').addEventListener('click',()=>sendAction('sound',{sounds:soundFrom('live-sound-options','live-volume')}));
  $('copy-student').addEventListener('click',()=>copyText(roomStudentURL()));
  $('copy-display').addEventListener('click',()=>copyText(roomDisplayURL()));
  $('audio-unlock').addEventListener('click',()=>{if(audioContext&&!localMute){localMute=true;$('audio-unlock').textContent='♫ 소리 꺼짐';}else{unlockAudio();message('효과음 재생이 켜졌습니다.');}});
  async function copyText(txt){try{await navigator.clipboard.writeText(txt);message('주소를 복사했습니다.');}catch{window.prompt('주소를 복사해 주세요.',txt);}}
  if(URLparams.get('room'))openRoom(URLparams.get('room'));
  else show('landing');
})();
