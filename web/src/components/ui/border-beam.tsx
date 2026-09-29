"use client";

// Magic UI BorderBeam (magicui.design/r/border-beam): a light beam running along the border.
import { motion, type MotionStyle } from "motion/react";

import { cn } from "@/lib/utils";

interface BorderBeamProps {
  size?: number;
  duration?: number;
  colorFrom?: string;
  colorTo?: string;
  className?: string;
  borderWidth?: number;
}

export const BorderBeam = ({
  className,
  size = 60,
  duration = 5,
  colorFrom = "#ef4444",
  colorTo = "#f59e0b",
  borderWidth = 1.5,
}: BorderBeamProps) => (
  <div
    className="pointer-events-none absolute inset-0 rounded-[inherit] border-(length:--border-beam-width) border-transparent mask-[linear-gradient(transparent,transparent),linear-gradient(#000,#000)] mask-intersect [mask-clip:padding-box,border-box]"
    style={{ "--border-beam-width": `${borderWidth}px` } as React.CSSProperties}
  >
    <motion.div
      className={cn(
        "absolute aspect-square bg-linear-to-l from-(--color-from) via-(--color-to) to-transparent",
        className,
      )}
      style={
        {
          width: size,
          offsetPath: `rect(0 auto auto 0 round ${size}px)`,
          "--color-from": colorFrom,
          "--color-to": colorTo,
        } as MotionStyle
      }
      initial={{ offsetDistance: "0%" }}
      animate={{ offsetDistance: ["0%", "100%"] }}
      transition={{ repeat: Infinity, ease: "linear", duration }}
    />
  </div>
);
