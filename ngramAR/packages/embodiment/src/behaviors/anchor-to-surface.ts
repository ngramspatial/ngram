// @ts-nocheck
import type { SceneAnchor } from "@ngram-ar/core";
import type { Behavior, BehaviorContext, BehaviorOutput } from "./behavior.js";

const TABLE_SURFACE_OFFSET = 0.05;
const PREFERRED_TYPES = ["table", "desk"];

export class AnchorToSurfaceBehavior implements Behavior {
  readonly name = "anchor-to-surface";
  readonly priority = 20;

  private anchor: SceneAnchor | null = null;

  update(context: BehaviorContext): BehaviorOutput | null {
    if (!this.anchor) {
      this.anchor = this.pickBestAnchor(context);
    }
    if (!this.anchor) return null;

    const pos = this.anchor.transform.position;
    const isTableLike = PREFERRED_TYPES.includes(
      this.anchor.semanticType ?? "",
    );
    const yOffset = isTableLike ? TABLE_SURFACE_OFFSET : 0;

    return {
      moveTarget: {
        x: pos.x,
        y: pos.y + yOffset,
        z: pos.z,
      },
    };
  }

  reset(): void {
    this.anchor = null;
  }

  private pickBestAnchor(context: BehaviorContext): SceneAnchor | null {
    const { anchors } = context.scene;
    if (anchors.length === 0) return null;

    const preferred = anchors.find((a) =>
      PREFERRED_TYPES.includes(a.semanticType ?? ""),
    );
    if (preferred) return preferred;

    const floor = anchors.find((a) => a.semanticType === "floor");
    if (floor) return floor;

    return anchors[0];
  }
}
