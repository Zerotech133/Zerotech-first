import { Outlet } from 'react-router-dom';

/**
 * 指挥大屏态壳（spec §2.1）：全屏、无侧栏/无底栏。
 * 仅挂背景（网格 + 3 层柔光晕）+ <Outlet/>。
 */
export default function BigscreenLayout() {
  return (
    <>
      <div className="bg-grid" />
      <div className="bg-glow g1" />
      <div className="bg-glow g2" />
      <div className="bg-glow g3" />
      <div className="shell-bs">
        <Outlet />
      </div>
    </>
  );
}
