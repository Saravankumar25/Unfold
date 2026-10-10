"use client";

import { useEffect, useState } from "react";

/** True once `active` has stayed true for `ms`: the cue for a "waking up" note on a cold-started service. */
export function useSlow(active: boolean, ms = 4000): boolean {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!active) return;
    const timer = setTimeout(() => setSlow(true), ms);
    return () => {
      clearTimeout(timer);
      setSlow(false);
    };
  }, [active, ms]);
  return active && slow;
}
