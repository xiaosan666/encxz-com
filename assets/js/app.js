/* ============================================================
   英语初学者 · 情景听说库 —— 前端逻辑
   数据源：data/en.xlsx（浏览器端用 SheetJS 解析，无需后端）
   音频：data/<mp3>
   视频：output/<同名>.mp4（可能尚未生成，需容错）
   新增课时只需在 xlsx 追加行，刷新页面即生效。
   ============================================================ */
(function () {
  'use strict';

  /* ---------- 常量 ---------- */
  var XLSX_URL = 'data/en.xlsx';
  var MP3_DIR = 'data/';
  var MP4_DIR = 'output/';
  var FAV_KEY = 'encxz.favorites.v1';

  /* ---------- 状态 ---------- */
  var state = {
    items: [],          // 全部课时
    levels: [],         // 动态推导出的级别
    query: '',
    level: 'all',
    favOnly: false,
    favs: new Set(),        // 收藏的 id
    missingVideo: new Set(),// 已确认 404 的视频
    presentVideo: new Set(),// 已确认存在的视频
    probedIds: new Set(),   // 已发起过探测的 id（避免重复请求）
    openId: null,
    tab: 'audio',
    lastFocus: null
  };

  var cardEls = new Map();  // id -> { tag, videoBtn }

  /* 视频可用性探测：滚动到可见才发请求，避免一次性打 200 个 404 */
  var videoObserver = null;
  var videoQueue = [];
  var probeActive = 0;
  var PROBE_CONCURRENCY = 4;

  /* ---------- DOM 引用 ---------- */
  var $ = function (id) { return document.getElementById(id); };
  var grid = $('grid');
  var skeleton = $('skeleton');
  var emptyState = $('emptyState');
  var errorState = $('errorState');
  var levelChips = $('levelChips');
  var searchInput = $('searchInput');
  var searchClear = $('searchClear');
  var favBtn = $('favBtn');
  var favBadge = $('favBadge');
  var resultCount = $('resultCount');
  var resetBtn = $('resetBtn');
  var sheet = $('sheet');
  var backdrop = $('backdrop');
  var sheetScroll = sheet.querySelector('.sheet-scroll');
  var mediaPanel = $('mediaPanel');
  var linesEl = $('lines');
  var toastEl = $('toast');

  /* ============================================================
     工具函数
     ============================================================ */
  function debounce(fn, wait) {
    var t;
    return function () {
      var args = arguments, self = this;
      clearTimeout(t);
      t = setTimeout(function () { fn.apply(self, args); }, wait);
    };
  }

  /** 拼 URL 并正确编码（文件名含空格等字符） */
  function encodeUrl(dir, filename) {
    return encodeURI(dir + filename);
  }
  function mp3Url(item) { return encodeUrl(MP3_DIR, item.mp3); }
  function mp4Url(item) { return encodeUrl(MP4_DIR, item.id.replace(/\.mp3$/i, '') + '.mp4'); }

  var toastTimer;
  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    requestAnimationFrame(function () { toastEl.classList.add('is-open'); });
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.classList.remove('is-open');
      setTimeout(function () { toastEl.hidden = true; }, 240);
    }, 2400);
  }

  /** 安全高亮：全部走 textContent，杜绝 xlsx 内容注入 HTML */
  function highlight(text, query) {
    var frag = document.createDocumentFragment();
    if (!query) { frag.appendChild(document.createTextNode(text)); return frag; }
    var lower = text.toLowerCase();
    var q = query.toLowerCase();
    var from = 0, idx;
    while ((idx = lower.indexOf(q, from)) !== -1) {
      if (idx > from) frag.appendChild(document.createTextNode(text.slice(from, idx)));
      var mark = document.createElement('mark');
      mark.textContent = text.slice(idx, idx + q.length);
      frag.appendChild(mark);
      from = idx + q.length;
    }
    frag.appendChild(document.createTextNode(text.slice(from)));
    return frag;
  }

  function splitLines(text) {
    return String(text || '')
      .split(/\r?\n/)
      .map(function (s) { return s.trim(); })
      .filter(function (s) { return s.length > 0; });
  }

  function pad3(n) {
    var s = String(n);
    while (s.length < 3) s = '0' + s;
    return s;
  }

  function levelClass(level) {
    return 'level-' + String(level || '').toLowerCase().replace(/[^a-z0-9]/g, '');
  }

  /* ============================================================
     收藏（localStorage）
     ============================================================ */
  function loadFavs() {
    try {
      var raw = localStorage.getItem(FAV_KEY);
      if (!raw) return;
      var arr = JSON.parse(raw);
      if (Array.isArray(arr)) state.favs = new Set(arr);
    } catch (e) { /* 隐私模式 / 数据损坏：静默降级为无收藏 */ }
  }

  function saveFavs() {
    try {
      localStorage.setItem(FAV_KEY, JSON.stringify(Array.from(state.favs)));
    } catch (e) { /* 存储不可用时不影响浏览 */ }
  }

  function isFav(id) { return state.favs.has(id); }

  function updateFavBadge() {
    var n = state.favs.size;
    favBadge.textContent = n;
    favBadge.hidden = n === 0;
    var label = n > 0 ? '我的收藏（' + n + ' 节）' : '我的收藏';
    favBtn.setAttribute('aria-label', label);
  }

  function toggleFav(id) {
    var added = !state.favs.has(id);
    if (added) state.favs.add(id); else state.favs.delete(id);
    saveFavs();
    updateFavBadge();

    // 卡片上的星标
    var refs = cardEls.get(id);
    if (refs && refs.star) {
      refs.star.setAttribute('aria-pressed', String(added));
      refs.star.setAttribute('aria-label', (added ? '取消收藏：' : '收藏：') + refs.title);
      if (added) {
        refs.star.classList.remove('is-pop');
        void refs.star.offsetWidth;   // 重启动画
        refs.star.classList.add('is-pop');
      }
    }
    // 弹层星标
    if (state.openId === id) syncSheetFav();

    // 收藏视图下取消收藏需即时移除卡片
    if (state.favOnly) render();

    return added;
  }

  function syncSheetFav() {
    var btn = $('sheetFav');
    var on = isFav(state.openId);
    btn.setAttribute('aria-pressed', String(on));
    $('sheetFavText').textContent = on ? '已收藏' : '收藏';
    btn.setAttribute('aria-label', on ? '取消收藏这节课' : '收藏这节课');
  }

  /* ============================================================
     加载并解析 xlsx
     ============================================================ */
  /** 按表头名定位列，列顺序/列名微调都能兼容 */
  var HEADER_RULES = [
    ['level',    [/^level\b/i, /级别/]],
    ['mp3',      [/mp3/i, /音频/, /^audio\b/i]],
    ['titleCn',  [/title\s*中/i, /中文标题/, /标题\s*中/]],
    ['scriptCn', [/script\s*中/i, /中文.*script/, /^中文$/, /中文文稿/]],
    ['titleEn',  [/^title\b/i, /title\s*英/i, /^标题/, /英文标题/]],
    ['scriptEn', [/script\s*英/i, /英文.*script/, /^英文$/, /英文文稿/]]
  ];

  function buildColumnMap(headerRow) {
    var map = {};
    var used = {};
    HEADER_RULES.forEach(function (rule) {
      var key = rule[0], patterns = rule[1];
      for (var c = 0; c < headerRow.length; c++) {
        if (used[c]) continue;
        var cell = String(headerRow[c] == null ? '' : headerRow[c]).trim();
        if (!cell) continue;
        for (var p = 0; p < patterns.length; p++) {
          if (patterns[p].test(cell)) { map[key] = c; used[c] = true; return; }
        }
      }
    });
    return map;
  }

  function findHeaderRow(rows) {
    var limit = Math.min(rows.length, 10);
    var best = { index: -1, score: -1 };
    for (var r = 0; r < limit; r++) {
      var row = rows[r] || [];
      var map = buildColumnMap(row);
      var score = Object.keys(map).length;
      if (score > best.score) best = { index: r, score: score };
    }
    return best.score >= 3 ? best : { index: 0, score: 0 };
  }

  function normalizeRows(rows) {
    var head = findHeaderRow(rows);
    var map = buildColumnMap(rows[head.index] || []);
    // 兜底：表头识别失败时按固定列序 A..F
    var fallback = { level: 0, titleEn: 1, scriptEn: 2, mp3: 3, titleCn: 4, scriptCn: 5 };
    var col = {};
    Object.keys(fallback).forEach(function (k) {
      col[k] = (map[k] === undefined) ? fallback[k] : map[k];
    });

    var items = [];
    for (var r = head.index + 1; r < rows.length; r++) {
      var row = rows[r] || [];
      var pick = function (k) {
        var v = row[col[k]];
        return v == null ? '' : String(v).trim();
      };
      var mp3 = pick('mp3');
      var titleEn = pick('titleEn');
      var titleCn = pick('titleCn');
      var scriptEn = pick('scriptEn');
      var scriptCn = pick('scriptCn');
      var level = pick('level');

      // 跳过空行 / 说明行
      if (!mp3 && !titleEn && !titleCn && !scriptEn) continue;

      var enLines = splitLines(scriptEn);
      var cnLines = splitLines(scriptCn);
      var pairs = [];
      var n = Math.max(enLines.length, cnLines.length);
      for (var i = 0; i < n; i++) {
        pairs.push({ en: enLines[i] || '', cn: cnLines[i] || '' });
      }

      var id = mp3 || ('row-' + r);
      // 编号优先取文件名的规范段（前缀-数字-...），退化为最后一个数字，再退化为行序
      var noMatch = /^[A-Za-z]+\d*-(\d{1,4})-/.exec(mp3) || /(\d{1,4})(?!.*\d)/.exec(mp3);
      items.push({
        id: id,
        row: r + 1,
        level: level.toUpperCase(),
        no: noMatch ? pad3(noMatch[1]) : pad3(items.length + 1),
        titleEn: titleEn || titleCn || '(未命名)',
        titleCn: titleCn || titleEn || '',
        scriptEn: scriptEn,
        scriptCn: scriptCn,
        pairs: pairs,
        mp3: mp3,
        haystack: (level + ' ' + titleEn + ' ' + titleCn + ' ' + scriptEn + ' ' + scriptCn).toLowerCase()
      });
    }
    return items;
  }

  function readXlsx(arrayBuffer) {
    if (typeof XLSX === 'undefined') {
      throw new Error('SheetJS 未加载（assets/vendor/xlsx.full.min.js 缺失）');
    }
    var wb = XLSX.read(new Uint8Array(arrayBuffer), { type: 'array' });
    var name = wb.SheetNames[0];
    if (!name) throw new Error('工作簿里没有工作表');
    var ws = wb.Sheets[name];
    var rows = XLSX.utils.sheet_to_json(ws, { header: 1, blankrows: false, defval: '' });
    return normalizeRows(rows);
  }

  function loadData() {
    errorState.hidden = true;
    skeleton.hidden = false;
    grid.hidden = true;
    resultCount.textContent = '正在读取 data/en.xlsx…';

    return fetch(XLSX_URL, { cache: 'no-cache' })
      .then(function (res) {
        if (!res.ok) throw new Error('HTTP ' + res.status + ' ' + res.statusText);
        return res.arrayBuffer();
      })
      .then(function (buf) {
        state.items = readXlsx(buf);
        if (!state.items.length) throw new Error('表格里没有可用数据行');
        state.levels = Array.from(new Set(state.items.map(function (i) { return i.level; })))
          .filter(Boolean)
          .sort(function (a, b) { return a.localeCompare(b, undefined, { numeric: true }); });
        state.missingVideo = new Set();
        state.presentVideo = new Set();
        state.probedIds = new Set();
        videoQueue = [];

        skeleton.hidden = true;
        grid.hidden = false;
        renderLevelChips();
        renderStats();
        render();
        updateFavBadge();
      })
      .catch(function (err) {
        skeleton.hidden = true;
        errorState.hidden = false;
        var hint = location.protocol === 'file:'
          ? '你是直接双击打开的页面（file:// 协议），浏览器出于安全策略会拦截对本地 xlsx 的读取。'
          : '请确认 data/en.xlsx 存在，且服务器能正常返回该文件。';
        $('errorText').textContent = hint + '（' + (err && err.message ? err.message : err) + '）';
        resultCount.textContent = '读取数据失败';
      });
  }

  /* ============================================================
     渲染：级别 chips / 统计 / 卡片网格
     ============================================================ */
  function renderLevelChips() {
    levelChips.textContent = '';
    var all = [{ key: 'all', label: '全部', count: state.items.length }];
    state.levels.forEach(function (lv) {
      all.push({
        key: lv,
        label: lv,
        count: state.items.filter(function (i) { return i.level === lv; }).length
      });
    });
    all.forEach(function (opt) {
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'chip';
      btn.dataset.level = opt.key;
      btn.setAttribute('aria-pressed', String(state.level === opt.key));
      btn.appendChild(document.createTextNode(opt.label));
      var cnt = document.createElement('span');
      cnt.className = 'chip-count';
      cnt.textContent = opt.count;
      btn.appendChild(cnt);
      btn.addEventListener('click', function () {
        state.level = opt.key;
        renderLevelChips();
        render();
      });
      levelChips.appendChild(btn);
    });
  }

  function renderStats() {
    $('statTotal').textContent = state.items.length;
    $('statLevels').textContent = state.levels.length;
    var lines = state.items.reduce(function (sum, i) { return sum + i.pairs.length; }, 0);
    $('statLines').textContent = lines;
  }

  function filtered() {
    var q = state.query.trim().toLowerCase();
    return state.items.filter(function (item) {
      if (state.favOnly && !isFav(item.id)) return false;
      if (state.level !== 'all' && item.level !== state.level) return false;
      if (q && item.haystack.indexOf(q) === -1) return false;
      return true;
    });
  }

  function render() {
    var list = filtered();
    grid.textContent = '';
    cardEls = new Map();

    var hasFilter = state.query.trim() !== '' || state.level !== 'all' || state.favOnly;
    resetBtn.hidden = !hasFilter;

    // 结果计数
    if (state.favOnly && state.favs.size === 0) {
      resultCount.textContent = '还没有收藏任何课时';
    } else {
      resultCount.textContent = '共 ' + list.length + ' 节';
    }

    // 空状态
    if (!list.length) {
      grid.hidden = true;
      emptyState.hidden = false;
      var t = $('emptyTitle'), p = $('emptyText'), act = $('emptyAction');
      if (state.favOnly && state.favs.size === 0) {
        t.textContent = '收藏夹还是空的';
        p.textContent = '点课程卡片右上角的星标，就能把想反复听的内容收进来。';
        act.textContent = '去浏览全部课程';
        act.hidden = false;
      } else if (state.favOnly) {
        t.textContent = '收藏里没有符合条件的课程';
        p.textContent = '试试换个级别，或清空搜索词。';
        act.textContent = '清空筛选';
        act.hidden = false;
      } else {
        t.textContent = '没有找到课程';
        p.textContent = '换个关键词，或清空筛选条件试试。';
        act.textContent = '清空筛选';
        act.hidden = false;
      }
      return;
    }

    emptyState.hidden = true;
    grid.hidden = false;

    var frag = document.createDocumentFragment();
    list.forEach(function (item) { frag.appendChild(buildCard(item)); });
    grid.appendChild(frag);
  }

  function buildCard(item) {
    var q = state.query.trim();

    var card = document.createElement('article');
    card.className = 'card';
    card.dataset.id = item.id;

    /* 顶部：级别 + 编号 + 收藏 */
    var top = document.createElement('div');
    top.className = 'card-top';

    var badge = document.createElement('span');
    badge.className = 'level-badge ' + levelClass(item.level);
    badge.textContent = item.level || '—';
    top.appendChild(badge);

    var no = document.createElement('span');
    no.className = 'row-no';
    no.textContent = '#' + item.no;
    top.appendChild(no);

    var star = document.createElement('button');
    star.type = 'button';
    star.className = 'star';
    star.setAttribute('aria-pressed', String(isFav(item.id)));
    star.setAttribute('aria-label', (isFav(item.id) ? '取消收藏：' : '收藏：') + item.titleEn);
    star.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3.6l2.6 5.3 5.9.9-4.2 4.1 1 5.8-5.3-2.8-5.3 2.8 1-5.8L3.5 9.8l5.9-.9z"></path></svg>';
    star.addEventListener('click', function (e) {
      e.stopPropagation();
      var added = toggleFav(item.id);
      toast(added ? '已加入收藏' : '已取消收藏');
    });
    top.appendChild(star);
    card.appendChild(top);

    /* 标题（可点击进入详情） */
    var h3 = document.createElement('h3');
    h3.className = 'card-title';
    var titleBtn = document.createElement('button');
    titleBtn.type = 'button';
    titleBtn.className = 'card-title-btn';
    var en = document.createElement('span');
    en.className = 'card-title-en';
    en.appendChild(highlight(item.titleEn, q));
    var cn = document.createElement('span');
    cn.className = 'card-title-cn';
    cn.appendChild(highlight(item.titleCn, q));
    titleBtn.appendChild(en);
    titleBtn.appendChild(cn);
    h3.appendChild(titleBtn);
    card.appendChild(h3);

    /* 元信息：句数 + 视频状态 */
    var meta = document.createElement('div');
    meta.className = 'card-meta';
    var cnt = document.createElement('span');
    cnt.textContent = item.pairs.length + ' 句';
    meta.appendChild(cnt);

    var tag = document.createElement('span');
    tag.className = 'tag-video';
    tag.hidden = true;
    meta.appendChild(tag);
    card.appendChild(meta);

    /* 操作区 */
    var actions = document.createElement('div');
    actions.className = 'card-actions';

    var audioBtn = document.createElement('button');
    audioBtn.type = 'button';
    audioBtn.className = 'btn btn-ghost';
    audioBtn.textContent = '音频';
    audioBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      openSheet(item, 'audio');
    });
    actions.appendChild(audioBtn);

    var videoBtn = document.createElement('button');
    videoBtn.type = 'button';
    videoBtn.className = 'btn btn-ghost';
    videoBtn.textContent = '视频';
    videoBtn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (state.missingVideo.has(item.id)) { toast('这节课的视频还没生成'); return; }
      openSheet(item, 'video');
    });
    actions.appendChild(videoBtn);
    card.appendChild(actions);

    // 整卡点击进入详情（点到按钮/链接时不重复触发）
    card.addEventListener('click', function (e) {
      if (e.target.closest('button, a')) return;
      openSheet(item, 'audio');
    });

    cardEls.set(item.id, { star: star, tag: tag, videoBtn: videoBtn, item: item, title: item.titleEn });
    // 已探测过就直接套用结果；否则交给观察器，滚到附近再探测
    if (state.probedIds.has(item.id)) {
      applyVideoState(item.id, tag, videoBtn);
    } else if (videoObserver) {
      videoObserver.observe(card);
    } else {
      enqueueProbe(item.id);
    }
    return card;
  }

  function applyVideoState(id, tag, videoBtn) {
    if (state.missingVideo.has(id)) {
      tag.hidden = false;
      tag.className = 'tag-video is-off';
      tag.textContent = '视频待生成';
      videoBtn.disabled = true;
      videoBtn.setAttribute('aria-disabled', 'true');
    } else if (state.presentVideo.has(id)) {
      tag.hidden = false;
      tag.className = 'tag-video is-on';
      tag.textContent = '含视频';
      videoBtn.disabled = false;
      videoBtn.removeAttribute('aria-disabled');
    } else {
      tag.hidden = true;
      videoBtn.disabled = false;
    }
  }

  /* ============================================================
     视频可用性探测
     一节视频可能还没生成，只有 HEAD 一次才知道。为免一进页面就
     并发 200 个请求（缺视频的会刷一屏 404），改为：卡片滚动到
     视口附近才入队探测，并发上限 4，结果在内存里缓存。
     ============================================================ */
  function setupVideoObserver() {
    if (!('IntersectionObserver' in window)) return;
    videoObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        videoObserver.unobserve(entry.target);
        enqueueProbe(entry.target.dataset.id);
      });
    }, { rootMargin: '400px 0px' });
  }

  function enqueueProbe(id) {
    if (!id || state.probedIds.has(id)) return;
    state.probedIds.add(id);
    videoQueue.push(id);
    pumpProbe();
  }

  function pumpProbe() {
    while (probeActive < PROBE_CONCURRENCY && videoQueue.length) {
      (function (id) {
        var item = itemById(id);
        if (!item) { pumpProbe(); return; }
        probeActive++;
        fetch(mp4Url(item), { method: 'HEAD' })
          .then(function (res) {
            // 只把明确的 404/410 判为缺失；其它状态按“未知”乐观处理
            if (res.status === 404 || res.status === 410) state.missingVideo.add(id);
            else if (res.ok) state.presentVideo.add(id);
          })
          .catch(function () {})
          .then(function () {
            probeActive--;
            var refs = cardEls.get(id);
            if (refs) applyVideoState(id, refs.tag, refs.videoBtn);
            if (state.openId === id) $('sheetVideoFlag').hidden = !state.missingVideo.has(id);
            pumpProbe();
          });
      })(videoQueue.shift());
    }
  }

  /* ============================================================
     详情弹层：播放 / 下载 / 中英对照
     ============================================================ */
  function openSheet(item, tab) {
    state.openId = item.id;
    state.tab = tab || 'audio';
    state.lastFocus = document.activeElement;

    $('sheetLevel').className = 'level-badge ' + levelClass(item.level);
    $('sheetLevel').textContent = item.level || '—';
    $('sheetNo').textContent = '#' + item.no;
    $('sheetTitle').textContent = item.titleEn;
    $('sheetTitleCn').textContent = item.titleCn;
    $('sheetVideoFlag').hidden = !state.missingVideo.has(item.id);

    // 下载链接
    var dlAudio = $('dlAudio');
    dlAudio.href = mp3Url(item);
    dlAudio.setAttribute('download', item.mp3 || (item.id + '.mp3'));
    var dlVideo = $('dlVideo');
    var vMissing = state.missingVideo.has(item.id);
    dlVideo.href = vMissing ? '#' : mp4Url(item);
    dlVideo.setAttribute('download', item.id.replace(/\.mp3$/i, '') + '.mp4');
    dlVideo.setAttribute('aria-disabled', String(vMissing));

    renderLines(item);
    switchTab(state.tab, item);
    syncSheetFav();

    sheet.hidden = false;
    backdrop.hidden = false;
    sheetScroll.scrollTop = 0;
    document.body.classList.add('is-locked');
    requestAnimationFrame(function () {
      backdrop.classList.add('is-open');
      sheet.classList.add('is-open');
    });

    // 深链：便于分享单节课程
    try { history.replaceState(null, '', '#' + encodeURIComponent(item.id)); } catch (e) {}

    setTimeout(function () { $('sheetClose').focus(); }, 60);
  }

  function closeSheet() {
    if (!state.openId) return;
    stopMedia();
    sheet.classList.remove('is-open');
    backdrop.classList.remove('is-open');
    document.body.classList.remove('is-locked');
    state.openId = null;
    try { history.replaceState(null, '', location.pathname + location.search); } catch (e) {}
    setTimeout(function () {
      sheet.hidden = true;
      backdrop.hidden = true;
      if (state.lastFocus && state.lastFocus.focus) state.lastFocus.focus();
    }, 220);
  }

  function stopMedia() {
    var media = mediaPanel.querySelector('audio, video');
    if (media) {
      try { media.pause(); } catch (e) {}
      media.removeAttribute('src');
      try { media.load(); } catch (e) {}
    }
    mediaPanel.textContent = '';
  }

  function switchTab(tab, itemArg) {
    state.tab = tab;
    var item = itemArg || currentItem();
    if (!item) return;
    var isAudio = tab === 'audio';
    $('tabAudio').classList.toggle('is-active', isAudio);
    $('tabVideo').classList.toggle('is-active', !isAudio);
    $('tabAudio').setAttribute('aria-selected', String(isAudio));
    $('tabVideo').setAttribute('aria-selected', String(!isAudio));
    mediaPanel.setAttribute('aria-labelledby', isAudio ? 'tabAudio' : 'tabVideo');

    mediaPanel.textContent = '';
    if (isAudio) {
      var audio = document.createElement('audio');
      audio.controls = true;
      audio.preload = 'metadata';
      audio.src = mp3Url(item);
      audio.addEventListener('error', function () {
        mediaPanel.textContent = '';
        mediaPanel.appendChild(placeholder('⚠️', '音频加载失败', '请确认 data/ 下存在 ' + item.mp3));
      });
      mediaPanel.appendChild(audio);
      var p = audio.play();
      if (p && p.catch) p.catch(function () { /* 非用户手势触发时浏览器会拦截，忽略 */ });
    } else {
      if (state.missingVideo.has(item.id)) {
        mediaPanel.appendChild(placeholder('🎬', '这节课的视频还没生成', '可以先用音频练习听力，视频生成后刷新页面即可出现。'));
        return;
      }
      var video = document.createElement('video');
      video.controls = true;
      video.playsInline = true;
      video.preload = 'metadata';
      video.setAttribute('poster', 'assets/template_bg.png');
      video.src = mp4Url(item);
      video.addEventListener('error', function () {
        // 实测缺失：同步状态并播报
        state.missingVideo.add(item.id);
        state.presentVideo.delete(item.id);
        var refs = cardEls.get(item.id);
        if (refs) applyVideoState(item.id, refs.tag, refs.videoBtn);
        $('sheetVideoFlag').hidden = false;
        mediaPanel.textContent = '';
        mediaPanel.appendChild(placeholder('🎬', '这节课的视频还没生成', '可以先用音频练习听力，视频生成后刷新页面即可出现。'));
        toast('视频尚未生成');
      });
      mediaPanel.appendChild(video);
      var pv = video.play();
      if (pv && pv.catch) pv.catch(function () {});
    }
  }

  function placeholder(emoji, title, text) {
    var box = document.createElement('div');
    box.className = 'media-placeholder';
    var e = document.createElement('div');
    e.className = 'state-emoji';
    e.textContent = emoji;
    var t = document.createElement('b');
    t.textContent = title;
    var p = document.createElement('p');
    p.textContent = text;
    box.appendChild(e); box.appendChild(t); box.appendChild(p);
    return box;
  }

  function currentItem() {
    return itemById(state.openId);
  }

  function itemById(id) {
    if (!id) return null;
    for (var i = 0; i < state.items.length; i++) {
      if (state.items[i].id === id) return state.items[i];
    }
    return null;
  }

  function renderLines(item) {
    linesEl.textContent = '';
    $('linesCount').textContent = item.pairs.length + ' 句';
    var frag = document.createDocumentFragment();
    item.pairs.forEach(function (pair, i) {
      var li = document.createElement('li');
      li.className = 'line';
      var no = document.createElement('span');
      no.className = 'line-no';
      no.textContent = pad3(i + 1);
      var body = document.createElement('div');
      if (pair.en) {
        var en = document.createElement('p');
        en.className = 'line-en';
        en.lang = 'en';
        en.textContent = pair.en;
        body.appendChild(en);
      }
      if (pair.cn) {
        var cn = document.createElement('p');
        cn.className = 'line-cn';
        cn.lang = 'zh-CN';
        cn.textContent = pair.cn;
        body.appendChild(cn);
      }
      li.appendChild(no);
      li.appendChild(body);
      li.addEventListener('click', function () { li.classList.add('is-revealed'); });
      frag.appendChild(li);
    });
    linesEl.appendChild(frag);
  }

  /* ============================================================
     事件绑定
     ============================================================ */
  function bind() {
    // 搜索（防抖）
    var onSearch = debounce(function () {
      state.query = searchInput.value;
      searchClear.hidden = searchInput.value === '';
      render();
    }, 160);
    searchInput.addEventListener('input', onSearch);
    searchInput.addEventListener('search', onSearch);
    $('searchForm').addEventListener('submit', function (e) { e.preventDefault(); onSearch(); });

    searchClear.addEventListener('click', function () {
      searchInput.value = '';
      state.query = '';
      searchClear.hidden = true;
      searchInput.focus();
      render();
    });

    // 收藏筛选
    favBtn.addEventListener('click', function () {
      state.favOnly = !state.favOnly;
      favBtn.setAttribute('aria-pressed', String(state.favOnly));
      render();
      if (state.favOnly) {
        var n = state.favs.size;
        toast(n ? '只看收藏（' + n + ' 节）' : '还没有收藏，点星标即可添加');
      }
    });

    // 清空筛选
    function resetFilters() {
      state.query = '';
      state.level = 'all';
      state.favOnly = false;
      searchInput.value = '';
      searchClear.hidden = true;
      favBtn.setAttribute('aria-pressed', 'false');
      renderLevelChips();
      render();
    }
    resetBtn.addEventListener('click', resetFilters);
    $('emptyAction').addEventListener('click', resetFilters);
    $('retryBtn').addEventListener('click', loadData);

    // 弹层
    $('sheetClose').addEventListener('click', closeSheet);
    backdrop.addEventListener('click', closeSheet);
    $('sheetFav').addEventListener('click', function () {
      var added = toggleFav(state.openId);
      toast(added ? '已加入收藏' : '已取消收藏');
    });
    $('tabAudio').addEventListener('click', function () { switchTab('audio'); });
    $('tabVideo').addEventListener('click', function () { switchTab('video'); });

    $('dlVideo').addEventListener('click', function (e) {
      if (state.missingVideo.has(state.openId)) {
        e.preventDefault();
        toast('这节课的视频还没生成');
      }
    });

    // 遮蔽中文
    var maskBtn = $('maskCn');
    maskBtn.addEventListener('click', function () {
      var on = !sheet.classList.contains('mask-cn');
      sheet.classList.toggle('mask-cn', on);
      maskBtn.setAttribute('aria-pressed', String(on));
      maskBtn.textContent = on ? '显示中文' : '遮蔽中文';
      if (on) toast('中文已遮盖，点句子可单独显示');
    });

    // 键盘：ESC 关闭、Tab 焦点循环
    document.addEventListener('keydown', function (e) {
      if (!state.openId) return;
      if (e.key === 'Escape') { e.preventDefault(); closeSheet(); return; }
      if (e.key === 'Tab') {
        var focusables = sheet.querySelectorAll('button, a[href], audio, video, [tabindex]:not([tabindex="-1"])');
        var list = Array.prototype.filter.call(focusables, function (el) {
          return !el.disabled && el.offsetParent !== null;
        });
        if (!list.length) return;
        var first = list[0], last = list[list.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    });

    // 移动端：下滑手势关闭底部弹层
    var startY = null;
    sheet.addEventListener('touchstart', function (e) {
      if (sheetScroll.scrollTop > 0) return;
      startY = e.touches[0].clientY;
    }, { passive: true });
    sheet.addEventListener('touchend', function (e) {
      if (startY === null) return;
      var dy = e.changedTouches[0].clientY - startY;
      if (dy > 90 && sheetScroll.scrollTop <= 0) closeSheet();
      startY = null;
    }, { passive: true });
  }

  /* ============================================================
     启动
     ============================================================ */
  function openFromHash() {
    var id = decodeURIComponent((location.hash || '').replace(/^#/, ''));
    if (!id) return false;
    var item = itemById(id);
    if (!item) return false;
    openSheet(item, 'audio');
    return true;
  }

  /** 同一标签页里粘贴 #课程 属于同文档导航，不会重新加载页面，
      必须监听 hashchange 才能打开对应课程。 */
  function watchHash() {
    window.addEventListener('hashchange', function () {
      var id = decodeURIComponent((location.hash || '').replace(/^#/, ''));
      if (!id) {
        if (state.openId) closeSheet();
        return;
      }
      if (id === state.openId) return;
      var item = itemById(id);
      if (item) openSheet(item, 'audio');
    });
  }

  function init() {
    loadFavs();
    updateFavBadge();
    setupVideoObserver();
    watchHash();
    bind();
    // 数据就绪后再尝试打开深链（#<mp3文件名>）
    loadData().then(openFromHash);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
