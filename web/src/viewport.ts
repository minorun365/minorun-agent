// 入力欄を、キーボードや操作バーで隠れない範囲へ収める。
export function visibleHeight(layoutHeight: number, visualHeight?: number): number {
  return visualHeight !== undefined && Number.isFinite(visualHeight) && visualHeight > 0
    ? Math.min(layoutHeight, visualHeight)
    : layoutHeight;
}

// キーボードを閉じる途中の高さを、横向き画面と取り違えない。
// 入力開始前の高さを保持し、元の高さへの復帰か幅の変更で解除する。
export class LayoutViewport {
  private width = 0;
  private height = 0;
  private editing = false;

  begin(width: number, height: number): void {
    this.measure(width, height, false);
    this.editing = true;
  }

  measure(width: number, height: number, composing: boolean): number {
    if (width !== this.width) {
      this.width = width;
      this.height = height;
      this.editing = false;
    }
    if (this.editing && !composing && height >= this.height - 1) this.editing = false;
    if (!this.editing) this.height = height;
    return Math.max(this.height, height);
  }
}
