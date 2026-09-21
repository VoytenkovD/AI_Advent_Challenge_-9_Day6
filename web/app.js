(function () {
  'use strict';
  var CATALOG = {};
  var PROFILE_PRESETS = [];
  var TASK_STAGES = [];
  var TASK_TRANSITIONS = {};
  var TASK_DESCRIPTIONS = {};
  var busy = false;

  var CHAT_LIST = []; // сводки из /api/chats: {id,name,topic,messageCount,updatedAt,taskState}
  var ACTIVE = null;  // полный объект активного чата (история, факты, память, профиль, config, taskState)

  var els = {};

  function el(tag, cls, txt) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (txt !== undefined) node.textContent = txt;
    return node;
  }

  function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

  function cacheElements() {
    var ids = ['note','history','input','send','sidebar','btnSettings','btnStats','btnClear',
      'btnStratBar','btnMemory','modalStats','btnCloseStats','selModel','preset','strategy','keeprecent',
      'sumevery','factsEvery','temp','topp','freq','pres','maxt','format','words','chars',
      'factsList','btnFactsUpdate','btnFactsClear','branchesList','btnBranchNew','btnBranchSwitch',
      'btnProfile','profilePanel','profIdentity','profStyle','profFormat','profConstraints','btnProfileSave',
      'profPresetSelect','btnPresetApply','btnPresetDelete','profPresetName','btnPresetSaveNew',
      'btnNewChat','chatsList','tsStage','tsStep','tsExpected',
      'tsSelect','tsTransitionBtn','tsHistoryToggle','tsHistory','tsError'];
    var map = {
      note:'app-note', history:'chat-history', input:'input', send:'send',
      sidebar:'sidebar', btnSettings:'btn-settings', btnStats:'btn-stats',
      btnClear:'btn-clear', btnStratBar:'btn-strat-bar', btnMemory:'btn-memory', modalStats:'modal-stats',
      btnCloseStats:'btn-stats-close', selModel:'set-model', preset:'set-preset',
      strategy:'set-strategy', keeprecent:'set-keeprecent', sumevery:'set-sumevery',
      factsEvery:'set-facts-every', temp:'set-temp', topp:'set-topp',
      freq:'set-freq', pres:'set-pres', maxt:'set-maxt', format:'set-format',
      words:'set-words', chars:'set-chars', factsList:'facts-list',
      btnFactsUpdate:'btn-facts-update', btnFactsClear:'btn-facts-clear',
      branchesList:'branches-list', btnBranchNew:'btn-branch-new',
      btnBranchSwitch:'btn-branch-switch',
      btnProfile:'btn-profile', profilePanel:'profile-panel', profIdentity:'prof-identity',
      profStyle:'prof-style', profFormat:'prof-format', profConstraints:'prof-constraints',
      btnProfileSave:'btn-profile-save',
      profPresetSelect:'prof-preset-select', btnPresetApply:'btn-preset-apply',
      btnPresetDelete:'btn-preset-delete', profPresetName:'prof-preset-name', btnPresetSaveNew:'btn-preset-save-new',
      btnNewChat:'btn-new-chat', chatsList:'chats-list',
      tsStage:'ts-stage', tsStep:'ts-step', tsExpected:'ts-expected',
      tsSelect:'ts-select', tsTransitionBtn:'ts-transition-btn', tsHistoryToggle:'ts-history-toggle',
      tsHistory:'ts-history', tsError:'ts-error'
    };
    ids.forEach(function(k){ els[k] = document.getElementById(map[k]) || {}; });
  }

  function getActiveHistory() {
    return ACTIVE.activeBranch ? (ACTIVE.branches[ACTIVE.activeBranch] || []) : ACTIVE.history;
  }

  // --- Работа с чатами на сервере ---
  function fetchChatsList() {
    return fetch('/api/chats').then(function(r){ return r.json(); }).then(function(data){
      CHAT_LIST = data.chats || [];
      return data;
    });
  }

  function fetchChat(id) {
    return fetch('/api/chats/' + id).then(function(r){ return r.json(); }).then(function(data){ return data.chat; });
  }

  function createChatOnServer(name) {
    return fetch('/api/chats', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: name || null })
    }).then(function(r){ return r.json(); }).then(function(data){ return data.chat; });
  }

  function setActiveChatOnServer(id) {
    fetch('/api/chats/' + id + '/active', { method: 'POST' }).catch(function(){});
  }

  function saveChatPatch(patch) {
    if (!ACTIVE) return;
    fetch('/api/chats/' + ACTIVE.id, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch)
    }).catch(function(){});
  }

  function deleteChatOnServer(id) {
    return fetch('/api/chats/' + id, { method: 'DELETE' }).then(function(r){ return r.json(); });
  }

  // --- Панель чатов ---
  function syncListEntryFromActive() {
    var entry = CHAT_LIST.filter(function(c){ return c.id === ACTIVE.id; })[0];
    if (!entry) {
      entry = { id: ACTIVE.id, name: ACTIVE.name, topic: ACTIVE.topic, messageCount: 0, updatedAt: Date.now()/1000, taskState: ACTIVE.taskState };
      CHAT_LIST.unshift(entry);
    }
    entry.name = ACTIVE.name;
    entry.topic = ACTIVE.topic;
    entry.messageCount = ACTIVE.history.length;
    entry.taskState = ACTIVE.taskState;
    entry.updatedAt = Date.now() / 1000;
  }

  function renderChatsList() {
    if (!els.chatsList) return;
    els.chatsList.innerHTML = '';
    CHAT_LIST.forEach(function(c){
      var item = el('div', 'chat-item' + (ACTIVE && c.id === ACTIVE.id ? ' active' : ''));
      var top = el('div', 'chat-item__top');
      top.appendChild(el('span', 'chat-item__name', c.name));
      var del = el('button', 'chat-item__del', '×');
      del.title = 'Удалить чат';
      del.addEventListener('click', function(ev){
        ev.stopPropagation();
        if (!confirm('Удалить чат «' + c.name + '»?')) return;
        deleteChatOnServer(c.id).then(function(data){
          fetchChatsList().then(function(){
            if (ACTIVE && data.activeChatId === ACTIVE.id) {
              renderChatsList();
            } else {
              switchToChat(data.activeChatId);
            }
          });
        });
      });
      top.appendChild(del);
      item.appendChild(top);
      item.appendChild(el('div', 'chat-item__topic', c.topic || 'Тема пока не определена'));
      var meta = el('div', 'chat-item__meta');
      meta.appendChild(el('span', null, c.messageCount + ' сообщ.'));
      meta.appendChild(el('span', 'chat-item__stage', (c.taskState && c.taskState.stage) || 'Ожидание задачи'));
      item.appendChild(meta);
      item.addEventListener('click', function(){ switchToChat(c.id); });
      els.chatsList.appendChild(item);
    });
  }

  function renderTaskStateBar() {
    if (!els.tsStage) return;
    var ts = (ACTIVE && ACTIVE.taskState) || { stage: 'Ожидание задачи', step: '', expectedAction: '' };
    els.tsStage.textContent = ts.stage || 'Ожидание задачи';
    els.tsStep.textContent = ts.step || '';
    els.tsExpected.textContent = ts.expectedAction || '';

    if (els.tsSelect) {
      var allowed = TASK_TRANSITIONS[ts.stage] || [];
      els.tsSelect.innerHTML = '';
      TASK_STAGES.forEach(function(s){
        var isAllowed = allowed.indexOf(s) !== -1 || s === ts.stage;
        var opt = el('option', null, (isAllowed ? s : ('🚫 ' + s)));
        opt.value = s;
        opt.setAttribute('data-allowed', isAllowed ? '1' : '0');
        els.tsSelect.appendChild(opt);
      });
      var firstAllowed = TASK_STAGES.filter(function(s){ return allowed.indexOf(s) !== -1; })[0];
      els.tsSelect.value = firstAllowed || ts.stage;
    }
    hideTsError();
    renderStageHistory();
  }

  function hideTsError() {
    if (els.tsError) { els.tsError.style.display = 'none'; els.tsError.textContent = ''; }
  }

  function showTsError(text) {
    if (!els.tsError) return;
    els.tsError.textContent = '✕ ' + text;
    els.tsError.style.display = 'block';
  }

  function renderStageHistory() {
    if (!els.tsHistory) return;
    var history = (ACTIVE && ACTIVE.stageHistory) || [];
    els.tsHistory.innerHTML = '';
    if (!history.length) {
      els.tsHistory.textContent = 'Переходов пока не было.';
      return;
    }
    history.slice().reverse().forEach(function(h){
      var line = el('div', 'task-state-bar__history-entry' + (h.ok ? '' : ' rejected'));
      var text = (h.source === 'manual' ? '[вручную] ' : '[авто] ') + h.from + ' → ' + h.attempted;
      if (!h.ok) text += '  ОТКЛОНЕНО: ' + h.reason;
      line.textContent = text;
      els.tsHistory.appendChild(line);
    });
  }

  function attemptTransition(target) {
    if (!ACTIVE || !target) return;
    fetch('/api/chats/' + ACTIVE.id + '/transition', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ to: target })
    }).then(function(r){ return r.json(); }).then(function(data){
      if (!data.chat) return;
      ACTIVE = data.chat;
      renderTaskStateBar();
      syncListEntryFromActive();
      renderChatsList();
      if (!data.ok) showTsError(data.message);
    });
  }

  function switchToChat(id) {
    if (!id) return;
    if (ACTIVE && ACTIVE.id === id) return;
    fetchChat(id).then(function(chat){
      if (!chat) return;
      ACTIVE = chat;
      setActiveChatOnServer(id);
      renderAllForActiveChat();
    });
  }

  function createNewChat() {
    createChatOnServer().then(function(chat){
      ACTIVE = chat;
      fetchChatsList().then(function(){
        syncListEntryFromActive();
        renderAllForActiveChat();
      });
    });
  }

  function renderAllForActiveChat() {
    renderHistory();
    renderTaskStateBar();
    if (els.tsHistory) els.tsHistory.style.display = 'none';
    renderChatsList();
    renderMemoryPanel();
    renderProfilePanel();
    if (els.profPresetSelect) { els.profPresetSelect.value = ''; updatePresetDeleteVisibility(); }
    loadSettingsFromActive();
    updateFactsUI();
    updateBranchUI();
    document.getElementById('mem-suggestions').innerHTML = '';
  }

  // --- Память (per-chat) ---
  function renderMemoryPanel() {
    var doc = document.getElementById('mem-short');
    if (doc) doc.textContent = 'Сообщений в диалоге: ' + (getActiveHistory().length || 0);

    var wDiv = document.getElementById('mem-working');
    if (wDiv) {
      wDiv.innerHTML = '';
      var wm = ACTIVE.memory.working || [];
      if (!wm.length) wDiv.textContent = '(пусто)';
      else wm.forEach(function(entry, i){
        var d = document.createElement('div');
        d.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:3px;';
        d.innerHTML = '<span style="color:#ffd426;">'+esc(entry.key)+'</span> = <span>'+esc(entry.value)+'</span> ' +
          '<button data-del="working:'+i+'" style="color:var(--err);background:none;border:none;cursor:pointer;font-size:11px;">×</button>';
        wDiv.appendChild(d);
      });
      wDiv.querySelectorAll('[data-del]').forEach(function(btn){
        btn.addEventListener('click', function(){
          var parts = this.getAttribute('data-del').split(':');
          var type = parts[0], idx = parseInt(parts[1]);
          ACTIVE.memory[type].splice(idx, 1);
          renderMemoryPanel(); saveChatPatch({ memory: ACTIVE.memory });
        });
      });
    }

    var lDiv = document.getElementById('mem-long');
    if (lDiv) {
      lDiv.innerHTML = '';
      var lm = ACTIVE.memory.long_term || [];
      if (!lm.length) lDiv.textContent = '(пусто)';
      else lm.forEach(function(entry, i){
        var d = document.createElement('div');
        d.style.cssText = 'display:flex;gap:6px;align-items:center;margin-bottom:3px;';
        d.innerHTML = '<span style="color:#3ddc84;">'+esc(entry.key)+'</span> = <span>'+esc(entry.value)+'</span> ' +
          '<button data-del="long_term:'+i+'" style="color:var(--err);background:none;border:none;cursor:pointer;font-size:11px;">×</button>';
        lDiv.appendChild(d);
      });
      lDiv.querySelectorAll('[data-del]').forEach(function(btn){
        btn.addEventListener('click', function(){
          var parts = this.getAttribute('data-del').split(':');
          var type = parts[0], idx = parseInt(parts[1]);
          ACTIVE.memory[type].splice(idx, 1);
          renderMemoryPanel(); saveChatPatch({ memory: ACTIVE.memory });
        });
      });
    }
  }

  function clearWorkingMemory() {
    ACTIVE.memory.working = [];
    renderMemoryPanel(); saveChatPatch({ memory: ACTIVE.memory });
  }

  function clearLongTermMemory() {
    ACTIVE.memory.long_term = [];
    renderMemoryPanel(); saveChatPatch({ memory: ACTIVE.memory });
  }

  // --- Профиль (per-chat) ---
  function renderProfilePanel() {
    if (!els.profIdentity) return;
    els.profIdentity.value = ACTIVE.profile.identity || '';
    els.profStyle.value = ACTIVE.profile.style || '';
    els.profFormat.value = ACTIVE.profile.format || '';
    els.profConstraints.value = ACTIVE.profile.constraints || '';
  }

  function renderPresetOptions() {
    if (!els.profPresetSelect) return;
    var current = els.profPresetSelect.value;
    els.profPresetSelect.innerHTML = '';
    els.profPresetSelect.appendChild(el('option', null, '— выбрать —'));
    els.profPresetSelect.options[0].value = '';
    PROFILE_PRESETS.forEach(function(p){
      var opt = el('option', null, p.name + (p.builtin ? '' : ' (свой)'));
      opt.value = p.id;
      els.profPresetSelect.appendChild(opt);
    });
    var stillThere = PROFILE_PRESETS.some(function(p){ return p.id === current; });
    els.profPresetSelect.value = stillThere ? current : '';
    updatePresetDeleteVisibility();
  }

  function updatePresetDeleteVisibility() {
    if (!els.btnPresetDelete) return;
    var preset = PROFILE_PRESETS.filter(function(p){ return p.id === els.profPresetSelect.value; })[0];
    els.btnPresetDelete.style.display = (preset && !preset.builtin) ? '' : 'none';
  }

  function showProfileNote(text) {
    var note = document.getElementById('profile-saved-note');
    if (!note) return;
    note.textContent = text;
    setTimeout(function(){ note.textContent = ''; }, 2000);
  }

  function saveProfileFromPanel() {
    ACTIVE.profile = {
      identity: els.profIdentity.value.trim(),
      style: els.profStyle.value.trim(),
      format: els.profFormat.value.trim(),
      constraints: els.profConstraints.value.trim()
    };
    saveChatPatch({ profile: ACTIVE.profile });
    var note = document.getElementById('profile-saved-note');
    if (note) { note.textContent = 'Сохранено'; setTimeout(function(){ note.textContent = ''; }, 1500); }
  }

  function renderHistory() {
    els.history.innerHTML = '';
    var h = getActiveHistory();
    h.forEach(function(msg){ appendMessage(msg.role, msg.content, msg.meta); });
    if (!h.length) {
      els.history.innerHTML = '<div class="msg-bot"><div class="md"><p>Новый чат. Опишите задачу.</p></div></div>';
    }
  }

  // --- UI ---
  function initUI() {
    els.btnSettings.addEventListener('click', function(){
      els.sidebar.style.display = els.sidebar.style.display === 'none' ? 'block' : 'none';
    });
    els.btnStats.addEventListener('click', renderStats);
    els.btnCloseStats.addEventListener('click', function(){ els.modalStats.style.display = 'none'; });

    els.btnClear.addEventListener('click', function(){
      ACTIVE.history = []; ACTIVE.facts = []; ACTIVE.factsUpTo = 0;
      ACTIVE.branches = {}; ACTIVE.activeBranch = null;
      ACTIVE.taskState = { stage: 'Ожидание задачи', step: '', expectedAction: '' };
      ACTIVE.stageHistory = [];
      ACTIVE.totalPrompt = 0; ACTIVE.totalComp = 0;
      els.history.innerHTML = '<div class="msg-bot"><div class="md"><p>Очищено.</p></div></div>';
      saveChatPatch({
        history: ACTIVE.history, facts: ACTIVE.facts, factsUpTo: ACTIVE.factsUpTo,
        branches: ACTIVE.branches, activeBranch: ACTIVE.activeBranch, taskState: ACTIVE.taskState,
        stageHistory: ACTIVE.stageHistory, totalPrompt: 0, totalComp: 0
      });
      renderTaskStateBar();
      syncListEntryFromActive(); renderChatsList();
      updateBranchUI(); updateFactsUI();
    });

    // Стратегия в шапке
    els.btnStratBar.addEventListener('click', function(){ els.sidebar.style.display = 'block';
      document.getElementById('set-strategy').focus(); });

    // Факты
    if (els.btnFactsUpdate && els.btnFactsUpdate.addEventListener) els.btnFactsUpdate.addEventListener('click', function(){ ACTIVE.factsUpTo = 0; });
    els.btnFactsClear.addEventListener('click', function(){ ACTIVE.facts = []; ACTIVE.factsUpTo = 0;
      updateFactsUI(); saveChatPatch({ facts: [], factsUpTo: 0 }); });

    // Ветки
    els.btnBranchNew.addEventListener('click', createBranch);
    els.btnBranchSwitch.addEventListener('click', function(){
      ACTIVE.activeBranch = null; renderHistory(); saveChatPatch({ activeBranch: null });
    });

    // Память
    els.btnMemory.addEventListener('click', function(){
      renderMemoryPanel();
      document.getElementById('modal-memory').style.display = 'flex';
    });
    document.getElementById('mem-add-working').addEventListener('click', function(){
      var k = document.getElementById('mem-new-key').value.trim();
      var v = document.getElementById('mem-new-val').value.trim();
      if (k && v) { ACTIVE.memory.working.push({key:k, value:v});
        document.getElementById('mem-new-key').value = '';
        document.getElementById('mem-new-val').value = '';
        renderMemoryPanel(); saveChatPatch({ memory: ACTIVE.memory }); }
    });
    document.getElementById('mem-add-long').addEventListener('click', function(){
      var k = document.getElementById('meml-new-key').value.trim();
      var v = document.getElementById('meml-new-val').value.trim();
      if (k && v) { ACTIVE.memory.long_term.push({key:k, value:v});
        document.getElementById('meml-new-key').value = '';
        document.getElementById('meml-new-val').value = '';
        renderMemoryPanel(); saveChatPatch({ memory: ACTIVE.memory }); }
    });
    document.getElementById('btn-work-clear').addEventListener('click', clearWorkingMemory);
    document.getElementById('btn-long-clear').addEventListener('click', clearLongTermMemory);
    document.getElementById('btn-memory-close').addEventListener('click', function(){
      document.getElementById('modal-memory').style.display = 'none';
    });

    // Профиль
    els.btnProfile.addEventListener('click', function(){
      els.profilePanel.style.display = els.profilePanel.style.display === 'none' ? 'block' : 'none';
    });
    els.btnProfileSave.addEventListener('click', saveProfileFromPanel);

    els.profPresetSelect.addEventListener('change', updatePresetDeleteVisibility);

    els.btnPresetApply.addEventListener('click', function(){
      var preset = PROFILE_PRESETS.filter(function(p){ return p.id === els.profPresetSelect.value; })[0];
      if (!preset) return;
      ACTIVE.profile = {
        identity: preset.profile.identity || '',
        style: preset.profile.style || '',
        format: preset.profile.format || '',
        constraints: preset.profile.constraints || ''
      };
      renderProfilePanel();
      saveChatPatch({ profile: ACTIVE.profile });
      showProfileNote('Пресет «' + preset.name + '» применён и сохранён');
    });

    els.btnPresetDelete.addEventListener('click', function(){
      var preset = PROFILE_PRESETS.filter(function(p){ return p.id === els.profPresetSelect.value; })[0];
      if (!preset || preset.builtin) return;
      if (!confirm('Удалить пресет «' + preset.name + '»?')) return;
      fetch('/api/profile-presets/' + preset.id, { method: 'DELETE' })
        .then(function(r){ return r.json(); })
        .then(function(data){
          PROFILE_PRESETS = data.presets || [];
          renderPresetOptions();
          showProfileNote('Пресет удалён');
        });
    });

    els.btnPresetSaveNew.addEventListener('click', function(){
      var name = els.profPresetName.value.trim();
      if (!name) { showProfileNote('Введите название пресета'); return; }
      var profile = {
        identity: els.profIdentity.value.trim(),
        style: els.profStyle.value.trim(),
        format: els.profFormat.value.trim(),
        constraints: els.profConstraints.value.trim()
      };
      fetch('/api/profile-presets', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: name, profile: profile })
      }).then(function(r){ return r.json(); }).then(function(data){
        if (!data.preset) { showProfileNote(data.error || 'Не удалось сохранить пресет'); return; }
        PROFILE_PRESETS = data.presets || PROFILE_PRESETS;
        renderPresetOptions();
        els.profPresetSelect.value = data.preset.id;
        updatePresetDeleteVisibility();
        els.profPresetName.value = '';
        showProfileNote('Пресет «' + data.preset.name + '» сохранён');
      });
    });

    // Чаты
    els.btnNewChat.addEventListener('click', createNewChat);

    // Переходы состояния задачи
    els.tsTransitionBtn.addEventListener('click', function(){
      attemptTransition(els.tsSelect.value);
    });
    els.tsHistoryToggle.addEventListener('click', function(){
      els.tsHistory.style.display = els.tsHistory.style.display === 'none' ? 'block' : 'none';
    });
  }

  function syncStrategyVisibility() {
    var s = els.strategy.value;
    document.getElementById('grp-keep').style.display = (s === 'compressed' || s === 'sliding' || s === 'facts') ? '' : 'none';
    document.getElementById('grp-every').style.display = s === 'compressed' ? '' : 'none';
    document.getElementById('grp-facts-every').style.display = s === 'facts' ? '' : 'none';
    document.getElementById('grp-facts').style.display = s === 'facts' ? '' : 'none';
    document.getElementById('grp-branches').style.display = s === 'branching' ? '' : 'none';

    ACTIVE.config.contextMode = s;
    ACTIVE.strategy = s;
    els.btnStratBar.textContent = s;
    saveChatPatch({ config: ACTIVE.config, strategy: s });
  }

  function updateBranchUI() {
    if (!els.branchesList) return;
    els.branchesList.innerHTML = '';
    var keys = Object.keys(ACTIVE.branches);
    if (!ACTIVE.activeBranch && keys.length === 0) {
      els.branchesList.textContent = 'Веток нет. Нажмите «Новая ветка».';
      els.btnBranchSwitch.textContent = 'Основная';
      return;
    }
    var active = (ACTIVE.activeBranch || 'main');
    if (ACTIVE.activeBranch) {
      els.branchesList.appendChild(el('div', null, 'Активна: ' + ACTIVE.activeBranch + ' (' + getActiveHistory().length + ' сообщ.)'));
      els.btnBranchSwitch.textContent = 'Основная';
    } else {
      els.branchesList.appendChild(el('div', null, 'Основная ветка (' + ACTIVE.history.length + ' сообщ.)'));
      els.btnBranchSwitch.textContent = 'К основной';
    }
    keys.forEach(function(id){
      if (id === active) return;
      var d = el('div', null, '' + id + ' (' + (ACTIVE.branches[id]||[]).length + ' сообщ.)');
      d.style.cursor = 'pointer'; d.style.color = 'var(--accent)';
      d.addEventListener('click', function(){
        ACTIVE.activeBranch = id; renderHistory(); saveChatPatch({ activeBranch: id });
      });
      els.branchesList.appendChild(d);
    });
  }

  function createBranch() {
    var name = prompt('Имя ветки (напр. «вариант А»):', 'branch-' + (Object.keys(ACTIVE.branches).length + 1));
    if (!name) return;
    var history = getActiveHistory();
    var checkpoint = history.length;
    ACTIVE.branches[name] = history.slice(0, checkpoint);
    ACTIVE.activeBranch = name;
    renderHistory(); saveChatPatch({ branches: ACTIVE.branches, activeBranch: ACTIVE.activeBranch });
  }

  function updateFactsUI() {
    if (!els.factsList) return;
    if (!ACTIVE.facts || ACTIVE.facts.length === 0) {
      els.factsList.textContent = 'Факты пока не извлечены.';
      return;
    }
    els.factsList.innerHTML = '';
    ACTIVE.facts.forEach(function(f){
      els.factsList.appendChild(el('div', null, f.key + ' = ' + f.value));
    });
  }

  function initSettings() {
    Object.keys(CATALOG).forEach(function(provider){
      var optgroup = el('optgroup');
      optgroup.label = provider;
      CATALOG[provider].forEach(function(m){
        var opt = el('option', null, m);
        opt.value = provider + ':' + m;
        optgroup.appendChild(opt);
      });
      els.selModel.appendChild(optgroup);
    });

    function saveSettings() {
      var p = els.selModel.value.split(':');
      ACTIVE.config.provider = p[0]; ACTIVE.config.model = p[1];
      ACTIVE.config.systemPromptPreset = els.preset.value;
      ACTIVE.config.temperature = parseFloat(els.temp.value);
      ACTIVE.config.topP = parseFloat(els.topp.value);
      ACTIVE.config.frequencyPenalty = parseFloat(els.freq.value);
      ACTIVE.config.presencePenalty = parseFloat(els.pres.value);
      ACTIVE.config.maxTokens = parseInt(els.maxt.value, 10);
      ACTIVE.config.responseFormat = els.format.value;
      ACTIVE.config.maxWords = parseInt(els.words.value, 10);
      ACTIVE.config.maxInputChars = parseInt(els.chars.value, 10);
      ACTIVE.config.contextMode = els.strategy.value;
      ACTIVE.config.keepRecent = parseInt(els.keeprecent.value, 10);
      ACTIVE.config.summarizeEvery = parseInt(els.sumevery.value, 10);
      ACTIVE.config.factsUpdateEvery = parseInt(els.factsEvery.value, 10);
      syncStrategyVisibility();

      document.getElementById('val-temp').textContent = ACTIVE.config.temperature;
      document.getElementById('val-topp').textContent = ACTIVE.config.topP;
      document.getElementById('val-freq').textContent = ACTIVE.config.frequencyPenalty;
      document.getElementById('val-pres').textContent = ACTIVE.config.presencePenalty;
    }

    els.selModel.addEventListener('change', saveSettings);
    ['preset','temp','topp','freq','pres','maxt','format','words','chars','strategy',
     'keeprecent','sumevery','factsEvery'].forEach(function(f){
      els[f].addEventListener('input', saveSettings);
    });
    els.strategy.addEventListener('change', saveSettings);
  }

  function loadSettingsFromActive() {
    var cfg = ACTIVE.config;
    var def = cfg.provider + ':' + cfg.model;
    var m = false;
    Array.from(els.selModel.options).forEach(function(o){ if (o.value === def) m = true; });
    if (!m && els.selModel.options.length) {
      var p = els.selModel.options[0].value.split(':');
      cfg.provider = p[0]; cfg.model = p[1];
    }
    els.selModel.value = cfg.provider + ':' + cfg.model;
    els.preset.value = cfg.systemPromptPreset;
    els.temp.value = cfg.temperature;
    els.topp.value = cfg.topP;
    els.freq.value = cfg.frequencyPenalty;
    els.pres.value = cfg.presencePenalty;
    els.maxt.value = cfg.maxTokens;
    els.format.value = cfg.responseFormat;
    els.words.value = cfg.maxWords;
    els.chars.value = cfg.maxInputChars;
    els.strategy.value = cfg.contextMode;
    els.keeprecent.value = cfg.keepRecent;
    els.sumevery.value = cfg.summarizeEvery;
    els.factsEvery.value = cfg.factsUpdateEvery || 1;
    syncStrategyVisibility();

    document.getElementById('val-temp').textContent = cfg.temperature;
    document.getElementById('val-topp').textContent = cfg.topP;
    document.getElementById('val-freq').textContent = cfg.frequencyPenalty;
    document.getElementById('val-pres').textContent = cfg.presencePenalty;
  }

  function appendMessage(role, content, metaData) {
    metaData = metaData || null;
    var msgDiv = el('div', role === 'user' ? 'msg-user' : 'msg-bot');
    if (role === 'user') { msgDiv.textContent = content; }
    else {
      if (metaData && metaData.status === 'error') msgDiv.appendChild(el('div', 'answer--empty', content));
      else {
        msgDiv.appendChild(window.MD.render(content));
        if (metaData) {
          var m = el('div', 'meta');
          if (metaData.latency_ms) m.appendChild(el('span', null, (metaData.latency_ms / 1000).toFixed(1) + ' с'));
          var u = metaData.usage || {};
          if (metaData.usage) m.appendChild(el('span', null, (u.total_tokens || 0) + ' tok'));
          if (metaData.facts && metaData.facts.length) {
            m.appendChild(el('span', 'meta__cmp', '+facts: ' + metaData.facts.length));
          }
          if (metaData.taskStateDisplay && metaData.taskStateDisplay.stage) {
            m.appendChild(el('span', 'meta__stage', metaData.taskStateDisplay.stage));
          }
          msgDiv.appendChild(m);
          if (metaData.transitionRejected) {
            var tr = metaData.transitionRejected;
            var warn = el('div', 'answer--empty', '⚠️ Переход «' + tr.from + '» → «' + tr.attempted +
              '» отклонён: ' + tr.reason);
            warn.style.marginTop = '6px';
            warn.style.fontSize = '11.5px';
            msgDiv.appendChild(warn);
          }
        }
      }
    }
    els.history.appendChild(msgDiv);
    els.history.scrollTop = els.history.scrollHeight;
  }

  function renderStats() {
    document.getElementById('t-p').textContent = ACTIVE.totalPrompt || 0;
    document.getElementById('t-c').textContent = ACTIVE.totalComp || 0;
    document.getElementById('t-t').textContent = (ACTIVE.totalPrompt || 0) + (ACTIVE.totalComp || 0);
    els.modalStats.style.display = 'flex';
  }

  function setBusy(v) {
    busy = v;
    els.input.disabled = v;
    els.send.disabled = v || !els.input.value.trim();
    els.send.textContent = v ? 'Работаю...' : 'Отправить';
  }

  function send() {
    var question = els.input.value.trim();
    if (!question || busy || !ACTIVE) return;
    els.input.value = '';
    setBusy(true);
    appendMessage('user', question);

    var loadDiv = el('div', 'msg-bot'); loadDiv.id = 'load-indicator';
    var span = el('span', 'muted', 'думаю'); span.appendChild(el('span', 'cursor'));
    loadDiv.appendChild(span); els.history.appendChild(loadDiv);
    els.history.scrollTop = els.history.scrollHeight;

    var history = getActiveHistory();
    var reqData = { question: question, agent: {
      config: ACTIVE.config, history: history,
      facts: ACTIVE.facts, factsUpTo: ACTIVE.factsUpTo,
      branches: ACTIVE.branches, activeBranch: ACTIVE.activeBranch,
      memory: ACTIVE.memory, profile: ACTIVE.profile,
      taskState: ACTIVE.taskState, topic: ACTIVE.topic
    }};

    fetch('/api/run', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqData)
    }).then(function(r){ return r.json(); }).then(function(data){
      var loadNode = document.getElementById('load-indicator');
      if (loadNode) loadNode.remove();
      var res = data.result;

      if (res && res.status === 'done') {
        history.push({ role: 'user', content: question });
        history.push({ role: 'assistant', content: res.text, meta: res });

        var u = res.usage || {};
        ACTIVE.totalPrompt = (ACTIVE.totalPrompt || 0) + (u.prompt_tokens || 0);
        ACTIVE.totalComp = (ACTIVE.totalComp || 0) + (u.completion_tokens || 0);

        if (res.facts) { ACTIVE.facts = res.facts; ACTIVE.factsUpTo = res.factsUpTo; }
        else { ACTIVE.factsUpTo = history.length; }

        if (res.taskState) { ACTIVE.taskState = res.taskState; }
        if (res.topic) { ACTIVE.topic = res.topic; }
        if (res.stageHistoryEntry) {
          ACTIVE.stageHistory = (ACTIVE.stageHistory || []).concat([res.stageHistoryEntry]);
        }

        // Предложения для памяти
        if (res.memory_suggestions) {
          var sugg = res.memory_suggestions;
          var div = document.getElementById('mem-suggestions');
          if (div) {
            div.innerHTML = '<h4 style="color:var(--accent);margin:0 0 6px;">Предложения памяти</h4>';
            if ((sugg.working||[]).length + (sugg.long_term||[]).length === 0) {
              div.innerHTML += '<span style="font-size:12px;color:var(--muted);">Нет новых предложений.</span>';
            } else {
              if (sugg.working && sugg.working.length) {
                div.innerHTML += '<div style="font-size:12px;margin-bottom:4px;"><b>Рабочая:</b><br>' +
                  sugg.working.map(function(e){return esc(e.key)+' = '+esc(e.value)}).join('<br>') + '</div>';
              }
              if (sugg.long_term && sugg.long_term.length) {
                div.innerHTML += '<div style="font-size:12px;margin-bottom:4px;"><b>Долговременная:</b><br>' +
                  sugg.long_term.map(function(e){return esc(e.key)+' = '+esc(e.value)}).join('<br>') + '</div>';
              }
              div.innerHTML += '<div style="display:flex;gap:6px;margin-top:6px;">' +
                '<button id="btn-accept-mem" class="btn-outline" style="flex:1;font-size:11px;">Принять все</button>' +
                '<button id="btn-reject-mem" class="btn-outline" style="flex:1;font-size:11px;">Отклонить</button></div>';
              document.getElementById('btn-accept-mem').addEventListener('click', function(){
                if (sugg.working) sugg.working.forEach(function(e){ ACTIVE.memory.working.push(e); });
                if (sugg.long_term) sugg.long_term.forEach(function(e){ ACTIVE.memory.long_term.push(e); });
                renderMemoryPanel(); saveChatPatch({ memory: ACTIVE.memory });
                div.innerHTML = '<span style="font-size:12px;color:#3ddc84;">Принято.</span>';
              });
              document.getElementById('btn-reject-mem').addEventListener('click', function(){
                div.innerHTML = '<span style="font-size:12px;color:var(--muted);">Отклонено.</span>';
              });
            }
          }
        }

        appendMessage('assistant', res.text, res);
        updateFactsUI();
        updateBranchUI();
        renderTaskStateBar();
        syncListEntryFromActive();
        renderChatsList();
        saveChatPatch({
          history: ACTIVE.history, facts: ACTIVE.facts, factsUpTo: ACTIVE.factsUpTo,
          branches: ACTIVE.branches, activeBranch: ACTIVE.activeBranch,
          taskState: ACTIVE.taskState, topic: ACTIVE.topic, stageHistory: ACTIVE.stageHistory,
          totalPrompt: ACTIVE.totalPrompt, totalComp: ACTIVE.totalComp
        });
      } else {
        appendMessage('assistant', res ? res.error : 'Ошибка', { status: 'error' });
      }
    }).catch(function(e){
      var ln = document.getElementById('load-indicator'); if (ln) ln.remove();
      appendMessage('assistant', String(e.message || e), { status: 'error' });
    }).finally(function(){ setBusy(false); });
  }

  function boot() {
    cacheElements();
    els.send.addEventListener('click', send);
    els.input.addEventListener('input', function(){ els.send.disabled = busy || !els.input.value.trim(); });
    els.input.addEventListener('keydown', function(e){
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });
    fetch('/api/config').then(function(r){ return r.json(); }).then(function(config){
      CATALOG = config.catalog || {};
      initUI();
      initSettings();
      return fetch('/api/profile-presets').then(function(r){ return r.json(); });
    }).then(function(data){
      PROFILE_PRESETS = data.presets || [];
      renderPresetOptions();
      return fetch('/api/task-states').then(function(r){ return r.json(); });
    }).then(function(data){
      TASK_STAGES = data.stages || [];
      TASK_TRANSITIONS = data.transitions || {};
      TASK_DESCRIPTIONS = data.descriptions || {};
      return fetchChatsList();
    }).then(function(data){
      var activeId = data.activeChatId || (CHAT_LIST[0] && CHAT_LIST[0].id);
      return fetchChat(activeId);
    }).then(function(chat){
      ACTIVE = chat;
      renderAllForActiveChat();
      els.note.textContent = 'готово';
    }).catch(function(e){ els.note.textContent = 'Ошибка: ' + e.message; });
  }
  boot();
})();
