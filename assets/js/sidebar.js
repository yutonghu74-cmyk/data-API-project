// 响应式侧边栏开关（小屏使用）
export function initSidebar() {
  const sidebar    = document.querySelector('.sidebar');
  const breadcrumb = document.querySelector('.breadcrumb');
  const toggle     = document.getElementById('sidebarToggle');
  if (!sidebar) return;

  // 同步读缓存用户，body 加 is-admin class，CSS 控制管理员链接可见性
  try {
    const cached = JSON.parse(localStorage.getItem('platform_user') || 'null');
    if (cached?.role === 'admin') document.body.classList.add('is-admin');
  } catch (_) {}

  // embed 模式（作为 iframe 嵌入时）跳过顶栏和面包屑注入
  const isEmbed = document.documentElement.classList.contains('embed-mode')
    || new URLSearchParams(location.search).get('embed') === '1';

  if (!isEmbed) {
    const topBar = document.createElement('div');
    topBar.className = 'top-bar';

    if (toggle) topBar.appendChild(toggle);

    const brandEl = breadcrumb?.querySelector('.brand');
    if (brandEl) topBar.appendChild(brandEl);

    const spacer = document.createElement('div');
    spacer.style.flex = '1';
    topBar.appendChild(spacer);

    const userBar = document.getElementById('userBar');
    if (userBar) topBar.appendChild(userBar);

    document.body.insertBefore(topBar, document.body.firstChild);

    breadcrumb?.querySelector('.sep')?.remove();
  }

  // 创建遮罩
  const overlay = document.createElement('div');
  overlay.className = 'sidebar-overlay';
  document.body.appendChild(overlay);

  function open()  { sidebar.classList.add('open'); overlay.classList.add('open'); }
  function close() { sidebar.classList.remove('open'); overlay.classList.remove('open'); }

  toggle?.addEventListener('click', () => sidebar.classList.contains('open') ? close() : open());
  overlay.addEventListener('click', close);

  // 点击侧边栏链接后自动关闭（手机端）
  sidebar.querySelectorAll('.sidebar-item').forEach(a =>
    a.addEventListener('click', () => { if (window.innerWidth <= 768) close(); })
  );

  // 全局：select 空选项时显示为占位样式（与 label 视觉对齐）
  function syncSelectPh(sel) {
    sel.dataset.ph = sel.value === '' ? '1' : '0';
  }
  document.querySelectorAll('select').forEach(sel => {
    syncSelectPh(sel);
    sel.addEventListener('change', () => syncSelectPh(sel));
  });
  // 动态插入的 select（如弹窗内）也能生效
  new MutationObserver(muts => {
    muts.forEach(m => m.addedNodes.forEach(n => {
      if (n.nodeType !== 1) return;
      (n.tagName === 'SELECT' ? [n] : [...n.querySelectorAll('select')])
        .forEach(sel => { syncSelectPh(sel); sel.addEventListener('change', () => syncSelectPh(sel)); });
    }));
  }).observe(document.body, { childList: true, subtree: true });
}
