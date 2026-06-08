/** Voice Fab - 全站悬浮语音助手按钮
 * 轻触: 打开 Assist + 自动隐藏
 * 按住拖动: 移动位置
 * 长按不拖: 隐藏
 * 右上角小图标: 恢复显示
 */
(function() {
    'use strict';

    const STORAGE_KEY_ENABLED = 'vf_enabled';
    const STORAGE_KEY_POS = 'vf_pos';
    const DRAG_THRESHOLD = 8; // 超过8px才算拖动

    const STYLES = `
        .vf-wrap{position:fixed;z-index:999999;user-select:none;-webkit-user-select:none;touch-action:none;}
        .vf-btn{
            width:56px;height:56px;border-radius:50%;
            background:linear-gradient(135deg,#6366f1,#8b5cf6);
            color:#fff;border:none;
            box-shadow:0 4px 16px rgba(99,102,241,0.35);
            cursor:pointer;font-size:24px;
            display:flex;align-items:center;justify-content:center;
            transition:transform 0.2s,opacity 0.3s;
        }
        .vf-btn:active{transform:scale(0.92);}
        .vf-btn.dragging{opacity:0.8;transform:scale(1.05);transition:none;}
        .vf-btn.hiding{opacity:0;transform:scale(0.3) translateY(-40px);pointer-events:none;}
        .vf-btn.showing{animation:vf-pop-in 0.3s ease-out;}
        @keyframes vf-pop-in{0%{opacity:0;transform:scale(0.3) translateY(-20px);}100%{opacity:1;transform:scale(1) translateY(0);}}
        /* 右上角恢复小图标 */
        .vf-restore{
            position:fixed;top:60px;right:16px;z-index:999998;
            width:36px;height:36px;border-radius:50%;
            background:rgba(99,102,241,0.15);border:1.5px solid rgba(99,102,241,0.3);
            cursor:pointer;display:none;align-items:center;justify-content:center;
            font-size:16px;transition:all 0.3s;
        }
        .vf-restore.show{display:flex;}
        .vf-restore:hover{background:rgba(99,102,241,0.3);border-color:rgba(99,102,241,0.6);transform:scale(1.1);}
    `;

    let wrap, btn, restoreBtn;
    let isHidden = localStorage.getItem(STORAGE_KEY_ENABLED) === 'false';

    // 指针状态
    let pointerId = null;
    let startX, startY, wrapStartX, wrapStartY;
    let isDragging = false;
    let hasMoved = false;
    let pressTimer = null;

    function loadPos() {
        try { return JSON.parse(localStorage.getItem(STORAGE_KEY_POS) || '{}'); }
        catch { return {}; }
    }
    function savePos(x, y) { localStorage.setItem(STORAGE_KEY_POS, JSON.stringify({x, y})); }

    function getRootEl() {
        return document.querySelector('home-assistant');
    }

    function openAssistDialog() {
        const rootEl = getRootEl();
        if (!rootEl) return;
        rootEl.dispatchEvent(new CustomEvent('hass-action', {
            bubbles: true, composed: true,
            detail: {
                config: { tap_action: { action: 'assist', pipeline_id: 'last_used', start_listening: false } },
                action: 'tap'
            }
        }));
    }

    function hide(animate) {
        isHidden = true;
        localStorage.setItem(STORAGE_KEY_ENABLED, 'false');
        if (animate !== false) {
            btn.classList.add('hiding');
            setTimeout(() => { btn.style.display = 'none'; btn.classList.remove('hiding'); }, 300);
        } else {
            btn.style.display = 'none';
        }
        restoreBtn.classList.add('show');
    }

    function show() {
        isHidden = false;
        localStorage.setItem(STORAGE_KEY_ENABLED, 'true');
        btn.style.display = 'flex';
        btn.classList.add('showing');
        setTimeout(() => btn.classList.remove('showing'), 300);
        restoreBtn.classList.remove('show');
    }

    function onPointerDown(e) {
        if (e.button !== 0) return;
        e.preventDefault();
        
        pointerId = e.pointerId;
        btn.setPointerCapture(e.pointerId);
        
        startX = e.clientX;
        startY = e.clientY;
        const rect = wrap.getBoundingClientRect();
        wrapStartX = rect.left;
        wrapStartY = rect.top;
        isDragging = false;
        hasMoved = false;

        // 长按计时（不拖动则隐藏）
        pressTimer = setTimeout(() => {
            if (!hasMoved) {
                hide(true);
            }
        }, 800);
    }

    function onPointerMove(e) {
        if (e.pointerId !== pointerId) return;
        
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        const dist = Math.sqrt(dx * dx + dy * dy);

        if (dist > DRAG_THRESHOLD) {
            hasMoved = true;
            if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
            
            if (!isDragging) {
                isDragging = true;
                btn.classList.add('dragging');
                wrap.style.right = 'auto';
                wrap.style.bottom = 'auto';
                wrap.style.left = wrapStartX + 'px';
                wrap.style.top = wrapStartY + 'px';
            }
            
            wrap.style.left = (wrapStartX + dx) + 'px';
            wrap.style.top = (wrapStartY + dy) + 'px';
        }
    }

    function onPointerUp(e) {
        if (e.pointerId !== pointerId) return;
        
        if (pressTimer) { clearTimeout(pressTimer); pressTimer = null; }
        btn.classList.remove('dragging');

        if (isDragging) {
            // 拖动结束，保存位置
            const rect = wrap.getBoundingClientRect();
            savePos(rect.left, rect.top);
        } else if (!hasMoved && !isHidden) {
            // 轻触：打开 Assist + 自动隐藏
            openAssistDialog();
            setTimeout(() => hide(true), 300);
        }

        pointerId = null;
        isDragging = false;
        hasMoved = false;
    }

    function init() {
        if (document.getElementById('vf-wrap')) return;

        // 注入样式
        const s = document.createElement('style');
        s.textContent = STYLES;
        document.head.appendChild(s);

        // 创建按钮
        wrap = document.createElement('div');
        wrap.className = 'vf-wrap';
        wrap.id = 'vf-wrap';
        btn = document.createElement('button');
        btn.className = 'vf-btn';
        btn.id = 'vf-btn';
        btn.title = '轻触: 语音助手 | 按住拖动: 移动 | 长按: 隐藏';
        btn.innerHTML = '🎤';
        wrap.appendChild(btn);
        document.body.appendChild(wrap);

        // 创建恢复按钮
        restoreBtn = document.createElement('div');
        restoreBtn.className = 'vf-restore';
        restoreBtn.id = 'vf-restore';
        restoreBtn.title = '恢复语音助手按钮';
        restoreBtn.innerHTML = '🎤';
        document.body.appendChild(restoreBtn);

        // 恢复位置
        const pos = loadPos();
        if (pos.x !== undefined && pos.y !== undefined) {
            wrap.style.right = 'auto'; wrap.style.bottom = 'auto';
            wrap.style.left = pos.x + 'px'; wrap.style.top = pos.y + 'px';
        } else {
            wrap.style.right = '24px'; wrap.style.bottom = '24px';
        }

        // 初始隐藏状态
        if (isHidden) {
            btn.style.display = 'none';
            restoreBtn.classList.add('show');
        }

        // 指针事件（直接在按钮上，手机友好）
        btn.addEventListener('pointerdown', onPointerDown);
        btn.addEventListener('pointermove', onPointerMove);
        btn.addEventListener('pointerup', onPointerUp);
        btn.addEventListener('pointercancel', onPointerUp);

        // 恢复按钮
        restoreBtn.addEventListener('click', show);

        // 监听 Assist dialog 关闭事件，自动恢复按钮
        document.addEventListener('dialog-closed', (e) => {
            const detail = e.detail || {};
            if (detail.dialog === 'ha-voice-command-dialog' && isHidden) {
                show();
            }
        });

        // 全局事件
        window.addEventListener('vf-toggle', () => isHidden ? show() : hide(true));
        window.addEventListener('vf-show', show);
        window.addEventListener('vf-hide', () => hide(true));
    }

    // 等待 HA 加载
    if (document.readyState === 'complete') setTimeout(init, 800);
    else window.addEventListener('load', () => setTimeout(init, 800));
    setTimeout(init, 2000);
})();