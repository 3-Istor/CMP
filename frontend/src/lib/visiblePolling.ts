/**
 * Call `tick` every `intervalMs` while the tab is visible, and once as soon as
 * it shows again: a hidden tab costs the backend nothing. Returns the stop function.
 */
export function pollWhileVisible(tick: () => unknown, intervalMs: number): () => void {
  let timer: ReturnType<typeof setInterval> | undefined;
  const start = () => {
    timer ??= setInterval(tick, intervalMs);
  };
  const stop = () => {
    clearInterval(timer);
    timer = undefined;
  };
  const onVisibility = () => {
    if (document.visibilityState !== "visible") {
      stop();
    } else if (timer === undefined) {
      start();
      void tick();
    }
  };

  if (document.visibilityState === "visible") start();
  document.addEventListener("visibilitychange", onVisibility);
  return () => {
    document.removeEventListener("visibilitychange", onVisibility);
    stop();
  };
}
