// ===== 全局状态 =====
// INITIAL_FILES / VECTOR_READY 由 templates/index.html 中的 <script> 注入
let currentTab = 'upload', selectedModuleId = null, selectedModuleName = null;
let selectedDocId = null, selectedDocType = null;
let allModules = [], currentChunks = [], currentChunkIdx = 0;
let editorPath = '', editorDirty = false, resultFiles = [];
let _currentFileList = [], _currentFileFilter = 'all';

// ===== 工具 =====
function toast(m) {
  const t = document.getElementById('toast');
  t.textContent = m;
  t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2800);
}
function esc(s) {
  return String(s).replace(/\\/g, '\\\\').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}
// 轻量版：用于 HTML 文本内容展示（不转义反斜杠，保持路径可读）
function escText(s) {
  return String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function showModal(html) {
  document.getElementById('modal-content').innerHTML = html;
  document.getElementById('modal-overlay').classList.remove('hidden');
}
function closeModal() { document.getElementById('modal-overlay').classList.add('hidden'); }

function excelActionButtons(path, name) {
  const encPath = encodeURIComponent(path);
  const ext = path.split('.').pop().toLowerCase();
  const binaryExts = ['xlsx', 'xls', 'zip', 'png', 'jpg', 'jpeg', 'gif', 'ico', 'pdf'];
  const showEdit = !binaryExts.includes(ext);
  return '<div class="btn-row" style="margin-top:8px">'
    + '<button class="btn btn-sm btn-outline" data-action="openLocalFile" data-path="' + encPath + '">📂 打开</button>'
    + '<a class="btn btn-sm btn-outline" href="/api/files/download-file?path=' + encPath + '" download="' + esc(name || '') + '">📥 下载</a>'
    + (showEdit ? '<button class="btn btn-sm btn-outline" data-action="openFileEditor" data-path="' + encPath + '">✏️ 编辑</button>' : '')
    + '</div>';
}
async function openLocalFile(path) {
  const fd = new FormData(); fd.append('file_path', path);
  try {
    const r = await fetch('/api/files/open-file', { method: 'POST', body: fd });
    const d = await r.json();
    toast(d.success ? '✅ 已打开文件' : '❌ ' + (d.message || '打开失败'));
  } catch (e) { toast('❌ 打开失败: ' + e.message); }
}

async function pollTask(taskId, onProgress, onDone) {
  for (let i = 0; i < 900; i++) {
    try {
      const r = await fetch('/task/' + taskId);
      const d = await r.json();
      const t = d.task || {};
      if (t.status === 'completed') { onDone(t.result || t); return; }
      if (t.status === 'failed') { onDone({ error: t.error || t.message || '任务失败' }); return; }
      onProgress(t.progress || 0, t.message || '');
    } catch (e) { onDone({ error: e.message }); return; }
    await sleep(2000);
  }
  onDone({ error: '任务超时' });
}

// ===== 全局事件委托（消除所有内联 onclick XSS 面） =====
document.addEventListener('click', function(e) {
  const target = e.target;

  // 关闭弹窗
  if (target.closest('#modal-overlay') && !target.closest('.modal')) closeModal();
  if (target.closest('.js-close-modal')) closeModal();

  // 文件操作
  const viewBtn = target.closest('.js-view-file');
  if (viewBtn) { viewFile(viewBtn.dataset.filename); return; }
  const delBtn = target.closest('.js-delete-file');
  if (delBtn) { deleteFile(delBtn.dataset.filename); return; }

  // 模块树 - 选择
  const modItem = target.closest('.js-module-item');
  if (modItem && !target.closest('.js-module-action')) {
    selectModule(modItem.dataset.moduleId, modItem.dataset.moduleName);
    return;
  }
  // 模块树 - 重命名
  const renameBtn = target.closest('.js-rename-module');
  if (renameBtn) { e.stopPropagation(); renameModule(renameBtn.dataset.moduleId, renameBtn.dataset.moduleName); return; }
  // 模块树 - 删除
  const delModBtn = target.closest('.js-delete-module');
  if (delModBtn) { e.stopPropagation(); deleteModule(delModBtn.dataset.moduleId, delModBtn.dataset.moduleName); return; }

  // 文档关联 - 详情
  const detailBtn = target.closest('.js-doc-detail');
  if (detailBtn) { showDocDetail(detailBtn.dataset.docId, detailBtn.dataset.docType); return; }
  // 文档关联 - 解绑
  const unbindBtn = target.closest('.js-doc-unbind');
  if (unbindBtn) { unbindDocFromModule(unbindBtn.dataset.docId, unbindBtn.dataset.docType); return; }
  // 文档关联 - 绑定
  const bindBtn = target.closest('.js-doc-bind');
  if (bindBtn) { bindDocToModule(bindBtn.dataset.docId, bindBtn.dataset.docType); return; }

  // 确认计划
  const confirmBtn = target.closest('.js-confirm-plan');
  if (confirmBtn) { confirmPlan(confirmBtn.dataset.path); return; }

  // 模块关联 — 解除
  const unlinkModBtn = target.closest('.js-unlink-module');
  if (unlinkModBtn) { unlinkModuleFromModule(unlinkModBtn.dataset.moduleName); return; }

  // 术语删除（文档详情视图）
  const docTermDelBtn = target.closest('.js-delete-term-doc');
  if (docTermDelBtn) { deleteDocGlossaryTerm(parseInt(docTermDelBtn.dataset.termId)); return; }

  // 术语删除（模块视图）
  const termDelBtn = target.closest('.js-delete-term');
  if (termDelBtn) { deleteGlossaryTerm(parseInt(termDelBtn.dataset.termId)); return; }
});

// ===== Tab 切换 =====
function switchTab(name) {
  if (!name) return;
  currentTab = name;
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  const targetTab = document.getElementById('tab-' + name);
  if (!targetTab) { console.error('Tab not found: tab-' + name); return; }
  targetTab.classList.add('active');
  document.querySelectorAll('nav a').forEach(a => {
    a.classList.remove('active');
    if (a.dataset.tab === name) a.classList.add('active');
  });
  if (name === 'upload') refreshFileList();
  if (name === 'manage') refreshModuleTree();
}
function setNavStatus(ok) {
  const s = document.getElementById('nav-status');
  s.textContent = ok ? '✅ 已就绪' : '❌ 未上传';
  s.style.opacity = ok ? '1' : '.6';
}

// ===== 文件上传 =====
function handleDrop(e, type) { e.preventDefault(); e.target.classList.remove('dragover'); if (e.dataTransfer.files[0]) uploadWithFile(e.dataTransfer.files[0], type); }
function uploadFile(input, type) { if (input.files[0]) { uploadWithFile(input.files[0], type); input.value = ''; } }
function _makeProgressCard(icon, name, id) {
  return '<div class="card upload-progress-card" id="' + id + '">'
    + '<h3>' + icon + ' ' + escText(name) + '</h3>'
    + '<div class="progress-bar"><div class="progress-fill" style="width:0%"></div></div>'
    + '<div class="progress-text" style="font-size:12px;color:var(--text-dim);margin-top:6px"></div>'
    + '</div>';
}

async function uploadWithFile(file, type) {
  const fd = new FormData(); fd.append('file', file);
  const uid = 'up-' + Date.now() + '-' + Math.random().toString(36).slice(2, 6);
  const container = document.getElementById('upload-progress-container');
  const icons = { product: '📄', api: '📡', axure: '🎨' };
  container.insertAdjacentHTML('afterbegin', _makeProgressCard(icons[type] || '📄', file.name, uid));
  const card = document.getElementById(uid);
  const bar = card.querySelector('.progress-fill');
  const txt = card.querySelector('.progress-text');
  let _uploadDone = false;
  txt.textContent = '正在上传...';
  try {
    const r = await fetch('/api/files/upload-file', { method: 'POST', body: fd });
    if (!r.ok) { card.querySelector('h3').innerHTML = '❌ ' + escText(file.name); txt.textContent = '上传失败 (HTTP ' + r.status + ')'; _uploadDone = true; return; }
    const d = await r.json();
    if (!d.task_id) { card.querySelector('h3').innerHTML = '❌ ' + escText(file.name); txt.textContent = d.message || '上传失败'; _uploadDone = true; return; }
    await pollTask(d.task_id,
      (p, m) => { bar.style.width = p + '%'; txt.textContent = m; },
      async result => {
        _uploadDone = true;
        bar.style.width = '100%';
        if (type === 'api' && result && result.apis) { txt.textContent = '提取完成'; showApiConfirmModal(result, file.name); }
        else if (result && !result.error) { txt.textContent = '✅ 处理完成'; toast('✅ ' + file.name + ' 处理完成'); showUploadResult(result); }
        else { card.querySelector('h3').innerHTML = '❌ ' + escText(file.name); txt.textContent = result.error || '处理失败'; }
        refreshFileList();
      });
  } catch (e) { txt.textContent = '❌ ' + e.message; _uploadDone = true; }
  finally { setTimeout(() => { if (_uploadDone) { card.style.opacity = '0'; card.style.transition = 'opacity 0.5s'; setTimeout(() => card.remove(), 500); } }, 3000); }
}
function showApiConfirmModal(result, fileName) {
  const apis = result.apis || [], mod = result.module_name || '';
  const rows = apis.map((a, i) => `<label style="display:flex;align-items:center;gap:8px;padding:6px 0;border-bottom:1px solid #eee"><input type="checkbox" checked data-idx="${i}"><b>${esc(a.method || '?')}</b> ${esc(a.url || '')} — ${esc(a.name || '')}</label>`).join('');
  showModal(`<h3>📡 接口拆分确认</h3><p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">模块: <b>${esc(mod)}</b> | 文件: ${esc(fileName)}</p><div style="max-height:400px;overflow-y:auto">${rows}</div>
    <div class="btn-row"><button class="btn btn-outline" id="retry-api-btn">🔄 重新拆分</button><button class="btn btn-success" id="commit-api-btn">✅ 确认入库</button></div>`);
  document.getElementById('commit-api-btn').onclick = async () => {
    let selected = [], allChecked = true;
    document.querySelectorAll('#modal-content input[type=checkbox]').forEach(cb => {
      if (cb.checked) selected.push(apis[parseInt(cb.dataset.idx)]); else allChecked = false;
    });
    closeModal();
    const r = await fetch('/api/upload/commit-api', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_path: result.file_path, module_name: mod, apis: selected, all_selected: allChecked }) });
    const d = await r.json(); toast(d.success ? '✅ ' + selected.length + ' 个接口已入库' : '❌ 入库失败');
    refreshFileList();
  };
  document.getElementById('retry-api-btn').onclick = async () => {
    closeModal();
    const r = await fetch('/api/upload/retry-api', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ file_path: result.file_path, module_name: mod }) });
    const d = await r.json(); if (d.success) showApiConfirmModal(d, fileName); else toast('❌ 重试失败');
  };
}
function showUploadResult(result) {
  const m = result.module_name || '?';
  const chunks = result.file ? result.file.chunks : (result.chunks || 0);
  let html = `<p>${escText(result.message || '处理完成')}</p>`;
  if (m !== '?') html += `<p>识别模块: <b>${escText(m)}</b></p>`;
  if (chunks) html += `<p>文本块: ${chunks}</p>`;
  if (result.api_count) html += `<p>接口数: ${result.api_count}</p>`;
  if (result.doc_id) html += `<p style="font-size:11px;color:var(--text-dim)">doc_id: ${escText(result.doc_id)}</p>`;
  showModal(`<h3>✅ 处理完成</h3>${html}<div class="btn-row"><button class="btn js-close-modal">确定</button></div>`);
}

// ===== 文件列表 =====
async function refreshFileList() {
  try {
    const r = await fetch('/api/files/uploaded-files');
    if (!r.ok) { console.error('文件列表加载失败:', r.status); return; }
    const d = await r.json();
    if (!d.files) { console.error('文件列表响应异常:', d); return; }
    renderFileList(d.files);
    setNavStatus(d.vector_ready);
  } catch (e) {
    console.error('文件列表加载异常:', e);
    document.getElementById('file-sections').innerHTML = '<div class="empty-hint" style="color:var(--danger)">⚠️ 文件列表加载失败，请刷新重试</div>';
  }
}
function setFileFilter(f, btn) {
  _currentFileFilter = f;
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  document.getElementById('file-filter-input').value = '';
  renderFileSections();
}
function filterFileList() { renderFileSections(); }
function renderFileList(files) {
  _currentFileList = files || [];
  document.getElementById('file-filter-input').value = '';
  _currentFileFilter = 'all';
  document.querySelectorAll('.filter-btn').forEach(b => b.classList.toggle('active', b.dataset.filter === 'all'));
  renderFileSections();
}
function renderFileSections() {
  let files = _currentFileList;
  const search = document.getElementById('file-filter-input').value.trim().toLowerCase();
  if (search) files = files.filter(f => f.name.toLowerCase().includes(search));
  const groups = { product: [], api: [], axure: [] };
  files.forEach(f => { const t = f.type || ''; if (groups[t]) groups[t].push(f); else groups.product.push(f); });
  const showAll = _currentFileFilter === 'all';
  let sections = [
    { key: 'product', icon: '📄', label: '产品文档', files: showAll || _currentFileFilter === 'product' ? groups.product : [] },
    { key: 'api', icon: '📡', label: '接口文档', files: showAll || _currentFileFilter === 'api' ? groups.api : [] },
    { key: 'axure', icon: '🎨', label: 'Axure 原型', files: showAll || _currentFileFilter === 'axure' ? groups.axure : [] },
  ];
  if (!showAll) sections = sections.filter(s => s.files.length || _currentFileFilter === s.key);
  const el = document.getElementById('file-sections');
  if (!files.length && !search) {
    el.innerHTML = '<div class="empty-hint">暂无文件，请上传文档</div>'; return;
  }
  el.innerHTML = sections.map(s => renderFileSection(s)).join('');
}
function renderFileSection(s) {
  const files = s.files;
  if (!files.length) return `<div class="file-section"><div class="file-section-header"><span>${s.icon} ${s.label}</span><span class="count">0 个文件</span></div></div>`;
  const rows = files.map(f => `<tr><td><span class="name-row"><span>${esc(f.name)}</span></span></td><td>${f.size || '—'}</td><td>${f.chunks || 0}</td><td>${f.time || ''}</td><td style="white-space:nowrap"><button class="btn btn-sm btn-outline js-view-file" data-filename="${esc(f.name)}">查看</button> <button class="btn btn-sm btn-danger-outline js-delete-file" data-filename="${esc(f.name)}">删除</button></td></tr>`).join('');
  return `<div class="file-section">
    <div class="file-section-header" onclick="this.querySelector('.arrow').classList.toggle('open');this.nextElementSibling.classList.toggle('hidden')">
      <span class="arrow open">▶</span><span>${s.icon} ${s.label}</span><span class="count">${files.length} 个文件</span>
    </div>
    <div class="file-section-body">
      <table class="file-table"><thead><tr><th>文件名</th><th>大小</th><th>文本块</th><th>时间</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table>
    </div></div>`;
}
function viewFile(name) {
  const f = _currentFileList.find(x => x.name === name);
  if (!f) { toast('未找到文件信息'); return; }
  let extra = [];
  if (f.doc_id) extra.push(`<p>doc_id: <code style="font-size:11px">${esc(f.doc_id)}</code></p>`);
  if (f.status) extra.push(`<p>状态: ${esc(f.status)}</p>`);
  showModal(`<h3>📋 ${esc(name)}</h3><p>类型: ${esc(f.type || '?')}</p><p>大小: ${esc(f.size || '—')}</p><p>文本块: ${f.chunks || 0}</p><p>时间: ${esc(f.time || '—')}</p>${extra.join('')}<div class="btn-row"><button class="btn js-close-modal">关闭</button></div>`);
}
async function deleteFile(name) {
  if (!confirm('确定删除 "' + name + '"？')) return;
  const fd = new FormData(); fd.append('filename', name);
  const r = await fetch('/api/files/delete-file', { method: 'POST', body: fd });
  const d = await r.json(); toast(d.message); refreshFileList();
}

// ===== 模块树 =====
async function refreshModuleTree() {
  try { const r = await fetch('/api/modules'); const d = await r.json(); allModules = d.tree || []; renderModuleTree(allModules); } catch (e) { console.error('模块树加载失败:', e); document.getElementById('module-tree').innerHTML = '<div class="empty-hint" style="color:var(--danger)">模块加载失败，请刷新重试</div>'; }
}
function renderModuleTree(tree) {
  const el = document.getElementById('module-tree');
  if (!tree || !tree.length) { el.innerHTML = '<div class="empty-hint">暂无模块</div>'; return; }
  el.innerHTML = tree.map(n => renderTreeNode(n, 0)).join('');
}
function renderTreeNode(node, depth) {
  const hasCh = node.children && node.children.length > 0;
  const icon = node.name === '全部模块' ? '🏠' : (hasCh ? '📁' : '📄');
  const sel = node.id === selectedModuleId ? ' selected' : '';
  let actions = '';
  if (node.name !== '全部模块') {
    actions = `<span class="actions">`
      + `<button class="js-rename-module js-module-action" data-module-id="${esc(node.id)}" data-module-name="${esc(node.name)}" title="重命名">✏️</button>`
      + `<button class="js-delete-module js-module-action" data-module-id="${esc(node.id)}" data-module-name="${esc(node.name)}" title="删除">🗑</button>`
      + `</span>`;
  }
  let html = `<div class="item${sel} js-module-item" style="padding-left:${12 + depth * 16}px" data-module-id="${esc(node.id)}" data-module-name="${esc(node.name)}">${icon} ${esc(node.name)}${actions}</div>`;
  if (hasCh) html += node.children.map(c => renderTreeNode(c, depth + 1)).join('');
  return html;
}
function selectModule(id, name) {
  selectedModuleId = id; selectedModuleName = name; selectedDocId = null; selectedDocType = null;
  document.getElementById('chunk-viewer').classList.add('hidden');
  renderModuleTree(allModules);
  document.getElementById('center-title').textContent = name;
  showUnassociatedPanel();
  loadBoundDocs(name);
  loadUnassociatedDocs();
  loadRelatedModules(name);
}
function flattenTree(tree) {
  let out = [];
  for (const n of tree) { out.push(n); if (n.children) out = out.concat(flattenTree(n.children)); }
  return out;
}

function showUnassociatedPanel() {
  document.getElementById('unassociated-by-type').classList.remove('hidden');
  document.getElementById('related-modules-section').classList.remove('hidden');
  document.getElementById('related-modules-rt').classList.remove('hidden');
  document.getElementById('glossary-content').classList.add('hidden');
}
function showGlossaryPanel() {
  document.getElementById('unassociated-by-type').classList.add('hidden');
  document.getElementById('related-modules-rt').classList.add('hidden');
  document.getElementById('glossary-content').classList.remove('hidden');
}
async function createModule() {
  let inp = document.getElementById('new-module-input'), name = inp.value.trim();
  if (!name) { name = prompt('模块名称:'); if (!name) return; }
  const pid = selectedModuleId || 'root';
  try {
    const r = await fetch('/api/modules', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name, parent_id: pid }) });
    const d = await r.json(); if (d.success) { inp.value = ''; refreshModuleTree(); toast('✅ 模块已创建'); } else toast('❌ ' + d.message);
  } catch (e) { console.error(e); toast('❌ 创建失败'); }
}
async function renameModule(id, cur) {
  const name = prompt('新名称:', cur); if (!name) return;
  try {
    const r = await fetch('/api/modules/' + encodeURIComponent(id), { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
    const d = await r.json(); if (d.success) { refreshModuleTree(); toast('✅ 已重命名'); } else toast('❌ ' + d.message);
  } catch (e) { console.error(e); toast('❌ 失败'); }
}
async function deleteModule(id, name) {
  if (!confirm('确定删除模块 "' + name + '"？\n\n该模块下绑定的文档将被解绑但不会删除。')) return;
  try {
    const r = await fetch('/api/modules/' + encodeURIComponent(id), { method: 'DELETE' }); const d = await r.json();
    if (d.success) {
      if (selectedModuleId === id) { selectedModuleId = null; selectedModuleName = null; }
      refreshModuleTree(); document.getElementById('center-title').textContent = '模块关联';
      document.getElementById('bound-docs').innerHTML = '<div class="empty-hint">请选择模块</div>';
      document.getElementById('unassociated-docs').innerHTML = '';
      document.getElementById('related-modules-content').innerHTML = '';
      toast('✅ 已删除');
    } else toast('❌ ' + d.message);
  } catch (e) { console.error(e); toast('❌ 失败'); }
}
async function loadBoundDocs(modName) {
  try {
    const r = await fetch('/api/modules/' + encodeURIComponent(modName) + '/docs'); const d = await r.json();
    const docs = d.docs || [];
    const el = document.getElementById('bound-docs');
    if (!docs.length) { el.innerHTML = '<div class="empty-hint">该模块下暂无文档</div>'; return; }
    const grouped = {};
    docs.forEach(d => { const t = d.doc_type || 'product'; if (!grouped[t]) grouped[t] = []; grouped[t].push(d); });
    const icons = { product: '📄', api: '📡', axure: '🎨' };
    const labels = { product: '产品文档', api: '接口定义', axure: 'Axure 原型' };
    el.innerHTML = Object.keys(grouped).map(dt => {
      const list = grouped[dt];
      const items = list.map(d => {
        const icon = icons[dt] || '📄';
        return '<div class="doc-assoc-item"><span class="name">' + icon + ' ' + esc(d.doc_id || '') + ' <span style="color:var(--text-dim);font-size:11px">' + esc(d.file_name || '') + '</span></span>'
          + '<span class="actions"><button class="btn btn-sm btn-outline js-doc-detail" data-doc-id="' + esc(d.doc_id) + '" data-doc-type="' + esc(dt) + '">详情</button>'
          + '<button class="btn btn-sm btn-danger-outline js-doc-unbind" data-doc-id="' + esc(d.doc_id) + '" data-doc-type="' + esc(dt) + '">解绑</button></span></div>';
      }).join('');
      return '<div class="assoc-group"><div class="assoc-group-header" onclick="this.querySelector(\'.arrow\').classList.toggle(\'open\');this.nextElementSibling.classList.toggle(\'collapsed\')"><span>' + (icons[dt] || '📄') + ' ' + (labels[dt] || dt) + '</span><span style="font-size:11px;color:var(--text-dim)">' + list.length + ' 个</span><span class="arrow open" style="margin-left:auto">▶</span></div><div class="assoc-group-body">' + items + '</div></div>';
    }).join('');
  } catch (e) { document.getElementById('bound-docs').innerHTML = '<div class="empty-hint">加载失败</div>'; }
}
async function loadUnassociatedDocs() {
  try {
    const r = await fetch('/api/docs/unassociated'); const d = await r.json();
    const docs = d.docs || [];
    const el = document.getElementById('unassociated-by-type');
    if (!docs.length) { el.innerHTML = '<div class="empty-hint">所有文档均已关联</div>'; return; }
    const grouped = {};
    docs.forEach(d => { const t = d.doc_type || 'product'; if (!grouped[t]) grouped[t] = []; grouped[t].push(d); });
    const icons = { product: '📄', api: '📡', axure: '🎨' };
    const labels = { product: "产品文档", api: "接口定义", axure: "Axure 原型" };
    let tabsHtml = '<div class="doc-type-tabs">';
    Object.keys(grouped).forEach((dt, i) => {
      tabsHtml += '<button class="' + (i === 0 ? 'active' : '') + '" onclick="document.querySelectorAll(\'.unassoc-group\').forEach(g=>g.classList.add(\'hidden\'));document.getElementById(\'unassoc-\'+this.dataset.dt).classList.remove(\'hidden\');document.querySelectorAll(\'.doc-type-tabs button\').forEach(b=>b.classList.remove(\'active\'));this.classList.add(\'active\')" data-dt="' + dt + '">' + (icons[dt] || '📄') + ' ' + (labels[dt] || dt) + ' (' + grouped[dt].length + ')</button>';
    });
    tabsHtml += '</div>';
    let bodyHtml = Object.keys(grouped).map((dt, i) => {
      const items = grouped[dt].map(d => {
        const icon = icons[dt] || '📄';
        return '<div class="doc-assoc-item"><span class="name">' + icon + ' ' + esc(d.file_name || d.doc_id || '') + '</span>'
          + '<span class="actions"><button class="btn btn-sm btn-outline js-doc-bind" data-doc-id="' + esc(d.doc_id) + '" data-doc-type="' + esc(dt) + '">关联</button></span></div>';
      }).join('');
      return '<div class="unassoc-group' + (i === 0 ? '' : ' hidden') + '" id="unassoc-' + dt + '">' + items + '</div>';
    }).join('');
    el.innerHTML = tabsHtml + bodyHtml;
  } catch (e) { document.getElementById('unassociated-by-type').innerHTML = '<div class="empty-hint">加载失败</div>'; }
}
function showDocDetail(docId, docType) {
  selectedDocId = docId; selectedDocType = docType;
  // 加载文档块
  fetch('/api/docs/' + encodeURIComponent(docId) + '/chunks').then(r => r.json()).then(d => {
    currentChunks = d.chunks || []; currentChunkIdx = 0;
    const el = document.getElementById('chunk-viewer');
    el.classList.remove('hidden');
    renderCurrentChunk();
  }).catch(() => toast('加载文档块失败'));
  // 产品文档额外加载术语表
  if (docType === 'product') {
    showGlossaryPanel();
    document.getElementById('glossary-content').classList.remove('hidden');
    loadDocGlossary(docId);
  }
}
async function loadDocGlossary(docId) {
  try {
    const r = await fetch('/api/docs/' + encodeURIComponent(docId) + '/glossary');
    const d = await r.json();
    const terms = d.terms || [];
    const el = document.getElementById('glossary-terms');
    if (!terms.length) { el.innerHTML = '<div class="empty-hint">该文档暂无术语</div>'; return; }
    el.innerHTML = terms.map(t => '<div class="glossary-item"><span class="remove js-delete-term-doc" data-term-id="' + t.id + '">✕</span><div class="term-name">' + esc(t.term) + '</div><div class="term-def">' + esc(t.definition || '') + '</div></div>').join('');
  } catch (e) { document.getElementById('glossary-terms').innerHTML = '<div class="empty-hint">加载失败</div>'; }
}
function renderCurrentChunk() {
  const c = currentChunks[currentChunkIdx];
  if (!c) { document.getElementById('chunk-viewer').innerHTML = '<div class="empty-hint">无内容</div>'; return; }
  const total = currentChunks.length;
  const nav = '<div class="chunk-nav">'
    + '<button class="btn btn-sm btn-outline" onclick="if(currentChunkIdx>0){currentChunkIdx--;renderCurrentChunk()}">◀ 上一个</button>'
    + '<span>' + (currentChunkIdx + 1) + ' / ' + total + '</span>'
    + '<button class="btn btn-sm btn-outline" onclick="if(currentChunkIdx<' + (total - 1) + '){currentChunkIdx++;renderCurrentChunk()}">下一个 ▶</button>'
    + (c.api_name ? ' <span style="color:var(--primary)">' + esc(c.api_name) + '</span>' : '')
    + '</div>';
  document.getElementById('chunk-viewer').innerHTML = nav + '<div style="white-space:pre-wrap;line-height:1.7;margin-top:8px">' + esc(c.content) + '</div>';
}
async function bindDocToModule(docId, docType) {
  if (!selectedModuleName) { toast('请先选择模块'); return; }
  try {
    const r = await fetch('/api/bindings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_type: docType, source_id: docId, target_type: 'module', target_id: selectedModuleName }) });
    const d = await r.json(); toast(d.success ? '✅ 已关联' : '❌ ' + d.message);
    loadBoundDocs(selectedModuleName); loadUnassociatedDocs(); loadRelatedModules(selectedModuleName);
  } catch (e) { console.error(e); toast('❌ 关联失败'); }
}
async function unbindDocFromModule(docId, docType) {
  if (!selectedModuleName) return;
  try {
    const r = await fetch('/api/bindings', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ a_type: docType, a_id: docId, b_type: 'module', b_id: selectedModuleName }) });
    const d = await r.json(); toast(d.success ? '✅ 已解绑' : '❌ ' + d.message);
    loadBoundDocs(selectedModuleName); loadUnassociatedDocs(); loadRelatedModules(selectedModuleName);
  } catch (e) { console.error(e); toast('❌ 解绑失败'); }
}
async function loadRelatedModules(modName) {
  try {
    const r = await fetch('/api/modules/' + encodeURIComponent(modName) + '/related'); const d = await r.json();
    const mods = d.related || [];
    // 中栏展示：已关联模块列表
    const ctr = document.getElementById('related-modules-content');
    if (!mods.length) { ctr.innerHTML = '<div class="empty-hint">暂无模块关联</div>'; }
    else {
      ctr.innerHTML = '<div style="margin-top:8px"><div style="font-size:12px;color:var(--text-dim);margin-bottom:4px">🔗 已关联模块</div>'
        + mods.map(m => '<div class="doc-assoc-item"><span class="name">📁 ' + esc(m.name) + '</span> <span class="actions"><button class="btn btn-sm btn-danger-outline js-unlink-module" data-module-name="' + esc(m.name) + '">解除</button></span></div>').join('')
        + '</div>';
    }
    // 右侧栏：显示已关联数量，下方为添加入口
    const el = document.getElementById('related-modules-rt-content');
    if (!mods.length) { el.innerHTML = '<div class="empty-hint">暂无关联</div>'; }
    else { el.innerHTML = '<div class="empty-hint">已关联 ' + mods.length + ' 个模块，可在中栏管理</div>'; }
    // 下拉框：树形展示所有模块（排除自身和已关联的）
    const sel = document.getElementById('link-module-select');
    sel.innerHTML = '<option value="">-- 选择模块 --</option>';
    const linkedNames = new Set(mods.map(m => m.name));
    function _addTreeOpts(nodes, depth) {
      const prefix = '    '.repeat(depth);  // &nbsp; x4
      for (const n of nodes) {
        if (n.name !== modName && !linkedNames.has(n.name)) {
          sel.innerHTML += '<option value="' + esc(n.name) + '">' + prefix + esc(n.name) + '</option>';
        }
        if (n.children) _addTreeOpts(n.children, depth + 1);
      }
    }
    _addTreeOpts(allModules, 0);
  } catch (e) {
    document.getElementById('related-modules-content').innerHTML = '<div class="empty-hint">加载失败</div>';
    document.getElementById('related-modules-rt-content').innerHTML = '<div class="empty-hint">加载失败</div>';
  }
}

async function linkModuleToModule() {
  if (!selectedModuleName) return;
  const sel = document.getElementById('link-module-select');
  const targetName = sel.value;
  if (!targetName) return;
  try {
    const r = await fetch('/api/bindings', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ source_type: 'module', source_id: selectedModuleName, target_type: 'module', target_id: targetName }) });
    const d = await r.json();
    if (d.success) { toast('✅ 已关联模块: ' + targetName); loadRelatedModules(selectedModuleName); }
    else toast('❌ ' + d.message);
  } catch (e) { console.error(e); toast('❌ 关联失败'); }
}

async function unlinkModuleFromModule(targetName) {
  if (!selectedModuleName) return;
  try {
    const r = await fetch('/api/bindings', { method: 'DELETE', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ a_type: 'module', a_id: selectedModuleName, b_type: 'module', b_id: targetName }) });
    const d = await r.json();
    if (d.success) { toast('✅ 已解除关联: ' + targetName); loadRelatedModules(selectedModuleName); }
    else toast('❌ ' + d.message);
  } catch (e) { console.error(e); toast('❌ 解除失败'); }
}

// ===== 模块关联弹窗（审核确认） =====
async function showModuleAuditModal(result, fileName) {
  const mod = result.module_name || '';
  const related = result.related_modules || [];
  const docId = result.doc_id || '';
  showModal('<h3>🔍 模块关联审核</h3><p style="font-size:12px;color:var(--text-dim);margin-bottom:8px">文件: <b>' + esc(fileName) + '</b></p>'
    + '<label style="display:block;margin-bottom:4px;font-weight:600">主模块:</label><input id="audit-module" style="width:100%;padding:8px;border:1px solid var(--border);border-radius:4px;margin-bottom:12px" value="' + esc(mod) + '">'
    + '<label style="display:block;margin-bottom:4px;font-weight:600">关联模块（每行一个）:</label><textarea id="audit-related" style="width:100%;padding:8px;border:1px solid var(--border);border-radius:4px;min-height:80px;margin-bottom:12px" rows="4">' + related.map(esc).join('\n') + '</textarea>'
    + '<div class="btn-row"><button class="btn btn-outline js-close-modal">取消</button><button class="btn btn-success" id="audit-commit-btn">✅ 确认</button></div>');
  document.getElementById('audit-commit-btn').onclick = async () => {
    const newMod = document.getElementById('audit-module').value.trim();
    const newRelated = document.getElementById('audit-related').value.split('\n').map(s => s.trim()).filter(Boolean);
    closeModal();
    const r = await fetch('/update-module', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ doc_id: docId, module_name: newMod, related_modules: newRelated }) });
    const d = await r.json(); toast(d.success ? '✅ ' + d.message : '❌ ' + d.message);
    refreshFileList();
  };
}

// ===== 术语表管理 =====
async function loadGlossary(modName) {
  showGlossaryPanel();
  try {
    const r = await fetch('/api/modules/' + encodeURIComponent(modName) + '/glossary'); const d = await r.json();
    const terms = d.terms || [];
    const el = document.getElementById('glossary-terms');
    if (!terms.length) { el.innerHTML = '<div class="empty-hint">暂无术语</div>'; return; }
    el.innerHTML = terms.map(t => '<div class="glossary-item"><span class="remove js-delete-term" data-term-id="' + t.id + '">✕</span><div class="term-name">' + esc(t.term) + '</div><div class="term-def">' + esc(t.definition || '') + '</div></div>').join('');
  } catch (e) { document.getElementById('glossary-terms').innerHTML = '<div class="empty-hint">加载失败</div>'; }
}
async function addGlossaryTerm() {
  if (!selectedModuleName) { toast('请先选择模块'); return; }
  const term = document.getElementById('new-term-input').value.trim();
  const def = document.getElementById('new-term-def').value.trim();
  if (!term) return;
  try {
    const r = await fetch('/api/modules/' + encodeURIComponent(selectedModuleName) + '/glossary', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ term, definition: def }) });
    const d = await r.json(); if (d.success) { document.getElementById('new-term-input').value = ''; document.getElementById('new-term-def').value = ''; loadGlossary(selectedModuleName); toast('✅ 术语已添加'); } else toast('❌ ' + d.message);
  } catch (e) { console.error(e); toast('❌ 添加失败'); }
}
async function deleteDocGlossaryTerm(termId) {
  if (!selectedDocId) return;
  try {
    const r = await fetch('/api/docs/' + encodeURIComponent(selectedDocId) + '/glossary/' + termId, { method: 'DELETE' });
    const d = await r.json();
    if (d.success) { loadDocGlossary(selectedDocId); toast('✅ 已删除'); } else toast('❌ ' + d.message);
  } catch (e) { console.error(e); toast('❌ 删除失败'); }
}

async function deleteGlossaryTerm(termId) {
  if (!selectedModuleName) return;
  try {
    // 需要 term 名称而非 ID，从 DOM 反查
    const termEl = document.querySelector('.js-delete-term[data-term-id="' + termId + '"]');
    const termName = termEl ? termEl.closest('.glossary-item').querySelector('.term-name').textContent.trim() : '';
    if (!termName) { toast('未找到术语名'); return; }
    const r = await fetch('/api/modules/' + encodeURIComponent(selectedModuleName) + '/glossary/' + encodeURIComponent(termName), { method: 'DELETE' });
    const d = await r.json(); if (d.success) { loadGlossary(selectedModuleName); toast('✅ 已删除'); } else toast('❌ ' + d.message);
  } catch (e) { console.error(e); toast('❌ 删除失败'); }
}

// ===== 聊天 & 测试生成 =====
let _chatRunning = false;
async function sendChat() {
  if (_chatRunning) return;
  const inp = document.getElementById('chat-input'); const userInput = inp.value.trim();
  if (!userInput) return;
  _chatRunning = true; inp.value = '';
  const box = document.getElementById('chat-box');
  box.innerHTML += '<div class="chat-msg"><div class="who user">👤 用户</div><div class="body">' + esc(userInput) + '</div></div>';
  box.scrollTop = box.scrollHeight;
  try {
    const fd = new FormData(); fd.append('user_input', userInput);
    const r = await fetch('/chat', { method: 'POST', body: fd }); const d = await r.json();
    if (d.success && d.task_id) {
      const msgDiv = document.createElement('div'); msgDiv.className = 'chat-msg';
      msgDiv.innerHTML = '<div class="who ai">🤖 AI</div><div class="body">⏳ 正在处理中...</div>';
      box.appendChild(msgDiv); box.scrollTop = box.scrollHeight;
      await pollTask(d.task_id,
        (p, m) => { msgDiv.querySelector('.body').textContent = '⏳ ' + m + ' (' + p + '%)'; },
        result => {
          if (result && !result.error) {
            let html = '<div class="chat-msg"><div class="who ai">🤖 AI</div><div class="body">' + escText(result.reply || result.summary || JSON.stringify(result)) + '</div></div>';
            if (result.excel_path) {
              html += '<div class="result-panel"><h4>📊 测试计划</h4><p>Excel: ' + escText(result.excel_path) + '</p>'
                + '<button class="btn btn-sm btn-success js-confirm-plan" data-path="' + esc(result.excel_path) + '">确认并生成 PY+YAML</button>'
                + excelActionButtons(result.excel_path, result.excel_name)
                + '</div>';
              resultFiles = [{ name: result.excel_name || 'test_plan.xlsx', path: result.excel_path }];
            }
            msgDiv.outerHTML = html;
          } else if (result && result.requires_review && result.excel_path) {
            let html = '<div class="chat-msg"><div class="who ai" style="color:#e37400">⚠️ 需人工审查</div><div class="body">' + escText(result.reply || '') + '</div></div>';
            html += '<div class="result-panel"><h4>📊 测试计划</h4><p>Excel: ' + escText(result.excel_path) + '</p>'
              + '<button class="btn btn-sm btn-success js-confirm-plan" data-path="' + esc(result.excel_path) + '">仍然生成 PY+YAML</button>'
              + excelActionButtons(result.excel_path, result.excel_name)
              + '</div>';
            msgDiv.outerHTML = html;
          } else {
            showChatError(result ? result.error || '未知错误' : '模型无响应');
            msgDiv.remove();
          }
        });
    } else { showChatError(d.message || '请求失败'); }
  } catch (e) { showChatError(e.message); } finally { _chatRunning = false; }
}
function showChatError(msg) {
  const box = document.getElementById('chat-box');
  box.innerHTML += '<div class="chat-msg"><div class="who ai" style="color:var(--danger)">❌ 错误</div><div class="body">' + esc(msg) + '</div></div>';
}

// ===== Phase C 工作流（多轮对话） =====
let _workflowSessionId = null;

async function sendWorkflowChat() {
  if (_chatRunning) return;
  const inp = document.getElementById('chat-input'); const userInput = inp.value.trim();
  if (!userInput) return;
  _chatRunning = true; inp.value = '';
  const box = document.getElementById('chat-box');
  box.innerHTML += '<div class="chat-msg"><div class="who user">👤 用户</div><div class="body">' + esc(userInput) + '</div></div>';
  box.scrollTop = box.scrollHeight;
  try {
    let endpoint, body;
    if (_workflowSessionId) {
      // 已有会话 → 确认模块
      endpoint = '/workflow/confirm';
      body = new FormData(); body.append('session_id', _workflowSessionId); body.append('choice', userInput);
    } else {
      // 新会话 → 启动工作流
      endpoint = '/workflow/start';
      body = new FormData(); body.append('user_input', userInput);
    }
    const r = await fetch(endpoint, { method: 'POST', body }); const d = await r.json();
    if (d.status === 'waiting' || d.status === 'reconfirm' || d.status === 'no_match') {
      // 需要用户确认
      _workflowSessionId = d.session_id;
      let html = '<div class="chat-msg"><div class="who ai">🤖 AI</div><div class="body">' + esc(d.question) + '</div></div>';
      if (d.candidates && d.candidates.length) {
        html += '<div class="chat-msg"><div class="body" style="display:flex;flex-wrap:wrap;gap:6px">';
        d.candidates.forEach((c, i) => {
          html += '<button class="btn btn-outline btn-sm" data-action="confirmWorkflowModule" data-session="' + esc(d.session_id) + '" data-module="' + esc(c) + '">' + (i + 1) + '. ' + esc(c) + '</button>';
        });
        html += '</div></div>';
      }
      box.innerHTML += html;
    } else if (d.success && d.task_id) {
      // 后台生成
      const msgDiv = document.createElement('div'); msgDiv.className = 'chat-msg';
      msgDiv.innerHTML = '<div class="who ai">🤖 AI</div><div class="body">⏳ 正在生成测试计划...</div>';
      box.appendChild(msgDiv); box.scrollTop = box.scrollHeight;
      await pollTask(d.task_id,
        (p, m) => { msgDiv.querySelector('.body').textContent = '⏳ ' + m + ' (' + p + '%)'; },
        result => {
          if (result && !result.error) {
            let html = '<div class="chat-msg"><div class="who ai">🤖 AI</div><div class="body">' + esc(result.reply || JSON.stringify(result)) + '</div></div>';
            if (result.excel_path) {
              html += '<div class="result-panel"><h4>📊 测试计划</h4><p>Excel: ' + escText(result.excel_path) + '</p>'
                + '<button class="btn btn-sm btn-success js-confirm-plan" data-path="' + esc(result.excel_path) + '">确认并生成 PY+YAML</button>'
                + excelActionButtons(result.excel_path, result.excel_name)
                + '</div>';
            }
            msgDiv.outerHTML = html;
          } else {
            showChatError(result ? result.error || '未知错误' : '任务失败');
            msgDiv.remove();
          }
          _workflowSessionId = null;
        });
    } else {
      showChatError(d.message || '请求失败');
    }
  } catch (e) { showChatError(e.message); } finally { _chatRunning = false; }
}

async function confirmWorkflowModule(sessionId, moduleName) {
  _workflowSessionId = sessionId;
  const box = document.getElementById('chat-box');
  box.innerHTML += '<div class="chat-msg"><div class="who user">👤 用户</div><div class="body">选择: ' + esc(moduleName) + '</div></div>';
  box.scrollTop = box.scrollHeight;
  try {
    const fd = new FormData(); fd.append('session_id', sessionId); fd.append('choice', moduleName);
    const r = await fetch('/workflow/confirm', { method: 'POST', body: fd }); const d = await r.json();
    if (d.status === 'reconfirm') {
      _workflowSessionId = d.session_id;
      box.innerHTML += '<div class="chat-msg"><div class="who ai">🤖 AI</div><div class="body">' + esc(d.question) + '</div></div>';
    } else if (d.success && d.task_id) {
      const msgDiv = document.createElement('div'); msgDiv.className = 'chat-msg';
      msgDiv.innerHTML = '<div class="who ai">🤖 AI</div><div class="body">⏳ 正在生成测试计划...</div>';
      box.appendChild(msgDiv); box.scrollTop = box.scrollHeight;
      await pollTask(d.task_id,
        (p, m) => { msgDiv.querySelector('.body').textContent = '⏳ ' + m + ' (' + p + '%)'; },
        result => {
          if (result && !result.error) {
            let html = '<div class="chat-msg"><div class="who ai">🤖 AI</div><div class="body">' + esc(result.reply || JSON.stringify(result)) + '</div></div>';
            if (result.excel_path) {
              html += '<div class="result-panel"><h4>📊 测试计划</h4><p>Excel: ' + escText(result.excel_path) + '</p>'
                + '<button class="btn btn-sm btn-success js-confirm-plan" data-path="' + esc(result.excel_path) + '">确认并生成 PY+YAML</button>'
                + excelActionButtons(result.excel_path, result.excel_name)
                + '</div>';
            }
            msgDiv.outerHTML = html;
          } else {
            showChatError(result ? result.error || '未知错误' : '任务失败');
            msgDiv.remove();
          }
          _workflowSessionId = null;
        });
    } else {
      showChatError(d.message || '请求失败');
    }
  } catch (e) { showChatError(e.message); }
}

// ---- 确认计划（PY+YAML 生成） ----
async function confirmPlan(excelPath) {
  const box = document.getElementById('chat-box');
  const msgDiv = document.createElement('div'); msgDiv.className = 'chat-msg';
  msgDiv.innerHTML = '<div class="who ai">🤖 AI</div><div class="body">⏳ 正在生成 PY+YAML...（0%）</div>';
  box.appendChild(msgDiv); box.scrollTop = box.scrollHeight;
  const fd = new FormData(); fd.append('excel_path', excelPath);
  try {
    const r = await fetch('/confirm-plan', { method: 'POST', body: fd }); const d = await r.json();
    if (d.success && d.task_id) {
      await pollTask(d.task_id,
        (p, m) => { msgDiv.querySelector('.body').textContent = '⏳ ' + m + '（' + p + '%）'; },
        result => {
          if (result && !result.error) {
            let html = '<div class="chat-msg"><div class="who ai">🤖 AI</div><div class="body">✅ ' + escText(result.message) + '</div></div>';
            if (result.py_path) {
              html += '<div class="result-panel"><h4>🐍 测试文件</h4><p>PY: ' + escText(result.py_path) + '</p>'
                + excelActionButtons(result.py_path, result.py_file) + '</div>';
            }
            if (result.excel_path) {
              html += '<div class="result-panel"><h4>📊 Excel 测试计划</h4><p>Excel: ' + escText(result.excel_path) + '</p>'
                + excelActionButtons(result.excel_path, 'test_plan.xlsx') + '</div>';
            }
            box.innerHTML += html; box.scrollTop = box.scrollHeight;
          } else {
            msgDiv.querySelector('.body').innerHTML = '<span style="color:var(--danger)">❌ ' + escText(result ? result.error : '生成失败') + '</span>';
          }
        });
    } else { msgDiv.querySelector('.body').innerHTML = '<span style="color:var(--danger)">❌ ' + escText(d.message || '提交失败') + '</span>'; }
  } catch (e) { msgDiv.querySelector('.body').innerHTML = '<span style="color:var(--danger)">❌ ' + escText(e.message) + '</span>'; }
}

// ---- 文件编辑器 ----
async function openFileEditor(path) {
  try {
    const r = await fetch('/api/files/file-content?path=' + encodeURIComponent(path)); const d = await r.json();
    if (!d.success) { toast('❌ ' + (d.message || '读取失败')); return; }
    if (d.binary) { toast('二进制文件无法编辑'); return; }
    editorPath = path; editorDirty = false;
    document.getElementById('file-editor-path').textContent = path;
    document.getElementById('file-editor-textarea').value = d.content || '';
    document.getElementById('file-editor').style.display = 'block';
  } catch (e) { console.error(e); toast('❌ 读取失败'); }
}
function closeFileEditor() { editorPath = ''; editorDirty = false; document.getElementById('file-editor').style.display = 'none'; }
async function saveFileEditor() {
  if (!editorPath) return;
  try {
    const content = document.getElementById('file-editor-textarea').value;
    const r = await fetch('/api/files/file-save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ path: editorPath, content }) });
    const d = await r.json(); if (d.success) { editorDirty = false; toast('✅ 已保存'); } else toast('❌ ' + d.message);
  } catch (e) { console.error(e); toast('❌ 保存失败'); }
}

// ===== 全局事件委托（data-action 按钮） =====
document.addEventListener('click', function(e) {
  const btn = e.target.closest('[data-action]');
  if (!btn) return;
  const action = btn.dataset.action;
  if (action === 'openLocalFile') openLocalFile(decodeURIComponent(btn.dataset.path || ''));
  else if (action === 'openFileEditor') openFileEditor(decodeURIComponent(btn.dataset.path || ''));
  else if (action === 'confirmWorkflowModule') confirmWorkflowModule(btn.dataset.session || '', btn.dataset.module || '');
});

// ===== 启动 =====
function init() {
  // 首次渲染：使用服务端直出的数据，避免空文件列表 + VECTOR_READY 未定义崩溃
  if (typeof INITIAL_FILES !== 'undefined' && INITIAL_FILES) {
    renderFileList(INITIAL_FILES);
  }
  if (typeof VECTOR_READY !== 'undefined') {
    setNavStatus(VECTOR_READY);
  } else {
    setNavStatus(false);
  }
  // 异步刷新文件列表（获取最新数据）
  refreshFileList();
  document.getElementById('chat-input').addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendWorkflowChat(); }
  });
  document.getElementById('file-filter-input').addEventListener('input', filterFileList);
}

document.addEventListener('DOMContentLoaded', init);
