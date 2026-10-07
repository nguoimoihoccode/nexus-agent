export function edgePath(
  from: HTMLElement | undefined,
  to: HTMLElement | undefined,
  canvas: HTMLElement | null,
  curve = 0,
): string {
  if (!from || !to || !canvas) return "";
  const box = canvas.getBoundingClientRect();
  const source = from.getBoundingClientRect();
  const target = to.getBoundingClientRect();
  const sx = source.left + source.width / 2 - box.left;
  const sy = source.top + source.height / 2 - box.top;
  const tx = target.left + target.width / 2 - box.left;
  const ty = target.top + target.height / 2 - box.top;
  const dx = tx - sx;
  const dy = ty - sy;
  const horizontal = Math.abs(dx) >= Math.abs(dy);
  const startX = sx + (horizontal ? Math.sign(dx) * source.width / 2 : 0);
  const startY = sy + (horizontal ? 0 : Math.sign(dy) * source.height / 2);
  const endX = tx - (horizontal ? Math.sign(dx) * target.width / 2 : 0);
  const endY = ty - (horizontal ? 0 : Math.sign(dy) * target.height / 2);
  if (curve) {
    const length = Math.hypot(endX - startX, endY - startY) || 1;
    const normalX = -(endY - startY) / length;
    const normalY = (endX - startX) / length;
    const middleX = (startX + endX) / 2 + normalX * curve;
    const middleY = (startY + endY) / 2 + normalY * curve;
    return `M ${startX} ${startY} Q ${middleX} ${middleY}, ${endX} ${endY}`;
  }
  const bend = horizontal ? Math.max(Math.abs(endX - startX) * 0.45, 28) : 0;
  return `M ${startX} ${startY} C ${startX + Math.sign(dx) * bend} ${startY}, ${endX - Math.sign(dx) * bend} ${endY}, ${endX} ${endY}`;
}
