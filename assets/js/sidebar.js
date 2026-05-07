// 响应式侧边栏开关（小屏使用）
export function initSidebar() {
  const sidebar  = document.querySelector('.sidebar');
  const toggle   = document.getElementById('sidebarToggle');
  const breadcrumb = document.querySelector('.breadcrumb');
  if (!sidebar) return;

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
}
