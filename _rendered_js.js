
    const INITIAL_FILES = [];
    const VECTOR_READY = false;
    let currentTab = 'upload';
    let selectedModuleId = null;
    let editorPath = null;
    let editorDirty = false;
    let resultFiles = [];  // 生成的文件列表 [{path, name, type}]

    // ==================== 导航 ====================
    function switchTab(name) {
        document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
        document.querySelectorAll('nav a').forEach(a => a.classList.remove('active'));
        document.getElementById('tab-' + name).classList.add('active');
        document.querySelector('nav a[data-tab="'+name+'"]').classList.add('active');
        currentTab = name;
        if (name === 'modules') refreshModuleTree();
        if (name === 'upload') refreshFileList();
    }

    // ==================== Toast ====================
    let _toastTimer;
    function toast(msg) {
        const el = document.getElementById('toast');
        el.textContent = msg; el.classList.add('show');
        clearTimeout(_toastTimer);
        _toastTimer = setTimeout(() => el.classList.remove('show'), 3000);
    }

    function escapeHtml(s) { return s ? String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;') : ''; }

    // ==================== 轮询 ====================
    async function pollTask(taskId, onProgress, onDone) {
        for (let i = 0; i < 120; i++) {
            await sleep(2000);
            try {
                const r = await fetch('/task/' + taskId);
                const d = await r.json();
                if (!d.success) { onDone({error: d.message}); return; }
                const t = d.task;
                if (onProgress) onProgress(t);
                if (t.status === 'completed') { onDone(t.result); return; }
                if (t.status === 'failed') { onDone({error: t.error || '未知错误'}); return; }
            } catch(e) { /* retry */ }
        }
        onDone({error: '任务超时'});
    }
    function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

    // ==================== 上传 ====================
    function handleDrop(e, type) {
        const files = e.dataTransfer.files;
        if (files.length) uploadWithFile(files[0], type);
    }

    function uploadFile(input, type) {
        if (input.files.length) uploadWithFile(input.files[0], type);
        input.value = '';
    }

    function uploadWithFile(file, type) {
        const formData = new FormData();
        formData.append('file', file);
        toast('⏳ 正在上传 ' + file.name + ' ...');
        fetch('/upload-file', { method: 'POST', body: formData })
        .then(r => r.json())
        .then(data => {
            if (data.success && data.task_id) {
                const tid = data.task_id;
                toast('📤 文件已接收，后台处理中...');
                pollTask(tid,
                    t => toast('⏳ ' + (t.message || '处理中...')),
                    result => {
                        if (result && !result.error) {
                            toast('✅ ' + (result.message || '完成'));
                            refreshFileList();
                            if (result.module_name) showView(result);
                        } else {
                            toast('❌ ' + (result ? result.error : '失败'));
                        }
                    }
                );
            } else {
                toast('❌ ' + (data.message || '上传失败'));
            }
        })
        .catch(e => toast('❌ ' + e.message));
    }

    // ==================== 文件列表 ====================
    async function refreshFileList() {
        try {
            const r = await fetch('/uploaded-files');
            const d = await r.json();
            renderFileList(d.files);
            document.getElementById('nav-status').textContent = d.vector_ready ? '✅ 已就绪' : '❌ 未上传';
        } catch(e) { console.error(e); }
    }

    function renderFileList(files) {
        const empty = document.getElementById('file-empty');
        const table = document.getElementById('file-table');
        const tbody = document.getElementById('file-tbody');
        document.getElementById('file-count').textContent = files.length;
        if (!files.length) { empty.classList.remove('hidden'); table.classList.add('hidden'); return; }
        empty.classList.add('hidden'); table.classList.remove('hidden');
        tbody.innerHTML = files.map(f => {
            const typeLabel = {axure:'🎨 Axure', pdf:'📄 PDF', md:'📝 MD', docx:'📘 Word'}[f.type] || f.type || '?';
            return `<tr>
                <td class="name">${escapeHtml(f.name)}</td>
                <td>${typeLabel}</td>
                <td>${escapeHtml(f.size)}</td>
                <td>${f.chunks}</td>
                <td>${escapeHtml(f.time)}</td>
                <td>
                    <button class="btn btn-sm btn-outline" onclick="viewFile('${escapeHtml(f.name)}')">🔍 查看</button>
                    <button class="btn btn-sm btn-danger-outline" style="margin-left:4px;" onclick="deleteFile('${escapeHtml(f.name)}')">🗑</button>
                </td>
            </tr>`;
        }).join('');
    }

    function viewFile(filename) {
        // 显示文件基本信息
        const f = INITIAL_FILES.find(x => x.name === filename) || {};
        let html = `<h3>📋 ${escapeHtml(filename)}</h3>`;
        html += `<table style="width:100%;font-size:13px;">`;
        html += `<tr><td style="padding:8px;color:var(--text-dim);">类型</td><td>${escapeHtml(f.type||'?')}</td></tr>`;
        html += `<tr><td style="padding:8px;color:var(--text-dim);">大小</td><td>${escapeHtml(f.size||'?')}</td></tr>`;
        html += `<tr><td style="padding:8px;color:var(--text-dim);">文本块</td><td>${f.chunks||'?'}</td></tr>`;
        html += `<tr><td style="padding:8px;color:var(--text-dim);">时间</td><td>${escapeHtml(f.time||'?')}</td></tr>`;
        html += `</table>`;
        html += `<div class="btn-row"><button class="btn" onclick="closeView()">关闭</button></div>`;
        showModal(html);
    }

    function showView(data) {
        let html = `<h3>📋 文档信息</h3>`;
        if (data.file) {
            html += `<p><strong>文件:</strong> ${escapeHtml(data.file.name)}</p>`;
        }
        if (data.module_name) html += `<p><strong>模块:</strong> ${escapeHtml(data.module_name)}</p>`;
        if (data.related_modules && data.related_modules.length) {
            html += `<p><strong>关联模块:</strong> ${data.related_modules.map(escapeHtml).join(', ')}</p>`;
        }
        html += `<div class="btn-row"><button class="btn" onclick="closeView()">关闭</button></div>`;
        showModal(html);
    }

    function showModal(html) {
        document.getElementById('view-content').innerHTML = html;
        document.getElementById('view-modal').classList.remove('hidden');
    }

    function closeView() {
        document.getElementById('view-modal').classList.add('hidden');
    }

    async function deleteFile(filename) {
        if (!confirm('确认删除 "' + filename + '" ？\n向量库数据也将一并删除。')) return;
        try {
            const fd = new FormData(); fd.append('filename', filename);
            const r = await fetch('/delete-file', { method: 'POST', body: fd });
            const d = await r.json();
            toast(d.success ? '✅ 已删除' : '❌ ' + d.message);
            if (d.success) refreshFileList();
        } catch(e) { toast('❌ ' + e.message); }
    }

    // ==================== 模块管理 ====================
    async function refreshModuleTree() {
        try {
            const r = await fetch('/api/modules');
            const d = await r.json();
            if (d.success) renderModuleTree(d.tree);
        } catch(e) { console.error(e); }
    }

    function renderModuleTree(tree) {
        const container = document.getElementById('module-tree');
        if (!tree || !tree.length) {
            container.innerHTML = '<div style="color:#aaa;padding:12px;">暂无模块</div>';
            return;
        }
        container.innerHTML = tree.map(n => renderTreeNode(n, 0)).join('');
    }

    function renderTreeNode(node, depth) {
        const indent = depth * 16;
        const hasChildren = node.children && node.children.length;
        const icon = depth === 0 ? '🏠' : (hasChildren ? '📂' : '📄');
        const selClass = node.id === selectedModuleId ? ' selected' : '';
        let html = `<div class="item${selClass}" style="padding-left:${indent+8}px;" onclick="selectModule('${node.id}','${escapeHtml(node.name)}')">
            <span>${icon}</span><span>${escapeHtml(node.name)}</span>
            ${node.id !== 'root' ? `<span style="margin-left:auto;display:inline-flex;gap:2px;">
                <button class="btn btn-sm btn-outline" style="padding:1px 6px;font-size:10px;" onclick="event.stopPropagation();renameModule('${node.id}','${escapeHtml(node.name)}')">✏️</button>
                <button class="btn btn-sm btn-danger-outline" style="padding:1px 6px;font-size:10px;" onclick="event.stopPropagation();deleteModule('${node.id}','${escapeHtml(node.name)}')">🗑</button>
            </span>` : ''}
        </div>`;
        if (hasChildren) {
            html += node.children.map(c => renderTreeNode(c, depth + 1)).join('');
        }
        return html;
    }

    function selectModule(id, name) {
        selectedModuleId = id;
        refreshModuleTree();
        // 显示详情
        const detail = document.getElementById('module-detail');
        detail.innerHTML = `<h3>📋 ${escapeHtml(name)}</h3>
            <div class="section"><h4>模块 ID</h4><code style="font-size:12px;">${escapeHtml(id)}</code></div>
            <div class="section">
                <h4>操作</h4>
                <button class="btn btn-sm" onclick="renameModule('${id}','${escapeHtml(name)}')">✏️ 重命名</button>
                <button class="btn btn-sm btn-outline" onclick="mergeModule('${id}','${escapeHtml(name)}')" style="margin-left:4px;">🔗 合并</button>
            </div>`;
    }

    async function createModule() {
        const input = document.getElementById('new-mod-name');
        const name = input.value.trim();
        if (!name) return;
        try {
            const r = await fetch('/api/modules', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({name, parent_id: selectedModuleId || 'root'}),
            });
            const d = await r.json();
            if (d.success) { input.value = ''; refreshModuleTree(); toast('✅ 已创建'); }
            else toast('❌ ' + d.message);
        } catch(e) { toast('❌ ' + e.message); }
    }

    async function renameModule(id, cur) {
        const name = prompt('新名称:', cur);
        if (!name || name === cur) return;
        try {
            const r = await fetch('/api/modules/' + id, {
                method:'PUT', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({name}),
            });
            const d = await r.json();
            if (d.success) { refreshModuleTree(); toast('✅ ' + d.message); }
            else toast('❌ ' + d.message);
        } catch(e) { toast('❌ ' + e.message); }
    }

    async function deleteModule(id, name) {
        if (!confirm('确定删除 "' + name + '" ？')) return;
        try {
            const r = await fetch('/api/modules/' + id, { method:'DELETE' });
            const d = await r.json();
            if (d.success) { if (selectedModuleId === id) selectedModuleId = null; refreshModuleTree(); toast('✅ 已删除'); }
            else toast('❌ ' + d.message);
        } catch(e) { toast('❌ ' + e.message); }
    }

    function mergeModule(id, name) {
        const target = prompt('合并到哪个模块？（输入目标模块名）:');
        if (!target) return;
        fetch('/api/modules/merge', {
            method:'POST', headers:{'Content-Type':'application/json'},
            body: JSON.stringify({source_id: id, target_id: target}),
        }).then(r => r.json()).then(d => {
            if (d.success) { refreshModuleTree(); toast('✅ 已合并'); }
            else toast('❌ ' + d.message);
        });
    }

    // ==================== 聊天 ====================
    async function sendChat() {
        const input = document.getElementById('chat-input');
        const box = document.getElementById('chat-box');
        const text = input.value.trim();
        if (!text) return;

        box.innerHTML += `<div class="chat-msg"><div class="who user">🧑 你</div><div class="body">${escapeHtml(text)}</div></div>`;
        input.value = ''; box.scrollTop = box.scrollHeight;

        document.getElementById('chat-btn').disabled = true;
        const progress = document.getElementById('chat-progress');
        const fill = document.getElementById('chat-fill');
        const ptxt = document.getElementById('chat-progress-text');
        progress.classList.remove('hidden'); fill.style.width = '0%'; ptxt.textContent = '提交中...';

        try {
            const fd = new FormData(); fd.append('user_input', text);
            const r = await fetch('/chat', { method: 'POST', body: fd });
            const d = await r.json();
            if (d.success && d.task_id) {
                pollTask(d.task_id,
                    t => { fill.style.width = (t.progress||0)+'%'; ptxt.textContent = t.message || '处理中...'; },
                    result => {
                        progress.classList.add('hidden');
                        document.getElementById('chat-btn').disabled = false;
                        if (result && !result.error) showChatResult(result);
                        else showChatError(result ? result.error : '未知错误');
                    }
                );
            } else {
                progress.classList.add('hidden');
                document.getElementById('chat-btn').disabled = false;
                showChatError(d.message || '请求失败');
            }
        } catch(e) {
            progress.classList.add('hidden');
            document.getElementById('chat-btn').disabled = false;
            showChatError(e.message);
        }
    }

    function showChatResult(result) {
        const box = document.getElementById('chat-box');
        let html = `<div class="chat-msg"><div class="who ai">🤖 AI</div><div class="body">`;
        if (result.requires_review) {
            html += `<div style="background:var(--danger-bg);border:1px solid var(--danger);border-radius:4px;padding:8px 12px;margin-bottom:8px;">
                <strong>⚠️ 需人工审查</strong><br>`;
            (result.error_info||[]).slice(0,6).forEach(e => html += '• ' + escapeHtml(e) + '<br>');
            html += `</div>`;
        }
        if (result.thinking) {
            html += `<details><summary>思考过程</summary>${escapeHtml(Array.isArray(result.thinking)?result.thinking.join('<br>'):result.thinking)}</details>`;
        }
        html += escapeHtml(result.reply || '') + `</div></div>`;
        box.innerHTML += html; box.scrollTop = box.scrollHeight;

        if (result.excel_path) {
            resultFiles = [];
            addResultFile(result.excel_path, '📊 测试计划');
            if (result.output_dir) loadOutputFiles(result.output_dir);
            document.getElementById('result-card').classList.remove('hidden');
            document.getElementById('file-panel').classList.remove('hidden');
            showPlanCard(result);
        }
    }

    function showChatError(msg) {
        const box = document.getElementById('chat-box');
        box.innerHTML += `<div class="chat-msg"><div class="who ai" style="color:var(--danger);">❌ 错误</div><div class="body">${escapeHtml(msg)}</div></div>`;
        box.scrollTop = box.scrollHeight;
    }

    function showPlanCard(result) {
        const content = document.getElementById('result-content');
        content.innerHTML = `
            <div style="background:var(--primary-bg);padding:12px;border-radius:6px;margin-bottom:12px;">
                <strong>📄 ${escapeHtml(result.excel_name || 'test_plan.xlsx')}</strong>
                <div style="font-size:12px;color:var(--text-dim);margin-top:4px;">${escapeHtml(result.excel_path)}</div>
            </div>
            <button class="btn btn-success" onclick="confirmPlan('${escapeHtml(result.excel_path||'')}')">✅ 确认并生成 PY + YAML</button>`;
    }

    function addResultFile(path, label) {
        if (!path || resultFiles.find(f => f.path === path)) return;
        const ext = (path||'').split('.').pop().toLowerCase();
        resultFiles.push({path, name: label || path.split(/[\\/]/).pop(), type: ext});
        renderFilePanel();
    }

    async function loadOutputFiles(dir) {
        // 简单列出 testcase_out 下的已知文件
        const known = ['test_plan.xlsx'];
        for (const name of known) {
            const p = dir.replace(/\\/g,'/') + '/' + name;
            if (resultFiles.find(f => f.path === p)) continue;
            resultFiles.push({path: p, name, type: 'xlsx'});
        }
        renderFilePanel();
    }

    function renderFilePanel() {
        const panel = document.getElementById('file-list-panel');
        if (!resultFiles.length) { panel.innerHTML = '<div style="color:var(--text-dim);">暂无文件</div>'; return; }
        panel.innerHTML = resultFiles.map(f => `
            <div class="file-row">
                <span>${f.name}</span>
                <div>
                    <button class="btn btn-sm btn-outline" onclick="editFile('${escapeHtml(f.path)}','${escapeHtml(f.name)}')">✏️ 编辑</button>
                    <button class="btn btn-sm btn-outline" onclick="openFileExternal('${escapeHtml(f.path)}')">🔗 打开</button>
                </div>
            </div>`).join('');
    }

    async function editFile(path, name) {
        document.getElementById('file-editor').classList.remove('hidden');
        document.getElementById('editor-title').textContent = '✏️ ' + name;
        document.getElementById('editor-status').textContent = '⏳ 加载中...';
        document.getElementById('editor-area').value = '';
        editorPath = path;
        editorDirty = false;

        try {
            const r = await fetch('/api/file-content?path=' + encodeURIComponent(path));
            const d = await r.json();
            if (d.success) {
                if (d.binary) {
                    document.getElementById('editor-area').value = '# 二进制文件，无法直接编辑。请使用"外部打开"。';
                    document.getElementById('editor-status').textContent = '⚠️ 二进制文件';
                } else {
                    document.getElementById('editor-area').value = d.content;
                    document.getElementById('editor-status').textContent = '✅ 已加载 | ' + (d.content||'').length + ' 字符';
                }
            } else {
                document.getElementById('editor-status').textContent = '❌ ' + d.message;
            }
        } catch(e) {
            document.getElementById('editor-status').textContent = '❌ ' + e.message;
        }
    }

    function editorChanged() { editorDirty = true; }

    async function saveEditor() {
        if (!editorPath) return;
        const content = document.getElementById('editor-area').value;
        try {
            const r = await fetch('/api/file-save', {
                method:'POST', headers:{'Content-Type':'application/json'},
                body: JSON.stringify({path: editorPath, content}),
            });
            const d = await r.json();
            if (d.success) {
                editorDirty = false;
                toast('✅ 已保存');
                document.getElementById('editor-status').textContent = '✅ 已保存';
            } else {
                toast('❌ ' + d.message);
            }
        } catch(e) { toast('❌ ' + e.message); }
    }

    async function revalidate() {
        if (!resultFiles.length) return;
        const excelFile = resultFiles.find(f => f.type === 'xlsx');
        if (!excelFile) { toast('⚠️ 未找到 Excel 文件'); return; }
        toast('⏳ 重新校验中...');
        try {
            const fd = new FormData(); fd.append('excel_path', excelFile.path);
            const r = await fetch('/confirm-plan', { method:'POST', body: fd });
            const d = await r.json();
            if (d.success && d.task_id) {
                pollTask(d.task_id,
                    null,
                    result => {
                        if (result && !result.error) {
                            toast('✅ 校验通过，文件已重新生成');
                            // 添加生成的文件
                            if (result.py_file) addResultFile(excelFile.path.replace('test_plan.xlsx', '') + result.py_file, '🐍 ' + result.py_file);
                            document.getElementById('editor-status').textContent = '✅ 校验通过';
                        } else {
                            toast('❌ 校验失败: ' + (result ? (result.error || '') : ''));
                            document.getElementById('editor-status').textContent = '❌ 校验失败，请修改后重试';
                        }
                    }
                );
            } else {
                toast('❌ ' + d.message);
            }
        } catch(e) { toast('❌ ' + e.message); }
    }

    function openFileExternal(path) {
        const p = path || editorPath;
        if (!p) return;
        const fd = new FormData(); fd.append('file_path', p);
        fetch('/open-file', { method:'POST', body: fd }).catch(() => {});
    }

    async function confirmPlan(excelPath) {
        toast('⏳ 正在生成测试文件...');
        try {
            const fd = new FormData();
            if (excelPath) fd.append('excel_path', excelPath);
            const r = await fetch('/confirm-plan', { method:'POST', body: fd });
            const d = await r.json();
            if (d.success && d.task_id) {
                pollTask(d.task_id,
                    t => toast(t.message || '生成中...'),
                    result => {
                        if (result && !result.error) {
                            toast('✅ ' + (result.message || '完成'));
                            if (result.py_file) addResultFile(result.py_file, '🐍 ' + result.py_file);
                            document.getElementById('result-content').innerHTML +=
                                '<div style="color:var(--success);margin-top:8px;">✅ ' + escapeHtml(result.message||'') + '</div>';
                        } else {
                            toast('❌ ' + (result ? (result.error || '失败') : '失败'));
                        }
                    }
                );
            } else if (!d.success) {
                toast('❌ ' + d.message);
            }
        } catch(e) { toast('❌ ' + e.message); }
    }

    // ==================== 初始化 ====================
    window.addEventListener('load', () => {
        if (INITIAL_FILES && INITIAL_FILES.length) renderFileList(INITIAL_FILES);
        else refreshFileList();
        document.getElementById('nav-status').textContent = VECTOR_READY ? '✅ 已就绪' : '❌ 未上传';
    });
