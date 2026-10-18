// 统一读取当前用户角色（localStorage zhice_role，登录时写入 / 登出清除）。
// VIEWER 只读：canWrite=false 用于隐藏写操作按钮；isViewer/isReadOnly 用于导航/数据裁剪。
// 多用户能力模型：USER 为 owner-scoped（canManageOwn=/my/* 写面），ADMIN 兼运营平台面
// （canManagePlatform 与 SystemGroup/UsersPage 的严格 ADMIN 判断同口径）。
// 后端为权威拦截（禁写中间件 + deny_viewer），前端仅隐藏入口改善体验。

export interface RoleInfo {
  role: string;
  isViewer: boolean;
  isUser: boolean;            // role === 'USER'
  canManageOwn: boolean;      // USER + ADMIN（/my/* 写面）
  canManagePlatform: boolean; // ADMIN
  canWrite: boolean;          // 保留原语义：非 VIEWER 即可写（现有调用方零改动）
  isReadOnly: boolean;        // = isViewer
}

export function getRoleCapabilities(): RoleInfo {
  const role = localStorage.getItem('zhice_role') || '';
  const isViewer = role === 'VIEWER';
  const isUser = role === 'USER';
  const isAdmin = role === 'ADMIN';
  return {
    role,
    isViewer,
    isUser,
    canManageOwn: isUser || isAdmin,
    canManagePlatform: isAdmin,
    canWrite: !isViewer,
    isReadOnly: isViewer,
  };
}

export function useRole(): RoleInfo {
  return getRoleCapabilities();
}
