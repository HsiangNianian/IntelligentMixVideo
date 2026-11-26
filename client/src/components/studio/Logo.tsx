/** 界面标志统一读取用户提供的圆形 Logo；侧栏、开场和空白页只通过尺寸类调整显示大小。 */

/** 标志图像：用主题混合模式隐藏原图的黑色圆底，仅显示 IMV 图形。 */
export function LogoMark({ className }: { className?: string }) {
  return <img src="/imv-logo.png" alt="" aria-hidden="true" draggable={false} className={`studio-logo-mark object-contain ${className ?? ""}`} />;
}
