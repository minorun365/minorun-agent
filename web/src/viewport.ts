// 入力欄を、キーボードや操作バーで隠れない範囲へ収める。
export function visibleHeight(layoutHeight: number, visualHeight?: number): number {
  return visualHeight !== undefined && Number.isFinite(visualHeight) && visualHeight > 0
    ? Math.min(layoutHeight, visualHeight)
    : layoutHeight;
}
