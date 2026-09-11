(function () {
  'use strict';
  var CATALOG = {};
  var busy = false;

  var AGENT = {
    history: [],
    summary: '',
    summaryUpTo: 0,
    total_prompt: 0,
    total_comp: 0,
    total_summ_prompt: 0,
    total_summ_comp: 0,
    lastComparison: null,
    config: {
      provider: 'ai-public',
      model: 'deepseek-ai/deepseek-v4-pro-0813',
      systemPromptPreset: 'assistant',
      temperature: 0.7,
      topP: 1.0,
      frequencyPenalty: 0.0,
      presencePenalty: 0.0,
      maxTokens: 4000,
      responseFormat: 'text',
      maxWords: 0,
      historyDepth: 5,
      maxInputChars: 2000,
      contextMode: 'compressed',
      keepRecent: 6,
      summarizeEvery: 10
    }
  };

  var els = {};

  function el(tag, cls, txt) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (txt !== undefined) node.textContent = txt;
    return node;
  }

  function cacheElements() {
    var ids = ['note', 'history', 'input', 'send', 'sidebar', 'btnSettings', 'btnStats',
      'btnClear', 'btnSummary', 'modalStats', 'btnCloseStats', 'modalSummary',
      'btnSummaryClose', 'btnSummaryReset', 'summaryText', 'summaryUpTo',
      'selModel', 'preset', 'temp', 'topp', 'freq', 'pres', 'maxt', 'format',
      'words', 'hist', 'chars', 'ctxmode', 'keeprecent', 'sumevery'];
    var map = {
      note: 'app-note', history: 'chat-history', input: 'input', send: 'send',
      sidebar: 'sidebar', btnSettings: 'btn-settings', btnStats: 'btn-stats',
      btnClear: 'btn-clear', btnSummary: 'btn-summary', modalStats: 'modal-stats',
      btnCloseStats: 'btn-stats-close', modalSummary: 'modal-summary',
      btnSummaryClose: 'btn-summary-close', btnSummaryReset: 'btn-summary-reset',
      summaryText: 'summary-text', summaryUpTo: 'summary-up-to',
      selModel: 'set-model', preset: 'set-preset', temp: 'set-temp', topp: 'set-topp',
      freq: 'set-freq', pres: 'set-pres', maxt: 'set-maxt', format: 'set-format',
      words: 'set-words', hist: 'set-hist', chars: 'set-chars',
      ctxmode: 'set-ctxmode', keeprecent: 'set-keeprecent', sumevery: 'set-sumevery'
    };
    ids.forEach(function (k) { els[k] = document.getElementById(map[k] || k); });
  }

  // --- Сохранение/загрузка состояния ---
  function saveStateToServer() {
    fetch('/api/history', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        history: AGENT.history,
        summary: AGENT.summary,
        summaryUpTo: AGENT.summaryUpTo
      })
    }).catch(function () {});
  }

  function resetSummaryOnServer() {
    fetch('/api/history/summary', { method: 'POST' }).catch(function () {});
  }

  function clearHistoryOnServer() {
    fetch('/api/history', { method: 'DELETE' }).catch(function () {});
  }

  function loadStateFromServer() {
    return fetch('/api/history')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (Array.isArray(data.history)) {
          AGENT.history = data.history;
          AGENT.summary = data.summary || '';
          AGENT.summaryUpTo = parseInt(data.summaryUpTo || 0, 10);
          els.history.innerHTML = '';
          AGENT.history.forEach(function (msg) { appendMessage(msg.role, msg.content); });
        }
      })
      .catch(function () {});
  }

  // --- UI ---
  function initUI() {
    els.btnSettings.addEventListener('click', function () {
      els.sidebar.style.display = els.sidebar.style.display === 'none' ? 'block' : 'none';
    });
    els.btnStats.addEventListener('click', function () { renderStats(); els.modalStats.style.display = 'flex'; });
    els.btnCloseStats.addEventListener('click', function () { els.modalStats.style.display = 'none'; });

    els.btnSummary.addEventListener('click', function () {
      els.summaryText.textContent = AGENT.summary || 'Резюме пока не создано.';
      els.summaryText.style.color = AGENT.summary ? 'var(--text)' : 'var(--muted)';
      els.summaryUpTo.textContent = AGENT.summaryUpTo;
      els.modalSummary.style.display = 'flex';
    });
    els.btnSummaryClose.addEventListener('click', function () { els.modalSummary.style.display = 'none'; });
    els.btnSummaryReset.addEventListener('click', function () {
      AGENT.summary = '';
      AGENT.summaryUpTo = 0;
      resetSummaryOnServer();
      els.summaryText.textContent = 'Резюме пока не создано.';
      els.summaryText.style.color = 'var(--muted)';
      els.summaryUpTo.textContent = '0';
    });

    els.btnClear.addEventListener('click', function () {
      AGENT.history = [];
      AGENT.summary = '';
      AGENT.summaryUpTo = 0;
      AGENT.total_prompt = 0;
      AGENT.total_comp = 0;
      AGENT.total_summ_prompt = 0;
      AGENT.total_summ_comp = 0;
      els.history.innerHTML = '<div class="msg-bot"><div class="md"><p>История очищена.</p></div></div>';
      clearHistoryOnServer();
    });
  }

  function initSettings() {
    Object.keys(CATALOG).forEach(function (provider) {
      var optgroup = el('optgroup');
      optgroup.label = provider;
      CATALOG[provider].forEach(function (m) {
        var opt = el('option', null, m);
        opt.value = provider + ':' + m;
        optgroup.appendChild(opt);
      });
      els.selModel.appendChild(optgroup);
    });

    function syncCompressionVisibility() {
      var mode = els.ctxmode.value;
      document.getElementById('grp-keep').classList.toggle('hidden', mode === 'full');
      document.getElementById('grp-every').classList.toggle('hidden', mode === 'full');
    }

    function loadSettings() {
      var def = AGENT.config.provider + ':' + AGENT.config.model;
      var match = false;
      Array.from(els.selModel.options).forEach(function (o) { if (o.value === def) match = true; });
      if (!match && els.selModel.options.length) {
        var parts = els.selModel.options[0].value.split(':');
        AGENT.config.provider = parts[0];
        AGENT.config.model = parts[1];
      }
      els.selModel.value = AGENT.config.provider + ':' + AGENT.config.model;
      els.preset.value = AGENT.config.systemPromptPreset;
      els.temp.value = AGENT.config.temperature;
      els.topp.value = AGENT.config.topP;
      els.freq.value = AGENT.config.frequencyPenalty;
      els.pres.value = AGENT.config.presencePenalty;
      els.maxt.value = AGENT.config.maxTokens;
      els.format.value = AGENT.config.responseFormat;
      els.words.value = AGENT.config.maxWords;
      els.hist.value = AGENT.config.historyDepth;
      els.chars.value = AGENT.config.maxInputChars;
      els.ctxmode.value = AGENT.config.contextMode;
      els.keeprecent.value = AGENT.config.keepRecent;
      els.sumevery.value = AGENT.config.summarizeEvery;

      document.getElementById('val-temp').textContent = AGENT.config.temperature;
      document.getElementById('val-topp').textContent = AGENT.config.topP;
      document.getElementById('val-freq').textContent = AGENT.config.frequencyPenalty;
      document.getElementById('val-pres').textContent = AGENT.config.presencePenalty;
      syncCompressionVisibility();
    }

    function saveSettings() {
      var parts = els.selModel.value.split(':');
      AGENT.config.provider = parts[0];
      AGENT.config.model = parts[1];
      AGENT.config.systemPromptPreset = els.preset.value;
      AGENT.config.temperature = parseFloat(els.temp.value);
      AGENT.config.topP = parseFloat(els.topp.value);
      AGENT.config.frequencyPenalty = parseFloat(els.freq.value);
      AGENT.config.presencePenalty = parseFloat(els.pres.value);
      AGENT.config.maxTokens = parseInt(els.maxt.value, 10);
      AGENT.config.responseFormat = els.format.value;
      AGENT.config.maxWords = parseInt(els.words.value, 10);
      AGENT.config.historyDepth = parseInt(els.hist.value, 10);
      AGENT.config.maxInputChars = parseInt(els.chars.value, 10);
      AGENT.config.contextMode = els.ctxmode.value;
      AGENT.config.keepRecent = parseInt(els.keeprecent.value, 10);
      AGENT.config.summarizeEvery = parseInt(els.sumevery.value, 10);

      document.getElementById('val-temp').textContent = AGENT.config.temperature;
      document.getElementById('val-topp').textContent = AGENT.config.topP;
      document.getElementById('val-freq').textContent = AGENT.config.frequencyPenalty;
      document.getElementById('val-pres').textContent = AGENT.config.presencePenalty;
      syncCompressionVisibility();
    }

    els.selModel.addEventListener('change', saveSettings);
    ['preset', 'temp', 'topp', 'freq', 'pres', 'maxt', 'format', 'words', 'hist', 'chars',
     'ctxmode', 'keeprecent', 'sumevery'].forEach(function (f) {
      els[f].addEventListener('input', saveSettings);
    });
    els.ctxmode.addEventListener('change', saveSettings);
    loadSettings();
  }

  function renderStats() {
    document.getElementById('t-p').textContent = AGENT.total_prompt;
    document.getElementById('t-c').textContent = AGENT.total_comp;
    document.getElementById('t-t').textContent = AGENT.total_prompt + AGENT.total_comp;
    document.getElementById('t-sp').textContent = AGENT.total_summ_prompt;
    document.getElementById('t-sc').textContent = AGENT.total_summ_comp;
    document.getElementById('t-st').textContent = AGENT.total_summ_prompt + AGENT.total_summ_comp;
    document.getElementById('t-ap').textContent = AGENT.total_prompt + AGENT.total_summ_prompt;
    document.getElementById('t-ac').textContent = AGENT.total_comp + AGENT.total_summ_comp;
    document.getElementById('t-at').textContent =
      AGENT.total_prompt + AGENT.total_comp + AGENT.total_summ_prompt + AGENT.total_summ_comp;

    var saveEl = document.getElementById('stats-saving');
    if (AGENT.lastComparison && AGENT.lastComparison.savedTokens > 0) {
      saveEl.style.display = 'block';
      saveEl.textContent = 'Экономия от сжатия: ~' +
        AGENT.lastComparison.savedTokens + ' токенов (-' +
        AGENT.lastComparison.savedPercent + '%) на последнем запросе';
    } else {
      saveEl.style.display = 'none';
    }
  }

  function appendMessage(role, content, metaData) {
    metaData = metaData || null;
    var msgDiv = el('div', role === 'user' ? 'msg-user' : 'msg-bot');

    if (role === 'user') {
      msgDiv.textContent = content;
    } else {
      if (metaData && metaData.status === 'error') {
        msgDiv.appendChild(el('div', 'answer--empty', content));
      } else {
        msgDiv.appendChild(window.MD.render(content));
        if (metaData) {
          var m = el('div', 'meta');
          m.appendChild(el('span', null, (metaData.latency_ms / 1000).toFixed(1) + ' с'));
          var u = metaData.usage || {};
          m.appendChild(el('span', null, (u.total_tokens || 0) + ' tok'));
          if (metaData.finish_reason && metaData.finish_reason !== 'stop') {
            m.appendChild(el('span', 'meta__warn', 'finish: ' + metaData.finish_reason));
          }
          if (metaData.comparison && metaData.comparison.mode === 'compressed') {
            var cmp = metaData.comparison;
            m.appendChild(el('span', 'meta__cmp',
              'сжатие: ' + cmp.messagesSent + '/' + cmp.messagesTotal + ' сообщ.'));
            if (cmp.savedTokens > 0) {
              m.appendChild(el('span', 'meta__save', '-' + cmp.savedTokens + ' tok (-' + cmp.savedPercent + '%)'));
            }
          }
          if (metaData.summarized) {
            m.appendChild(el('span', 'meta__cmp', 'резюме обновлено'));
          }
          msgDiv.appendChild(m);
        }
      }
    }
    els.history.appendChild(msgDiv);
    els.history.scrollTop = els.history.scrollHeight;
    return msgDiv;
  }

  function setBusy(val) {
    busy = val;
    els.input.disabled = val;
    els.send.disabled = val || !els.input.value.trim();
    els.send.textContent = val ? 'Работаю...' : 'Отправить';
  }

  function send() {
    var question = els.input.value.trim();
    if (!question || busy) return;

    els.input.value = '';
    setBusy(true);

    appendMessage('user', question);

    var loadDiv = el('div', 'msg-bot');
    loadDiv.id = 'load-indicator';
    var span = el('span', 'muted', 'думаю');
    span.appendChild(el('span', 'cursor'));
    loadDiv.appendChild(span);
    els.history.appendChild(loadDiv);
    els.history.scrollTop = els.history.scrollHeight;

    var reqData = {
      question: question,
      agent: {
        config: AGENT.config,
        history: AGENT.history,
        summary: AGENT.summary,
        summaryUpTo: AGENT.summaryUpTo
      }
    };

    fetch('/api/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(reqData)
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        var loadNode = document.getElementById('load-indicator');
        if (loadNode) loadNode.remove();

        var res = data.result;
        if (res && res.status === 'done') {
          AGENT.history.push({ role: 'user', content: question });
          AGENT.history.push({ role: 'assistant', content: res.text });

          var u = res.usage || {};
          AGENT.total_prompt += u.prompt_tokens || 0;
          AGENT.total_comp += u.completion_tokens || 0;

          if (res.summary) {
            AGENT.summary = res.summary;
            AGENT.summaryUpTo = res.summaryUpTo;
          }
          if (res.comparison) {
            AGENT.lastComparison = res.comparison;
            if (res.comparison.summaryTokensThisTurn > 0) {
              AGENT.total_summ_prompt += res.comparison.summaryTokensThisTurn;
            }
          }

          appendMessage('assistant', res.text, res);
          saveStateToServer();
        } else {
          appendMessage('assistant', res ? res.error : 'Ошибка связи', { status: 'error' });
        }
      })
      .catch(function (e) {
        var loadNode = document.getElementById('load-indicator');
        if (loadNode) loadNode.remove();
        appendMessage('assistant', String(e.message || e), { status: 'error' });
      })
      .finally(function () { setBusy(false); });
  }

  els.send.addEventListener ? null : null;

  function boot() {
    cacheElements();
    els.send.addEventListener('click', send);
    els.input.addEventListener('input', function () {
      els.send.disabled = busy || !els.input.value.trim();
    });
    els.input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    });

    fetch('/api/config').then(function (r) { return r.json(); }).then(function (config) {
      CATALOG = config.catalog || {};
      initUI();
      initSettings();
      loadStateFromServer().then(function () {
        els.note.textContent = 'настройки загружены';
      });
    }).catch(function (e) {
      els.note.textContent = 'Ошибка загрузки конфигурации: ' + e.message;
    });
  }

  boot();
})();